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

import datetime as dt
import math
from dataclasses import dataclass
from pathlib import Path

from .extract import Activity, Contract, load_all
from .instance import Instance
from .rules import ACTIVITY_NUDGE, CONTRACT_WEIGHT


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

# A mirrored closure reaches roughly a sector either side on top of its own
# buffer; this only has to rank activities against each other, not measure them.
MIRROR_REACH_BONUS = 2


def _sector_of(location_id: str, sectors_by_id: dict):
    """The sector a location books, or None if it is a platform.

    Matched against the sector ids the instance declares, so no assumption is
    made about how location ids are spelled or how many components they have.
    """
    for sid in sorted(sectors_by_id, key=len, reverse=True):
        if location_id == sid or (
            location_id.startswith(sid) and not location_id[len(sid)].isalnum()
        ):
            return sectors_by_id[sid]
    return None


def _hub_station_ids(stations) -> set[str]:
    return {s.station_id for s in stations if s.is_interchange}


def _is_shared(sector, hubs: set[str]) -> bool:
    """Flagged shared, or running between two interchange stations."""
    if sector is None:
        return False
    return bool(sector.is_shared) or {sector.from_station_id, sector.to_station_id} <= hubs


def compute_raw_factors(data: dict) -> dict[str, dict[str, float]]:
    activities: list[Activity] = data["activities"]
    contracts: dict[str, Contract] = {c.contract_number: c for c in data["contracts"]}
    sectors_by_id = {s.sector_id: s for s in data["sectors"]}
    hubs = _hub_station_ids(data["stations"])
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

        start_sector = _sector_of(a.start_location_id, sectors_by_id)
        end_sector = _sector_of(a.end_location_id, sectors_by_id)

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
                (buffer.up_to_buffer_sectors
                 + (MIRROR_REACH_BONUS if buffer.opposite_bound_required else 0))
                if buffer else 0
            ),
            "shared_sector_bonus": 1 if (
                _is_shared(start_sector, hubs) or _is_shared(end_sector, hubs)
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

def legacy_main() -> None:
    import sys

    default = Path(__file__).resolve().parents[2] / "01_data"
    folder_path = Path(sys.argv[1]) if len(sys.argv) > 1 else default

    data = load_all(folder_path)
    ranking = score_activities(data)

    print(f"{'rank':>4}  {'activity':<10} {'score':>6}")
    for rank, r in enumerate(ranking, start=1):
        print(f"{rank:>4}  {r['activity_id']:<10} {r['score']:>6.2f}")


# --------------------------------------------------------------------------
# Scenario-aware, penalty-calibrated ranking used by the schedulers.
# --------------------------------------------------------------------------

ACCESS_MULTIPLIER = {"PM": 1.35, "PC": 1.20, "C": 1.00}
ECLO_COST = 5.0


@dataclass(frozen=True)
class PriorityBreakdown:
    activity_id: str
    contract_number: str
    scenario: str
    as_of_date: dt.date
    due_date: dt.date
    days_to_due: int
    remaining_accesses: float
    minimum_duration_weeks: int
    slack_days: int
    runway_ratio: float
    contract_weight: float
    activity_multiplier: float
    weekly_delay_penalty: float
    nature_multiplier: float
    access_multiplier: float
    closure_pressure: float
    restriction_multiplier: float
    score: float


def _minimum_duration_weeks(
    remaining_accesses: float,
    scenario: str,
    remaining_eclo: int | None,
) -> int:
    if remaining_accesses <= 0:
        return 0
    if scenario == "A":
        return math.ceil(remaining_accesses)
    cap = math.inf if scenario == "B" else max(0, remaining_eclo or 0)
    half_units = round(2 * remaining_accesses)
    best = math.ceil(remaining_accesses)
    for eclo in range(0, half_units // 3 + 1):
        normal_units = half_units - 3 * eclo
        if eclo <= cap and normal_units >= 0 and normal_units % 2 == 0:
            best = min(best, eclo + normal_units // 2)
    return best


def calculate_priority(
    inst: Instance,
    activity_id: str,
    scenario: str,
    *,
    as_of_date: dt.date,
    remaining_accesses: float | None = None,
    eclo_used: int = 0,
) -> PriorityBreakdown:
    """Return an explainable priority score; higher values dispatch first.

    The calculation uses duration-adjusted due-date slack as its feasibility
    guardrail, then scales the official delay penalty by data-derived closure
    and access restrictions. Nature names are deliberately not hardcoded:
    buffer depth and opposite-bound mirroring carry their operational meaning.
    """
    scenario = scenario.upper()
    if scenario not in {"A", "B", "C"}:
        raise ValueError(f"unknown scenario {scenario!r}; expected A, B or C")
    activity = inst.activities[activity_id]
    contract = inst.contracts[activity.contract_number]
    remaining = float(activity.total_accesses if remaining_accesses is None else remaining_accesses)
    remaining_eclo = None if scenario == "B" else max(0, 2 - eclo_used)
    duration = _minimum_duration_weeks(remaining, scenario, remaining_eclo)

    days_to_due = (contract.planned_completion_date - as_of_date).days + 1
    available_days = max(1, days_to_due)
    duration_days = 7 * duration
    slack_days = days_to_due - duration_days
    runway_ratio = min(20.0, duration_days / available_days)
    late_weeks = max(0.0, -slack_days / 7)
    slack_weeks = max(0.0, slack_days / 7)
    proximity = 1 / (1 + slack_weeks)
    urgency_decay = proximity * proximity
    duration_factor = 1 + 0.15 * duration

    contract_weight = CONTRACT_WEIGHT[contract.contract_priority]
    activity_multiplier = 1 + ACTIVITY_NUDGE[activity.activity_priority]
    weekly_penalty = 7 * contract_weight * activity_multiplier

    buffer_depth, mirrors = inst.buffer_rules[contract.nature_of_activity]
    nature_multiplier = 1 + 0.10 * buffer_depth + (0.15 if mirrors else 0)
    access_multiplier = ACCESS_MULTIPLIER[contract.access_type]
    footprint = inst.closure_footprint(activity)
    closure_pressure = (
        sum(1 / max(1, inst.supply[location]) for location in footprint) / len(footprint)
        if footprint else 0.0
    )
    restriction = nature_multiplier * access_multiplier * (1 + 0.25 * closure_pressure)

    if scenario == "A":
        score = weekly_penalty * restriction * duration_factor * (
            urgency_decay + 10 * late_weeks
        )
    elif scenario == "B":
        feasibility = 10_000 * late_weeks + 1_000 * urgency_decay * duration_factor
        score = restriction * feasibility + weekly_penalty * proximity
    else:
        avoidable_penalty = max(0.0, weekly_penalty - ECLO_COST)
        score = restriction * duration_factor * (
            ECLO_COST * urgency_decay
            + avoidable_penalty * (2 * urgency_decay + 10 * late_weeks)
        )

    return PriorityBreakdown(
        activity_id=activity_id,
        contract_number=activity.contract_number,
        scenario=scenario,
        as_of_date=as_of_date,
        due_date=contract.planned_completion_date,
        days_to_due=days_to_due,
        remaining_accesses=remaining,
        minimum_duration_weeks=duration,
        slack_days=slack_days,
        runway_ratio=round(runway_ratio, 4),
        contract_weight=contract_weight,
        activity_multiplier=activity_multiplier,
        weekly_delay_penalty=weekly_penalty,
        nature_multiplier=round(nature_multiplier, 4),
        access_multiplier=access_multiplier,
        closure_pressure=round(closure_pressure, 4),
        restriction_multiplier=round(restriction, 4),
        score=round(score, 6),
    )


def ranked_priorities(
    inst: Instance, scenario: str, as_of_date: dt.date
) -> list[PriorityBreakdown]:
    return sorted(
        (calculate_priority(inst, aid, scenario, as_of_date=as_of_date)
         for aid in inst.activities),
        key=lambda item: (-item.score, item.due_date, item.activity_id),
    )


if __name__ == "__main__":
    legacy_main()
