"""Human-readable Phase 0 report: `python3 -m sincro.report 01_data`."""

from __future__ import annotations

import sys

from .analyse import (capacity_pressure, earliest_schedule, precedence_stats,
                      rule_coverage, score_from_schedule, weeks_needed)
from .instance import load_instance


def main(data_dir: str = "01_data") -> None:
    inst = load_instance(data_dir)
    H = inst.horizon_weeks
    print("=" * 78)
    print(f"INSTANCE  {data_dir}")
    print("=" * 78)
    print(f"horizon        : {inst.horizon_start} .. {inst.week_end(H)}  ({H} weeks)")
    print(f"contracts      : {len(inst.contracts)}")
    print(f"activities     : {len(inst.activities)}")
    print(f"locations      : {len(inst.supply)}")
    print(f"total accesses : {sum(a.total_accesses for a in inst.activities.values())}")

    # ---- Q4: model size -------------------------------------------------
    n_av = len(inst.activities) * H
    print(f"\n[Q4] model size: {len(inst.activities)} activities x {H} weeks "
          f"= {n_av} activity-week booleans (+ same again for ECLO)")

    # ---- Q2: precedence -------------------------------------------------
    ps = precedence_stats(inst)
    print("\n" + "-" * 78)
    print("[Q2] PRECEDENCE STRUCTURE")
    print("-" * 78)
    print(f"activities with a predecessor : {ps['n_linked']}/{ps['n_activities']} "
          f"({100*ps['n_linked']/ps['n_activities']:.0f}%)")
    print(f"longest chain (activities)    : {ps['max_chain_depth']}")
    print(f"cross-contract links          : {len(ps['cross_contract_links'])}")
    for aid, p in ps["links"]:
        a, pa = inst.activities[aid], inst.activities[p]
        flag = "  <-- successor's planned start is NOT after predecessor's" \
            if (aid, p) in ps["inverted_planned_starts"] else ""
        print(f"    {aid} ({a.contract_number}) <- {p} ({pa.contract_number})"
              f"  starts {a.planned_start_date} / {pa.planned_start_date}{flag}")

    # ---- Q3: critical-path bound ---------------------------------------
    print("\n" + "-" * 78)
    print("[Q3] CRITICAL-PATH LOWER BOUND  (infinite capacity; only planned")
    print("     starts + precedence + 1 access-night per activity per week)")
    print("-" * 78)
    for label, eclo in (("no ECLO  (Scenario A)", False), ("unrestricted ECLO (B; optimistic bound for C)", True)):
        sched = earliest_schedule(inst, eclo)
        sc = score_from_schedule(inst, sched)
        beyond = [aid for aid, (_, f) in sched.items() if f > H]
        print(f"\n  {label}")
        print(f"    priority_weighted_score (floor) : {sc['priority_weighted_score']:,.1f}")
        print(f"    priority_overrun (activity-days): {sc['priority_overrun']}")
        print(f"    overrun_days_total (contract)   : {sc['overrun_days_total']}")
        print(f"    contracts_overrunning           : {sc['contracts_overrunning']}/{len(inst.contracts)}")
        print(f"    activities finishing past wk {H}   : {len(beyond)} {sorted(beyond)}")
        if not eclo and sc["overrunning_activities"]:
            print("    worst offenders (activity, contract, Ctier, Aprio, days, cost):")
            for row in sc["overrunning_activities"][:8]:
                print(f"       {row[0]}  {row[1]}  P{row[2]}  a{row[3]}  {row[4]:3d}d  {row[5]:9,.1f}")

    sched_a = earliest_schedule(inst, False)
    sc_end = score_from_schedule(inst, sched_a, use_week_end=True)
    sc_start = score_from_schedule(inst, sched_a, use_week_end=False)
    print(f"\n    sensitivity: completion date = week END -> {sc_end['priority_weighted_score']:,.1f}")
    print(f"                 completion date = week START -> {sc_start['priority_weighted_score']:,.1f}")

    # ---- Q1: capacity pressure -----------------------------------------
    cp = capacity_pressure(inst)
    print("\n" + "-" * 78)
    print("[Q1] CAPACITY PRESSURE")
    print("-" * 78)
    print(f"supply, possession-slots over horizon      : {cp['total_supply_slots']:,}")
    print(f"  ... as activity-slots at max co-sharing  : {cp['total_supply_activity_slots']:,}")
    print(f"demand, activity-location-weeks (span only): {cp['total_span_demand']:,}")
    print(f"demand, incl. buffers + mirroring          : {cp['total_footprint_demand']:,}")
    print(f"\n  ratio  span    / slots (no co-share)  : {cp['ratio_span_no_coshare']:.3f}")
    print(f"  ratio  footprint/ slots (no co-share)  : {cp['ratio_footprint_no_coshare']:.3f}")
    print(f"  ratio  footprint/ slots (max co-share) : {cp['ratio_footprint_max_coshare']:.3f}")

    hottest = sorted(cp["per_location"], key=lambda r: -r["ratio_no_coshare"])[:12]
    print("\n  hottest locations (footprint demand vs slots):")
    print(f"    {'location':<24} {'cap':>3} {'slots':>6} {'span':>5} {'foot':>5} "
          f"{'no-cs':>6} {'max-cs':>7}")
    for r in hottest:
        print(f"    {r['location']:<24} {r['cap_per_week']:>3} {r['slots_in_horizon']:>6} "
              f"{r['span_demand']:>5} {r['footprint_demand']:>5} "
              f"{r['ratio_no_coshare']:>6.2f} {r['ratio_max_coshare']:>7.2f}")

    sizes = cp["span_sizes"]
    widest = sorted(sizes.items(), key=lambda kv: -kv[1][1])[:6]
    print("\n  widest closure footprints (activity: span locs -> footprint locs):")
    for aid, (s, f) in widest:
        a = inst.activities[aid]
        c = inst.contracts[a.contract_number]
        print(f"    {aid}  {c.nature_of_activity:<19} {s:>2} -> {f:>2} locations"
              f"  x {a.total_accesses} nights")

    # ---- Q7: rule coverage ---------------------------------------------
    rc = rule_coverage(inst)
    print("\n" + "-" * 78)
    print("[Q7] RULE COVERAGE IN THE PUBLIC INSTANCE")
    print("-" * 78)
    for k, (n, v) in rc["by_access_type"].items():
        print(f"  access_type {k:<3}: {n:>2} activities")
    for k, (n, v) in rc["by_nature"].items():
        print(f"  nature {k:<20}: {n:>2} activities")
    print(f"\n  Live activities ({len(rc['live_activities'])}):")
    for aid, cn, ta, s, e in rc["live_activities"]:
        print(f"    {aid} {cn} accesses={ta}  {s} -> {e}")
    print(f"  Live reaching across lines at interchange: {rc['live_crossing_interchange']}")
    print(f"\n  capacity-1 locations ({len(rc['min_capacity_locations'])}): "
          f"{[l for l, _ in rc['min_capacity_locations']]}")

    # ---- weekly allocation head-room ------------------------------------
    print("\n" + "-" * 78)
    print("[EXTRA] CONTRACT WEEKLY THROUGHPUT vs WORKLOAD")
    print("-" * 78)
    print(f"  {'contract':<9} {'P':>1} {'acts':>4} {'accs':>4} {'n/wk':>4} {'wf':>2} "
          f"{'max act/wk':>10} {'win(wks)':>8} {'min wks':>7} {'slack':>6}")
    for cn, c in inst.contracts.items():
        acts = [a for a in inst.activities.values() if a.contract_number == cn]
        accs = sum(a.total_accesses for a in acts)
        first = min(inst.week_of(a.planned_start_date) for a in acts)
        last = inst.week_of(c.planned_completion_date)
        window = last - first + 1
        cap_per_week = c.max_access_per_week * c.number_of_workfronts
        # every activity needs its own week per access; parallelism caps how
        # many activities can progress in one week
        min_wks = max(max(weeks_needed(a.total_accesses, False) for a in acts),
                      -(-accs // cap_per_week))
        print(f"  {cn:<9} {c.contract_priority:>1} {len(acts):>4} {accs:>4} "
              f"{c.max_access_per_week:>4} {c.number_of_workfronts:>2} {cap_per_week:>10} "
              f"{window:>8} {min_wks:>7} {window-min_wks:>6}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "01_data")
