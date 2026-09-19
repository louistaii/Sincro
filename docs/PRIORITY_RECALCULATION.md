# Priority penalty recalculation

The user explicitly confirmed: **each activity's own late days** are charged.
The canonical scorer, exact solver, heuristic and analysis now agree on
`activity-own-delay-v4`:

`delay penalty = sum(contract_weight * (1 + activity_nudge) * activity_late_days)`

`activity_late_days = max(0, activity_completion - contract_planned_completion)`

| Contract priority | Activity P1 (+0.3) | Activity P2 (+0.2) | Activity P3 (+0.0) |
| --- | ---: | ---: | ---: |
| P1 | 130 | 120 | 100 |
| P2 | 13 | 12 | 10 |
| P3 | 1.3 | 1.2 | 1 |

Rates are per calendar day. Completion remains the end of the final scheduled
week. Finishing on time incurs zero, regardless of a sibling's completion.
The exact model uses integer tenths, so the small tier-3 differences survive
unchanged. Urgency, duration, nature and access restrictions guide the secondary
ranking, which cannot worsen the proven primary penalty.

## Verified results

All comparisons retain the same inputs, hard rules, ECLO cost (5/access) and
excess-access cost (7/location-night). B keeps hard deadlines and prices only
ECLO and excess access. All three regenerated schedules pass local validation.

| Scenario | Previous schedule, rescored | New optimum | Actual reduction | Delay component | ECLO cost |
| --- | ---: | ---: | ---: | ---: | ---: |
| A | 222.6 | 222.6 | 0 | 222.6 | 0 |
| B | 60.0 | 60.0 | 0 | 0 | 60 |
| C | 146.2 | 135.5 | 10.7 (7.3%) | 115.5 | 20 |

All scores in this table use the same confirmed formula. C reduces activity
delay by 0.7 and ECLO cost by 10, using four rather than six ECLO accesses.
Its 28 contract-delay days are now spread across four contracts instead of
three. The old contract-final delay diagnostic rises from 512.4 to 558.6;
minimising that different metric is not the confirmed objective. No Priority-1
contract overruns, capacity excess, omitted workload or hard-rule violations
occur. All 54 activities and 192 required work units are delivered.

## Proof scope

The solver proves the primary minimum at 30 weeks, then searches every week
that could contain a strictly better A/C solution. Since every activity has
a strictly positive delay rate and other penalties are nonnegative, the
incumbent gives each activity a finite latest possible completion in any better
schedule. These per-activity bounds yield certificate horizons of **59 weeks
for A** and **47 weeks for C**. Both strict-improvement models are infeasible.
B's hard planned dates bound all feasible accesses. All three primary gaps
are zero, so no better primary score exists under these implemented rules.

Secondary priority optimisation is separate and budgeted. The recorded B run
did not prove its secondary optimum; this does not affect its proven primary
penalty of 60. The unavailable organiser validator was not used. Local proofs
do not establish equivalence to its scoring or undisclosed rules.

Detailed scores, lower bounds and proof flags are in
[out/priority-recalculation.json](../out/priority-recalculation.json).
Validated schedules are in `out/optimal-a`, `out/optimal-b`, `out/optimal-c`.
Historical contract-based results in `OPTIMISATION_AUDIT.md` and
`out/algorithm-results.json` are not directly comparable to this objective.

To regenerate and compare against the currently checked-in schedules:

```powershell
$env:PYTHONPATH='src'
.\.tools\python\python.exe docs/recalculate_priorities.py
```

This stages all three candidates, requires proof and validation, rejects any
objective regression, then replaces their CSVs and writes a fresh comparison
report. A later run compares against the then-current schedules, not the old
contract-based baseline recorded here.
