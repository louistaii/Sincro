"""Greedy feasible-always constructor.

Week-by-week placement under the CONSERVATIVE reading of the buffer rule:
two activities in different possessions (co_share_groups) in the same week
may not have overlapping closure footprints anywhere. If a schedule survives
this reading it also survives the permissive (per-night) reading, so the
score it reaches is a safe upper bound on the achievable optimum.

Constraints enforced:
  R1 workload conservation      R2 planned start week
  R3 finish-to-start precedence R4 closures + buffers + mirroring
  R5 legal mix (PM alone, <=1 PC + <=3 C, <=4 per possession per location)
  R6 co-sharing exemption       R7 weekly allocation  R8 workfronts
"""

from __future__ import annotations

import sys
from collections import defaultdict

from .analyse import CONTRACT_WEIGHT, ACTIVITY_NUDGE
from .instance import Instance, load_instance

MAX_PER_POSSESSION = 4


class Week:
    """Possessions placed in a single week."""

    def __init__(self, inst: Instance, week: int) -> None:
        self.inst = inst
        self.week = week
        self.groups: list[list[str]] = []          # group index -> activity ids
        self.group_of: dict[str, int] = {}
        self.nights: dict[tuple[str, str], dict[int, list[str]]] = defaultdict(dict)

    # ---- feasibility of adding `aid` to group `g` (g == -1 means new) ----

    def _mix_ok(self, aid: str, g: int) -> bool:
        if g == -1:
            return True
        a = self.inst.activities[aid]
        kind = self.inst.contracts[a.contract_number].access_type
        members = self.groups[g]
        kinds = [self.inst.contracts[self.inst.activities[m].contract_number].access_type
                 for m in members]
        if kind == "PM" or "PM" in kinds:
            return False
        if kind == "PC" and "PC" in kinds:
            return False
        span = set(self.inst.span_locations(a))
        for loc in span:
            here = sum(1 for m in members if loc in set(self.inst.span_locations(
                self.inst.activities[m])))
            if here + 1 > MAX_PER_POSSESSION:
                return False
        return True

    def _buffers_ok(self, aid: str, g: int) -> bool:
        foot = self.inst.closure_footprint(self.inst.activities[aid])
        for gi, members in enumerate(self.groups):
            if gi == g:
                continue
            for m in members:
                if foot & self.inst.closure_footprint(self.inst.activities[m]):
                    return False
        return True

    def _capacity_ok(self, aid: str, g: int) -> bool:
        span = set(self.inst.span_locations(self.inst.activities[aid]))
        for loc in span:
            groups_here = {gi for gi, ms in enumerate(self.groups)
                           if any(loc in set(self.inst.span_locations(self.inst.activities[m]))
                                  for m in ms)}
            if g == -1 or g not in groups_here:
                groups_here = groups_here | {g if g != -1 else len(self.groups)}
            if len(groups_here) > self.inst.supply[loc]:
                return False
        return True

    def _night_ok(self, aid: str) -> int | None:
        """Pick an access_night index respecting R7 (weekly allocation) and
        R8 (workfronts). Returns the night, or None if the contract+type is
        already saturated this week."""
        a = self.inst.activities[aid]
        c = self.inst.contracts[a.contract_number]
        key = (a.contract_number, a.activity_type)
        used = self.nights[key]
        for n in range(1, c.max_access_per_week + 1):
            if len(used.get(n, [])) < c.number_of_workfronts:
                return n
        return None

    def try_place(self, aid: str) -> int | None:
        night = self._night_ok(aid)
        if night is None:
            return None
        for g in list(range(len(self.groups))) + [-1]:
            if self._mix_ok(aid, g) and self._buffers_ok(aid, g) and self._capacity_ok(aid, g):
                if g == -1:
                    self.groups.append([aid])
                    g = len(self.groups) - 1
                else:
                    self.groups[g].append(aid)
                self.group_of[aid] = g
                a = self.inst.activities[aid]
                self.nights[(a.contract_number, a.activity_type)].setdefault(night, []).append(aid)
                return night
        return None


