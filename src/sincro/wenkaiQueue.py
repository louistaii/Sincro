"""
Turns the priority scores from priority.py into an actual night-by-night
schedule, respecting two per-contract caps:

  - NIGHTLY cap  (contract.number_of_workfronts)
        How many crews/workfronts that contract can send out on any single
        night. Reset back to zero at the start of every night.

  - WEEKLY cap   (contract.number_of_maximum_access_per_week)
        How many nights per week that contract is allowed onto the track
        at all. Reset back to zero every 7 nights.
        ASSUMPTION: this counts NIGHTS the contract was granted access, not
        individual activities. So if a contract runs 2 workfronts on the
        same night, that's still only 1 night of "weekly access" used.
        (If your data actually means something else by this field, tell me
        and I'll change how it's counted.)

HOW ONE NIGHT WORKS
--------------------
1. Everything currently waiting (the priority queue + anything bounced
   back from last night) gets drained and attempted, highest score first.
2. For each activity popped:
     - if its contract has already used up this week's access allowance
       -> WEEKLY_BLOCKED bucket (held until the week rolls over)
     - elif its contract has already used up tonight's workfront slots
       -> NIGHT_BUFFER (retried next night)
     - else -> scheduled tonight; both counters for that contract go up
3. Once the queue is empty, the night is over. NIGHT_BUFFER gets poured
   back into the priority queue for the next night. Move on.
4. Every 7 nights, the week rolls over: weekly counters reset to zero and
   WEEKLY_BLOCKED gets poured back into the priority queue too.

Each activity is scheduled AT MOST ONCE - one successful night fully
completes it (per your confirmation), so it never goes back into the
queue once scheduled.
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass, field
from pathlib import Path

from extract import load_all, Activity, Contract
from priority import score_activities


NIGHTS_PER_WEEK = 7


@dataclass(order=True)
class _QueueItem:
    """Wraps an activity so heapq can order by score without comparing
    the Activity object itself (which has no meaningful ordering)."""
    sort_key: float                    # negative score -> heapq pops highest score first
    activity_id: str
    activity: Activity = field(compare=False)
    contract_number: str = field(compare=False)


def build_priority_queue(data: dict) -> list[_QueueItem]:
    """Scores every activity and returns a ready-to-use heap (a plain list -
    use heapq.heappush/heappop on it)."""
    activities_by_id = {a.activity_id: a for a in data["activities"]}
    ranking = score_activities(data)

    heap: list[_QueueItem] = []
    for r in ranking:
        activity = activities_by_id[r["activity_id"]]
        item = _QueueItem(
            sort_key=-r["score"],          # negative so the heap is a max-heap on score
            activity_id=activity.activity_id,
            activity=activity,
            contract_number=activity.contract_number,
        )
        heapq.heappush(heap, item)
    return heap


def run_schedule(data: dict) -> dict:
    """
    Simulates the whole thing night by night until every activity is
    either scheduled or ends up permanently stuck (see 'unscheduled' below).

    Returns:
        {
          "schedule": [ {night, week, activity_id, contract_number}, ... ],
          "nights_used": int,
        }
    """
    contracts = {c.contract_number: c for c in data["contracts"]}

    pq = build_priority_queue(data)
    night_buffer: list[_QueueItem] = []      # bounced for tonight's workfront cap, retry tomorrow
    weekly_blocked: list[_QueueItem] = []    # bounced for this week's access cap, retry next week

    schedule: list[dict] = []
    night = 0

    while pq or night_buffer or weekly_blocked:
        night += 1
        week = (night - 1) // NIGHTS_PER_WEEK + 1
        is_first_night_of_week = (night - 1) % NIGHTS_PER_WEEK == 0

        # start of a new week -> weekly caps reset, weekly-blocked items get another shot
        if is_first_night_of_week:
            for item in weekly_blocked:
                heapq.heappush(pq, item)
            weekly_blocked = []

        # start of a new night -> anything bounced last night for hitting the
        # nightly workfront cap gets another shot tonight
        for item in night_buffer:
            heapq.heappush(pq, item)
        night_buffer = []

        night_workfront_count: dict[str, int] = {}   # resets every night
        weekly_access_used: dict[str, int] = _weekly_access_used_so_far(schedule, week)

        # drain the whole queue once - that's what makes this "one night"
        while pq:
            item = heapq.heappop(pq)
            contract_number = item.contract_number
            contract = contracts[contract_number]

            used_this_week = weekly_access_used.get(contract_number, 0)
            already_accessing_tonight = night_workfront_count.get(contract_number, 0) > 0

            # weekly cap check (only matters if tonight would be a NEW access night)
            if not already_accessing_tonight and used_this_week >= contract.number_of_maximum_access_per_week:
                weekly_blocked.append(item)
                continue

            # nightly workfront cap check
            if night_workfront_count.get(contract_number, 0) >= contract.number_of_workfronts:
                night_buffer.append(item)
                continue

            # scheduled!
            night_workfront_count[contract_number] = night_workfront_count.get(contract_number, 0) + 1
            if not already_accessing_tonight:
                weekly_access_used[contract_number] = used_this_week + 1
            schedule.append({
                "night": night,
                "week": week,
                "activity_id": item.activity_id,
                "contract_number": contract_number,
            })

        # safety valve: guards against a contract with 0 workfronts or 0
        # weekly access (misconfigured data) looping forever with nothing
        # ever able to schedule.
        if night > NIGHTS_PER_WEEK * 1000:
            break

    return {"schedule": schedule, "nights_used": night}


def _weekly_access_used_so_far(schedule: list[dict], week: int) -> dict[str, int]:
    """Rebuilds this week's per-contract access-night count from the
    schedule so far, in case we need it (e.g. resuming mid-week)."""
    nights_by_contract: dict[str, set[int]] = {}
    for row in schedule:
        if row["week"] == week:
            nights_by_contract.setdefault(row["contract_number"], set()).add(row["night"])
    return {c: len(nights) for c, nights in nights_by_contract.items()}


# --------------------------------------------------------------------------
# Run it
# --------------------------------------------------------------------------

if __name__ == "__main__":
    script_dir = Path(__file__).parent.parent.parent
    folder_path = script_dir / "01_data"   # <- adjust to wherever your CSVs live

    data = load_all(folder_path)
    result = run_schedule(data)

    print(f"Scheduled {len(result['schedule'])} activities over {result['nights_used']} nights\n")
    for row in result["schedule"]:
        print(f"night {row['night']:>3} (week {row['week']:>2})  "
              f"{row['activity_id']:<8} contract {row['contract_number']}")