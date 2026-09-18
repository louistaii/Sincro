"""Does the critical-path-earliest schedule actually fit under capacity?

Places every activity at its earliest feasible week (planned start +
precedence only), one access-night per week, then measures the resulting
per-location-week load against LOCATION_SUPPLY under two co-sharing
assumptions. Anything that overflows is where the real problem lives.
"""

from __future__ import annotations

import sys
from collections import defaultdict

from .analyse import MAX_CO_SHARE, cuts_traction_power, earliest_schedule
from .instance import load_instance


def main(data_dir: str = "01_data") -> None:
    inst = load_instance(data_dir)
    sched = earliest_schedule(inst, eclo=False)

    span_load: dict[tuple[str, int], list[str]] = defaultdict(list)
    foot_load: dict[tuple[str, int], list[str]] = defaultdict(list)
    for aid, (start, fin) in sched.items():
        a = inst.activities[aid]
        span, foot = inst.span_locations(a), inst.closure_footprint(a)
        for wk in range(start, fin + 1):
            for loc in span:
                span_load[(loc, wk)].append(aid)
            for loc in foot:
                foot_load[(loc, wk)].append(aid)

    over_slot, over_mix = [], []
    for (loc, wk), acts in sorted(span_load.items()):
        cap = inst.supply[loc]
        if len(acts) > cap * MAX_CO_SHARE:
            over_mix.append((loc, wk, cap, acts))
        elif len(acts) > cap:
            over_slot.append((loc, wk, cap, acts))

    print("=" * 78)
    print("EARLIEST-SCHEDULE CAPACITY PROBE  (Scenario A, no ECLO)")
    print("=" * 78)
    print(f"occupied location-weeks (span)      : {len(span_load)}")
    print(f"  over cap*{MAX_CO_SHARE} -> HARD infeasible : {len(over_mix)}")
    print(f"  over cap  -> needs co-sharing     : {len(over_slot)}")

    if over_mix:
        print("\n  HARD overflows (cannot be fixed by co-sharing):")
        for loc, wk, cap, acts in over_mix:
            print(f"    wk{wk:>2} {loc:<24} cap={cap} x{MAX_CO_SHARE} < {len(acts)}: {acts}")

    print("\n  needs-co-sharing hotspots (top 15 by crowding):")
    for loc, wk, cap, acts in sorted(over_slot, key=lambda r: -len(r[3]))[:15]:
        types = [inst.contracts[inst.activities[x].contract_number].access_type for x in acts]
        print(f"    wk{wk:>2} {loc:<24} cap={cap} < {len(acts)} "
              f"[{','.join(f'{x}:{t}' for x, t in zip(acts, types))}]")

    # ---- legal-mix violations that co-sharing cannot resolve -------------
    print("\n" + "-" * 78)
    print("LEGAL-MIX PROBLEMS (PM must be alone; <=1 PC per possession)")
    print("-" * 78)
    pm_clashes = []
    for (loc, wk), acts in sorted(foot_load.items()):
        kinds = {x: inst.contracts[inst.activities[x].contract_number].access_type for x in acts}
        pms = [x for x, t in kinds.items() if t == "PM"]
        if pms and len(acts) > 1:
            pm_clashes.append((loc, wk, pms, [x for x in acts if x not in pms]))
    if pm_clashes:
        weeks = sorted({wk for _, wk, _, _ in pm_clashes})
        pmset = sorted({p for _, _, ps, _ in pm_clashes for p in ps})
        others = sorted({o for _, _, _, os_ in pm_clashes for o in os_})
        print(f"  PM footprint overlaps another activity in {len(pm_clashes)} location-weeks")
        print(f"    weeks   : {weeks}")
        print(f"    PM      : {pmset}")
        print(f"    blocked : {others}")
    else:
        print("  none")

    # ---- Live cross-line pressure ---------------------------------------
    print("\n" + "-" * 78)
    print("LIVE ACTIVITY BLAST RADIUS")
    print("-" * 78)
    for a in inst.activities.values():
        if not cuts_traction_power(inst, a):
            continue
        start, fin = sched[a.activity_id]
        foot = inst.closure_footprint(a)
        collide = set()
        for wk in range(start, fin + 1):
            for loc in foot:
                collide.update(x for x in foot_load.get((loc, wk), []) if x != a.activity_id)
        c = inst.contracts[a.contract_number]
        print(f"  {a.activity_id} ({a.contract_number}, {c.access_type}, P{c.contract_priority}) "
              f"earliest wk{start}, closes {len(foot)} locations")
        print(f"    would collide with {len(collide)} activities: {sorted(collide)}")

    # ---- what the interchange actually wants ----------------------------
    print("\n" + "-" * 78)
    print("INTERCHANGE DEMAND (the 12 capacity-1 locations)")
    print("-" * 78)
    cap1 = sorted(l for l, c in inst.supply.items() if c == 1)
    for loc in cap1:
        wks = sorted(wk for (l, wk) in span_load if l == loc)
        tot = sum(len(span_load[(loc, wk)]) for wk in wks)
        peak = max((len(span_load[(loc, wk)]) for wk in wks), default=0)
        print(f"  {loc:<24} weeks-used={len(wks):>2}/{inst.horizon_weeks}  activity-weeks={tot:>3}  peak/wk={peak}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "01_data")
