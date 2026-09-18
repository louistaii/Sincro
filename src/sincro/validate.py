"""Independent post-hoc validator.

Re-derives every hard rule from the submission CSVs alone, sharing no code
with the constructor beyond the instance model. Mirrors the reference
validator's report shape (§2.7) so the two can be diffed once the real
`trackaccess` tool is in hand.
"""

from __future__ import annotations

import csv
import json
import sys
from collections import defaultdict
from pathlib import Path

from .analyse import ACTIVITY_NUDGE, CONTRACT_WEIGHT
from .instance import Instance, load_instance

MAX_PER_POSSESSION = 4


def validate(inst: Instance, sub_dir: str | Path, scenario: str) -> dict:
    d = Path(sub_dir)
    access = list(csv.DictReader(open(d / "SCHEDULE_ACCESS.csv")))
    occ = list(csv.DictReader(open(d / "SCHEDULE_OCCUPANCY.csv")))
    V: list[dict] = []

    def bad(rule: str, detail: str) -> None:
        V.append({"rule": rule, "severity": "hard", "detail": detail})

    # ---- index -----------------------------------------------------------
    yields: dict[str, float] = defaultdict(float)
    weeks_of: dict[str, list[int]] = defaultdict(list)
    per_week: dict[str, list[int]] = defaultdict(list)
    eclo_nights = 0
    for r in access:
        aid, wk, e = r["activity_id"], int(r["week"]), int(r["eclo"])
        yields[aid] += 1.5 if e else 1.0
        weeks_of[aid].append(wk)
        per_week[f"{aid}|{wk}"].append(int(r["access_night"]))
        eclo_nights += e

    group: dict[tuple[str, int], set[str]] = defaultdict(set)   # (loc,wk,grp)->acts
    groups_at: dict[tuple[str, int], set[str]] = defaultdict(set)  # (loc,wk)->groups
    act_groups: dict[tuple[str, int], set[str]] = defaultdict(set)  # (aid,wk)->groups
    for r in occ:
        aid, wk, loc, g = r["activity_id"], int(r["week"]), r["location_id"], r["co_share_group"]
        group[(loc, wk, g)].add(aid)
        groups_at[(loc, wk)].add(g)
        act_groups[(aid, wk)].add(g)

    # ---- R1 workload conservation ---------------------------------------
    for aid, a in inst.activities.items():
        if yields[aid] + 1e-9 < a.total_accesses:
            bad("workload", f"{aid}: yielded {yields[aid]} < required {a.total_accesses}")

    # ---- R2 planned start ------------------------------------------------
    for aid, wks in weeks_of.items():
        ps = inst.week_of(inst.activities[aid].planned_start_date)
        if min(wks) < ps:
            bad("planned_start", f"{aid}: wk{min(wks)} before planned start wk{ps}")

    # ---- one access-night per activity per week (§2.4 r10 premise) -------
    for key, nights in per_week.items():
        if len(nights) > 1:
            bad("multi_access_week", f"{key}: {len(nights)} accesses in one week")

    # ---- R3 precedence ---------------------------------------------------
    for aid, a in inst.activities.items():
        p = a.predecessor_activity_id
        if p and weeks_of[aid] and weeks_of[p]:
            if min(weeks_of[aid]) <= max(weeks_of[p]):
                bad("precedence", f"{aid} starts wk{min(weeks_of[aid])} but "
                                  f"{p} finishes wk{max(weeks_of[p])} (needs FS+0)")

    # ---- R5 legal mix ----------------------------------------------------
    for (loc, wk, g), acts in group.items():
        kinds = [inst.contracts[inst.activities[x].contract_number].access_type for x in acts]
        if len(acts) > MAX_PER_POSSESSION:
            bad("mix", f"wk{wk} {loc} grp {g}: {len(acts)} activities > {MAX_PER_POSSESSION}")
        if "PM" in kinds and len(acts) > 1:
            bad("mix", f"wk{wk} {loc} grp {g}: PM not alone -> {sorted(acts)}")
        if kinds.count("PC") > 1:
            bad("mix", f"wk{wk} {loc} grp {g}: {kinds.count('PC')} PC in one possession")

    # ---- capacity --------------------------------------------------------
    excess = 0
    for (loc, wk), gs in groups_at.items():
        cap = inst.supply[loc]
        if len(gs) > cap:
            over = len(gs) - cap
            excess += over
            if scenario == "A" or (scenario == "C" and over > 1):
                bad("capacity", f"wk{wk} {loc}: {len(gs)} possessions > supply {cap}")

    # ---- R4 closures and buffers ----------------------------------------
    by_week: dict[int, set[str]] = defaultdict(set)
    for (aid, wk) in act_groups:
        by_week[wk].add(aid)
    for wk, acts in by_week.items():
        acts = sorted(acts)
        for i, x in enumerate(acts):
            fx = inst.closure_footprint(inst.activities[x])
            for y in acts[i + 1:]:
                if act_groups[(x, wk)] & act_groups[(y, wk)]:
                    continue  # same possession -> R6 exemption
                clash = fx & inst.closure_footprint(inst.activities[y])
                if clash:
                    bad("closure", f"wk{wk}: {y} inside closure of ['{x}'] at "
                                   f"{sorted(clash)[:3]}")

    # ---- R7 weekly allocation / R8 workfronts ---------------------------
    ct_week: dict[tuple[str, str, int], dict[int, set[str]]] = defaultdict(lambda: defaultdict(set))
    for r in access:
        a = inst.activities[r["activity_id"]]
        ct_week[(a.contract_number, a.activity_type, int(r["week"]))][int(r["access_night"])].add(
            r["activity_id"])
    for (cn, at, wk), nights in ct_week.items():
        c = inst.contracts[cn]
        if len(nights) > c.max_access_per_week:
            bad("allocation", f"wk{wk} {cn}/{at}: {len(nights)} nights > "
                              f"{c.max_access_per_week}")
        for n, acts in nights.items():
            if len(acts) > c.number_of_workfronts:
                bad("workfront", f"wk{wk} {cn}/{at} night {n}: {len(acts)} activities > "
                                 f"{c.number_of_workfronts} workfronts")

    # ---- scenario levers -------------------------------------------------
    if scenario == "A" and eclo_nights:
        bad("eclo", f"{eclo_nights} ECLO nights used but ECLO is forbidden in Scenario A")

    # ---- soft scores -----------------------------------------------------
    tier_days = defaultdict(int)
    weighted = 0.0
    contract_fin: dict[str, int] = {}
    for aid, wks in weeks_of.items():
        a = inst.activities[aid]
        c = inst.contracts[a.contract_number]
        fin = max(wks)
        contract_fin[c.contract_number] = max(contract_fin.get(c.contract_number, 0), fin)
        days = max(0, (inst.week_end(fin) - c.planned_completion_date).days)
        if days:
            tier_days[c.contract_priority] += days
            weighted += CONTRACT_WEIGHT[c.contract_priority] * \
                (1 + ACTIVITY_NUDGE[a.activity_priority]) * days
    contract_over = {cn: max(0, (inst.week_end(f) - inst.contracts[cn].planned_completion_date).days)
                     for cn, f in contract_fin.items()}
    if scenario == "B" and any(contract_over.values()):
        for cn, v in contract_over.items():
            if v:
                bad("planned_date", f"{cn}: overruns planned completion by {v} days")

    obj = {"A": weighted, "B": 7 * excess + 5 * eclo_nights,
           "C": weighted + 7 * excess + 5 * eclo_nights}[scenario]

    out = {
        "scenario": scenario,
        "feasible": not V,
        "hard_violations": V,
        "soft_scores": {
            "scenario": scenario,
            "overrun_days_total": sum(contract_over.values()),
            "contracts_overrunning": sum(1 for v in contract_over.values() if v),
            "earliness_days_total": 0,
            "excess_access_nights_total": excess,
            "eclo_nights_total": eclo_nights,
            "priority_overrun": {str(k): v for k, v in sorted(tier_days.items())},
            "priority_weighted_score": round(weighted, 1),
        },
        "detail": {"capacity_hotspots": [], "nights_scheduled": len(access),
                   "eclo_nights": eclo_nights},
    }
    if not V:
        out["soft_scores"]["objective_score"] = round(obj, 1)
        out["soft_scores"]["formula_version"] = "readme-2.5"
    return out


def main() -> None:
    data, sub, scen = sys.argv[1], sys.argv[2], sys.argv[3]
    print(json.dumps(validate(load_instance(data), sub, scen), indent=2))


if __name__ == "__main__":
    main()
