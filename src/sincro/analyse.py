"""Phase 0 instance analyser.

Answers the questions that decide the solver architecture before any solver
is written: capacity pressure, precedence structure, the critical-path lower
bound on Scenario A's score, model size, and which rules the instance
actually exercises.
"""

from __future__ import annotations

import math
from collections import defaultdict

from .instance import Instance
from .rules import ACTIVITY_NUDGE, CONTRACT_WEIGHT, ECLO_YIELD, MAX_CO_SHARE

__all__ = ["ACTIVITY_NUDGE", "CONTRACT_WEIGHT", "MAX_CO_SHARE"]


def cuts_traction_power(inst: Instance, act) -> bool:
    """True for natures that mirror onto the opposite bound.

    Mirroring is the instance's own signal that a nature cuts traction power,
    which is also what makes it reach across lines at an interchange. Reading
    the flag keeps this independent of what the nature is called.
    """
    return inst.buffer_rules[inst.contracts[act.contract_number].nature_of_activity][1]


def weeks_needed(total_accesses: int, eclo: bool) -> int:
    """Weeks to burn down a workload, given <=1 access-night per activity per week."""
    return math.ceil(total_accesses / ECLO_YIELD) if eclo else total_accesses


def precedence_stats(inst: Instance) -> dict:
    preds = {a.activity_id: a.predecessor_activity_id for a in inst.activities.values()}
    linked = {k: v for k, v in preds.items() if v}
    depth: dict[str, int] = {}

    def d(aid: str, seen: frozenset = frozenset()) -> int:
        if aid in depth:
            return depth[aid]
        if aid in seen:
            raise ValueError(f"predecessor cycle at {aid}")
        p = preds.get(aid)
        depth[aid] = 1 if not p else 1 + d(p, seen | {aid})
        return depth[aid]

    for aid in preds:
        d(aid)

    inverted = [
        (aid, p) for aid, p in linked.items()
        if inst.activities[aid].planned_start_date <= inst.activities[p].planned_start_date
    ]
    cross = [
        (aid, p) for aid, p in linked.items()
        if inst.activities[aid].contract_number != inst.activities[p].contract_number
    ]
    return {
        "n_activities": len(preds),
        "n_linked": len(linked),
        "links": sorted(linked.items()),
        "max_chain_depth": max(depth.values()),
        "cross_contract_links": cross,
        "inverted_planned_starts": inverted,
    }


def earliest_schedule(inst: Instance, eclo: bool) -> dict[str, tuple[int, int]]:
    """Unconstrained-capacity earliest (start_week, finish_week) per activity.

    Respects only planned start dates and finish-to-start precedence, so it is
    a hard lower bound: capacity can only push activities later.
    """
    finish: dict[str, int] = {}

    def solve(aid: str) -> int:
        if aid in finish:
            return finish[aid]
        a = inst.activities[aid]
        start = max(1, inst.week_of(a.planned_start_date))
        if a.predecessor_activity_id:
            start = max(start, solve(a.predecessor_activity_id) + 1)
        finish[aid] = start + weeks_needed(a.total_accesses, eclo) - 1
        starts[aid] = start
        return finish[aid]

    starts: dict[str, int] = {}
    for aid in inst.activities:
        solve(aid)
    return {aid: (starts[aid], finish[aid]) for aid in inst.activities}