def construct(inst: Instance, allow_eclo: bool = False) -> tuple[dict, list]:
    H = inst.horizon_weeks
    remaining = {aid: float(a.total_accesses) for aid, a in inst.activities.items()}
    finish_week: dict[str, int] = {}
    placements: list[tuple[str, int, int, int, int]] = []  # aid, seq, week, eclo, night
    group_of: dict[tuple[str, int], int] = {}
    seq = defaultdict(int)

    def earliest(aid: str) -> int:
        a = inst.activities[aid]
        w = inst.week_of(a.planned_start_date)
        if a.predecessor_activity_id:
            pf = finish_week.get(a.predecessor_activity_id)
            if pf is None:
                return H + 1
            w = max(w, pf + 1)
        return w

    def urgency(aid: str) -> tuple:
        a = inst.activities[aid]
        c = inst.contracts[a.contract_number]
        deadline = inst.week_of(c.planned_completion_date)
        slack = deadline - (week + remaining[aid]) + 1
        return (slack, -CONTRACT_WEIGHT[c.contract_priority],
                -ACTIVITY_NUDGE[a.activity_priority], aid)

    for week in range(1, H + 1):
        wk = Week(inst, week)
        ready = [aid for aid in inst.activities
                 if remaining[aid] > 0 and earliest(aid) <= week]
        for aid in sorted(ready, key=urgency):
            night = wk.try_place(aid)
            if night is None:
                continue
            eclo = 0
            if allow_eclo and remaining[aid] > 1:
                eclo = 1
            gain = 1.5 if eclo else 1.0
            seq[aid] += 1
            group_of[(aid, week)] = wk.group_of[aid]
            placements.append((aid, seq[aid], week, eclo, night))
            remaining[aid] = max(0.0, remaining[aid] - gain)
            if remaining[aid] == 0:
                finish_week[aid] = week

    unfinished = {aid: r for aid, r in remaining.items() if r > 0}
    return {"placements": placements, "finish_week": finish_week,
            "group_of": group_of, "unfinished": unfinished}, sorted(unfinished)


def score(inst: Instance, finish_week: dict[str, int]) -> dict:
    tier_days = defaultdict(int)
    total = 0.0
    rows = []
    for aid, fin in finish_week.items():
        a = inst.activities[aid]
        c = inst.contracts[a.contract_number]
        days = max(0, (inst.week_end(fin) - c.planned_completion_date).days)
        if days:
            w = CONTRACT_WEIGHT[c.contract_priority] * (1 + ACTIVITY_NUDGE[a.activity_priority])
            total += w * days
            tier_days[c.contract_priority] += days
            rows.append((aid, a.contract_number, c.contract_priority, a.activity_priority,
                         days, round(w * days, 1)))
    return {"priority_weighted_score": round(total, 1),
            "priority_overrun": dict(sorted(tier_days.items())),
            "rows": sorted(rows, key=lambda r: -r[5])}


def main(data_dir: str = "01_data") -> None:
    inst = load_instance(data_dir)
    res, unfinished = construct(inst, allow_eclo=False)
    sc = score(inst, res["finish_week"])

    print("=" * 78)
    print("GREEDY CONSTRUCTOR  (Scenario A rules, conservative buffer reading)")
    print("=" * 78)
    print(f"activities scheduled   : {len(res['finish_week'])}/{len(inst.activities)}")
    print(f"access-nights placed   : {len(res['placements'])}")
    print(f"unfinished (R1 breach) : {unfinished if unfinished else 'none'}")
    print(f"\npriority_weighted_score : {sc['priority_weighted_score']:,.1f}")
    print(f"priority_overrun        : {sc['priority_overrun']}")
    print("\noverrunning activities (aid, contract, Ctier, Aprio, days, cost):")
    for r in sc["rows"]:
        print(f"   {r[0]}  {r[1]}  P{r[2]}  a{r[3]}  {r[4]:3d}d  {r[5]:>9,.1f}")

    last = max(w for _, _, w, _, _ in res["placements"])
    print(f"\nlast week used: {last}/{inst.horizon_weeks}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "01_data")
