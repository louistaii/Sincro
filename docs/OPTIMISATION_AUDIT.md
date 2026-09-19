# Can stronger search improve A or C?

**Historical audit:** this document uses the superseded contract-final delay
objective, not the user-confirmed activity-own-delay formula. Its references
to current schedules describe the older artifacts. See
[PRIORITY_RECALCULATION.md](PRIORITY_RECALCULATION.md) for current results.

Under the current local hard rules and inferred contract-completion score,
**A = 1028.3 and C = 542.4 are global optima**, including schedules extending
beyond the nominal planning horizon. This is a statement about the local model;
the organiser validator is inaccessible and the previous C score discrepancy
remains unresolved.

## Strict-improvement checks

Every contract has a positive daily delay cost, and all other score terms are
nonnegative. Given a target score, each contract therefore has a latest possible
completion week for any schedule meeting that target. The maximum of these bounds
gives a finite horizon covering every strictly improving schedule.

| Scenario | Incumbent | Required better score | Sufficient horizon | CP-SAT result |
| --- | ---: | ---: | ---: | --- |
| A | 1028.3 | <= 1028.2 | 174 weeks | INFEASIBLE |
| C | 542.4 | <= 542.3 | 105 weeks | INFEASIBLE |

Scores are represented in integer tenths, so these cutoffs cover every strict
improvement. Both checks finished within their 30-second limits. Derived bounds,
times and methodology are recorded in [optimality-audit.json](../out/optimality-audit.json).
No organiser validation attempts were used.

An independently written relaxation retaining only **eight of 54 activities**
also proves the same values. It omits every other activity, capacity limits,
workfront limits and predecessors, while retaining necessary conflicts, release
dates, workload and ECLO windows. Removing those restrictions still cannot improve
the scores; this isolates the bottleneck from the full solver implementation.
It continues to share local geometry and the inferred scoring formula.

Reproduce in under a second of solver time with:

```bash
PYTHONPATH=src .venv/bin/python docs/verify_public_lower_bound.py
```

The retained activities are A001, A007, A008, A017, A036, A059, A074 and A075.
This is a diagnostic for the public instance, separate from the general scheduler.

## Independent explanation of A's floor

Contract completion costs per week include C001 = 50.4, C002 = 420,
C006 = 42.7, C010 = 45.5, C013 = 84 and C014 = 7. Even the cheapest
Priority-1 contract's one-week delay costs 1540, exceeding the incumbent;
any improving schedule must therefore keep all Priority-1 contracts on time.

The following bounds use release dates, one access per activity-week, and the
current geometry and weekly closure rules, without relying on the CP-SAT optimum:

1. **C001 + C002 + C013 cost at least 890.4.** A008 needs five weeks from
   week 16 and A017 needs seven weeks from week 17. They conflict, so C002
   cannot finish before week 27 while C003 finishes by week 26. A074 adds a
   third mutually conflicting access. If A074 runs by week 27, C002 must
   finish at week 28 or later, costing at least 840. If A074 runs by week 23,
   its conflict with A007 and A001 also forces C001 one week late, adding 50.4.
   Running A074 in weeks 24–27 instead delays C013 by at least three weeks,
   costing 252. Running it at week 28 or later costs at least 588 for C013,
   plus at least 420 for C002. None beats 890.4 across these three contracts.
2. **C010 costs at least 45.5.** A059 releases at week 14 and needs seven
   standard accesses, finishing no earlier than week 20 against a week-19 target.
3. **C006 + C014 cost at least 92.4.** A036 releases at week 22 and needs
   seven accesses, finishing no earlier than week 28 against a week-26 target:
   85.4 points. Finishing at week 28 occupies every week from 22 through 28,
   excluding A075, which releases at week 24; C014 then incurs at least 7.
   Delaying A036 further costs at least 128.1 for C006 alone.

The sum is **890.4 + 45.5 + 92.4 = 1028.3**, attained by the current schedule.

## What could enable a lower valid score?

Further gains on these fixed inputs require evidence that a model restriction
or scoring interpretation differs from the organiser's rules. The most consequential
assumption is whether closure conflicts exclude an entire week or only simultaneous
nights. The current checker treats them as weekly and therefore prevents separate
possessions at an occupied location-week; more nominal capacity alone cannot help.
Previous organiser rejections are evidence against casually weakening these checks.

C's useful ECLO windows are already chosen jointly with scheduling by the exact
solver. Changing seeds, adding local search, restoring possession labels, or
extending the horizon cannot beat the certified score within this feasible set.

The audit found no valid improvement from allowing workload surplus, changing
contract/type workfront accounting, or undoing possession-label compaction.
Any future rule correction should be supported by a concrete rule example and
independent feasibility checks before replacing the current schedules.