def score_from_schedule(inst: Instance, sched: dict[str, tuple[int, int]],
                        use_week_end: bool = True) -> dict:
    """Price contract completion, retaining activity completion as a diagnostic."""
    per_activity = []
    per_contract_finish: dict[str, int] = {}
    tier_days = defaultdict(int)
    total = 0.0
    activity_total = 0.0

    for aid, (_, fin) in sched.items():
        a = inst.activities[aid]
        per_contract_finish[a.contract_number] = max(
            per_contract_finish.get(a.contract_number, 0), fin
        )

    contract_overrun = {}
    for cn, fin in per_contract_finish.items():
        c = inst.contracts[cn]
        date = inst.week_end(fin) if use_week_end else inst.week_start(fin)
        contract_overrun[cn] = max(0, (date - c.planned_completion_date).days)

    for aid, (_, fin) in sched.items():
        a = inst.activities[aid]
        c = inst.contracts[a.contract_number]
        w = CONTRACT_WEIGHT[c.contract_priority] * (1 + ACTIVITY_NUDGE[a.activity_priority])
        date = inst.week_end(fin) if use_week_end else inst.week_start(fin)
        activity_total += w * max(0, (date - c.planned_completion_date).days)
        days = contract_overrun[a.contract_number]
        if days:
            total += w * days
            tier_days[c.contract_priority] += days
            per_activity.append((aid, c.contract_number, c.contract_priority,
                                 a.activity_priority, days, w * days))

    return {
        "priority_weighted_score": round(total, 1),
        "activity_finish_weighted_score": round(activity_total, 1),
        "priority_overrun": dict(sorted(tier_days.items())),
        "overrun_days_total": sum(contract_overrun.values()),
        "contracts_overrunning": sum(1 for v in contract_overrun.values() if v),
        "contract_overrun": contract_overrun,
        "overrunning_activities": sorted(per_activity, key=lambda r: -r[5]),
    }


def capacity_pressure(inst: Instance) -> dict:
    """Demand vs supply per location, using both the activity's own span and
    its full closure footprint (span + buffers + mirroring)."""
    def ratio(demand: int, supply: int) -> float:
        return demand / supply if supply else (math.inf if demand else 0.0)

    span_demand = defaultdict(int)
    foot_demand = defaultdict(int)
    span_sizes = {}

    for a in inst.activities.values():
        span = inst.span_locations(a)
        foot = inst.closure_footprint(a)
        span_sizes[a.activity_id] = (len(span), len(foot))
        for loc in span:
            span_demand[loc] += a.total_accesses
        for loc in foot:
            foot_demand[loc] += a.total_accesses

    H = inst.horizon_weeks
    total_supply_slots = sum(inst.supply.values()) * H
    total_span_demand = sum(span_demand.values())
    total_foot_demand = sum(foot_demand.values())

    hot = []
    for loc, cap in sorted(inst.supply.items()):
        sd, fd = span_demand.get(loc, 0), foot_demand.get(loc, 0)
        hot.append({
            "location": loc, "cap_per_week": cap, "slots_in_horizon": cap * H,
            "span_demand": sd, "footprint_demand": fd,
            "ratio_no_coshare": ratio(fd, cap * H),
            "ratio_max_coshare": ratio(fd, cap * H * MAX_CO_SHARE),
        })

    return {
        "total_supply_slots": total_supply_slots,
        "total_supply_activity_slots": total_supply_slots * MAX_CO_SHARE,
        "total_span_demand": total_span_demand,
        "total_footprint_demand": total_foot_demand,
        "ratio_span_no_coshare": ratio(total_span_demand, total_supply_slots),
        "ratio_footprint_no_coshare": ratio(total_foot_demand, total_supply_slots),
        "ratio_footprint_max_coshare": ratio(total_foot_demand, total_supply_slots * MAX_CO_SHARE),
        "per_location": hot,
        "span_sizes": span_sizes,
    }


def rule_coverage(inst: Instance) -> dict:
    by_access = defaultdict(list)
    by_nature = defaultdict(list)
    for a in inst.activities.values():
        c = inst.contracts[a.contract_number]
        by_access[c.access_type].append(a.activity_id)
        by_nature[c.nature_of_activity].append(a.activity_id)

    live_acts = [a for a in inst.activities.values()
                 if cuts_traction_power(inst, a)]
    live_cross = []
    for a in live_acts:
        line = inst.line_of(a.start_location_id)
        if any(inst.line_of(loc) != line for loc in inst.closure_footprint(a)):
            live_cross.append(a.activity_id)

    return {
        "by_access_type": {k: (len(v), v) for k, v in sorted(by_access.items())},
        "by_nature": {k: (len(v), v) for k, v in sorted(by_nature.items())},
        "live_activities": [(a.activity_id, a.contract_number, a.total_accesses,
                             a.start_location_id, a.end_location_id) for a in live_acts],
        "live_crossing_interchange": live_cross,
        "min_capacity_locations": sorted(
            [(loc, cap) for loc, cap in inst.supply.items() if cap == 1]
        ),
    }
