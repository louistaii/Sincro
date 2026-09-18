"""
Priority ranking for activities.

Combines several signals into one "urgency score" per activity:
  - stuff on the activity itself (its own priority field, how long the work takes)
  - stuff on its contract (contract priority, deadline, how many accesses/week allowed)
  - stuff on its locations (how scarce the track capacity is, whether it's a shared sector)
  - stuff derived from OTHER activities (how many activities are stuck waiting on this one)

HOW TO TUNE IT
--------------
Just edit the numbers in WEIGHTS below.
  - Bigger number = that factor matters more.
  - 0 = ignore that factor completely.
  - Weights don't need to add up to anything in particular - they're just
    relative to each other.

Every factor is squashed to a 0-1 range before its weight is applied, so a
weight of "3.0" always means the same thing regardless of the factor's
original units (days, counts, capacity, whatever).
"""

from __future__ import annotations

from pathlib import Path

from extract import load_all, Activity, Contract, LocationSupply, BufferLocation


WEIGHTS = {
    # --- from the activity itself ---
    "activity_priority": 3.0,   # its own priority field in 08_ACTIVITY_DETAILS (lower number = more urgent)
    "duration_needed":   1.5,   # total_accesses / max_access_per_week -> how many weeks the work will take

    # --- from its contract (07_PROJECT_DETAILS) ---
    "contract_priority": 2.0,   # priority of the contract it belongs to
    "deadline_urgency":  2.5,   # how soon the contract's planned completion date is
    "schedule_slack":    2.0,   # days between planned_start_date and the deadline (little slack = urgent)

    # --- from where the work happens (03_SECTORS, 04_LOCATION_SUPPLY, 05_BUFFER_LOCATION) ---
    "disruption_size":     1.0,  # how big a closure footprint its nature-of-work needs
    "location_scarcity":   1.5,  # how little spare track capacity its start/end locations have
    "shared_sector_bonus": 1.0,  # touches a sector shared between both lines

    # --- from other activities (08_ACTIVITY_DETAILS, via predecessor links) ---
    "dependents_count": 2.0,   # how many other activities can't start until this one finishes
}


# --------------------------------------------------------------------------
# 2. Raw numbers per activity, pulled from every related file.
#    "higher raw value = more urgent" is noted per factor - some need
#    flipping later (e.g. lower activity_priority number = MORE urgent).
# --------------------------------------------------------------------------

def _sector_id_of(location_id: str) -> str:
    """'SEC:BET:S15_S16:EB' -> 'SEC:BET:S15_S16' (strips the trailing bound)."""
    return location_id.rsplit(":", 1)[0]


def compute_raw_factors(data: dict) -> dict[str, dict[str, float]]:
    activities: list[Activity] = data["activities"]
    contracts: dict[str, Contract] = {c.contract_number: c for c in data["contracts"]}
    sectors_by_id = {s.sector_id: s for s in data["sectors"]}
    supply_by_location = {ls.location_id: ls.supply_capacity for ls in data["location_supply"]}
    buffer_by_nature = {b.nature_of_works: b for b in data["buffer_locations"]}
    horizon_start = next(p.value for p in data["parameters"] if p.key == "horizon_start")
    import datetime as dt
    horizon_start = dt.date.fromisoformat(horizon_start)

    # how many activities list each activity_id as their predecessor
    dependents_count: dict[str, int] = {a.activity_id: 0 for a in activities}
    for a in activities:
        if a.predecessor_activity_id:
            dependents_count[a.predecessor_activity_id] = dependents_count.get(a.predecessor_activity_id, 0) + 1

    raw: dict[str, dict[str, float]] = {}
    for a in activities:
        contract = contracts[a.contract_number]
        buffer = buffer_by_nature.get(contract.nature_of_activity)

        start_sector_id = _sector_id_of(a.start_location_id)
        end_sector_id = _sector_id_of(a.end_location_id)
        start_sector = sectors_by_id.get(start_sector_id)
        end_sector = sectors_by_id.get(end_sector_id)

        start_capacity = supply_by_location.get(a.start_location_id)
        end_capacity = supply_by_location.get(a.end_location_id)
        capacities = [c for c in (start_capacity, end_capacity) if c is not None]

        raw[a.activity_id] = {
            # lower number = more urgent -> will be inverted in step 3
            "activity_priority": a.activity_priority,
            "contract_priority": contract.contract_priority,
            "deadline_urgency": (contract.planned_completion_date - horizon_start).days,
            "schedule_slack": (contract.planned_completion_date - a.planned_start_date).days,
            "location_scarcity": min(capacities) if capacities else 0,

            # higher number = more urgent -> used as-is in step 3
            "duration_needed": a.total_accesses / contract.number_of_maximum_access_per_week,
            "disruption_size": (
                (buffer.up_to_buffer_sectors + (2 if buffer.opposite_bound_required else 0))
                if buffer else 0
            ),
            "shared_sector_bonus": 1 if (
                (start_sector and start_sector.is_shared) or (end_sector and end_sector.is_shared)
            ) else 0,
            "dependents_count": dependents_count[a.activity_id],
        }
    return raw


# Which factors need flipping because a SMALLER raw number means MORE urgent.
INVERT = {
    "activity_priority",
    "contract_priority",
    "deadline_urgency",
    "schedule_slack",
    "location_scarcity",
}


# --------------------------------------------------------------------------
# 3. Squash every factor to 0-1, flip the ones that need it, apply weights.
# --------------------------------------------------------------------------

def _normalize(values: dict[str, float]) -> dict[str, float]:
    """Min-max scale a {activity_id: raw_value} dict to 0-1."""
    lo, hi = min(values.values()), max(values.values())
    if hi == lo:
        return {k: 0.5 for k in values}  # everyone's tied - no signal either way
    return {k: (v - lo) / (hi - lo) for k, v in values.items()}


def score_activities(data: dict, weights: dict[str, float] = WEIGHTS) -> list[dict]:
    """Returns a list of {activity_id, score, breakdown}, sorted most-urgent first."""
    raw = compute_raw_factors(data)
    factor_names = next(iter(raw.values())).keys()

    # normalize each factor across ALL activities (column by column)
    normalized: dict[str, dict[str, float]] = {}
    for factor in factor_names:
        column = {aid: raw[aid][factor] for aid in raw}
        scaled = _normalize(column)
        if factor in INVERT:
            scaled = {aid: 1 - v for aid, v in scaled.items()}
        normalized[factor] = scaled

    results = []
    for aid in raw:
        breakdown = {factor: normalized[factor][aid] * weights.get(factor, 0) for factor in factor_names}
        results.append({
            "activity_id": aid,
            "score": sum(breakdown.values()),
            "breakdown": breakdown,
        })

    results.sort(key=lambda r: r["score"], reverse=True)
    return results


# --------------------------------------------------------------------------
# 4. Run it
# --------------------------------------------------------------------------

if __name__ == "__main__":
    script_dir = Path(__file__).parent.parent.parent
    folder_path = script_dir / "01_data"   # <- adjust to wherever your CSVs live

    data = load_all(folder_path)
    ranking = score_activities(data)

    print(f"{'rank':>4}  {'activity':<10} {'score':>6}")
    for rank, r in enumerate(ranking, start=1):
        print(f"{rank:>4}  {r['activity_id']:<10} {r['score']:>6.2f}")