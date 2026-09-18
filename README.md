# Sincro — Railway Track Access Optimisation

A dependency-free Python planner for the dual-line PS1 instance. It delivers the
complete workload, compares Scenarios A/B/C, checks the submission files, and
provides a browser interface for uploading the eight instance CSV or Excel files.

**Validation is local, not a certification from the judges.** The reference
`trackaccess` package, `02_references/`, and `03_submission_sample/` are not in
this repository. The dependency-free solver is a deterministic heuristic;
the optional OR-Tools backend proves the inferred contract-completion objective
within the modelled horizon. Organiser-validator equivalence is unverified.
See [REVIEW.md](REVIEW.md) for the branch review and remaining delivery gaps.
The full brief is kept verbatim in [docs/PROBLEM_STATEMENT.txt](docs/PROBLEM_STATEMENT.txt).

## Run the web app

Requires Python 3.10 or later. The fast preview has no third-party dependencies;
install `ortools` to use the default exact optimiser. From the repository root:

```bash
PYTHONPATH=src python3 -m sincro.web
```

Open <http://127.0.0.1:8000>. Select all eight named instance files as CSV or
single-sheet XLSX workbooks and choose a scenario, or select **Use public example**
to run the bundled data. Exact optimisation is the default; **Fast preview** uses
the deterministic heuristic for a quicker planning pass. The app shows:

- Completion of every activity, local validation, and the scenario's penalty.
- A filterable weekly calendar, access timeline, contract summary, ECLO nights,
  delayed work, locations and predecessors.
- Capacity hotspots and the full local validation report.
- A ZIP per successful scenario containing exactly the three required CSV files.
- Separate iCalendar (`.ics`) and calendar-summary CSV downloads for stakeholder
  calendars and reporting.

The interface follows your system's light or dark appearance. The **Auto** control
in the header switches between following the system, always light and always dark;
the choice is remembered in the browser.

### Controlled plan changes

Every generated scenario includes an **Append · edit · postpone** action:

- **Append** accepts one or more rows in the standard activity-details CSV
  format, or a guided form tied to an existing contract.
- **Edit** changes an activity's planned access days/start date, or a contract's
  aggregate access days/planned completion date. Contract reductions always
  retain at least one day for each activity.
- **Postpone** removes a selected access from its planned week and finds a
  validated replacement.

The change date controls a rolling 14-day stability window. Existing decisions
inside that window are fixed. With 3–13 days' notice, a postponed access may be
replanned only alongside future work owned by the same contract; the first two
days and every other contract remain protected. With two days' notice or less,
the replacement is scheduled after the frozen window. Controlled replanning
uses the exact OR-Tools model because the heuristic cannot guarantee these
locks. Each revised result states the freeze date and number of protected and
changed accesses.

Uploads are processed in temporary directories and removed after the response.
A failure in B is shown explicitly while successful A/C results remain available.
The server binds to localhost by default. This is a local application, not yet a
hosted submission URL. Public hosting still needs a deployment environment and
an appropriate production server/proxy.

## Generate and validate answer keys

```bash
# Generate out/A, out/B and out/C, each containing exactly three CSVs.
PYTHONPATH=src python3 -m sincro.emit 01_data out all

# Or generate one scenario to a chosen directory.
PYTHONPATH=src python3 -m sincro.emit 01_data out/B B

# Optimise the contract-completion objective, then improve its
# priority-weighted completion tie-break (pip install ortools).
PYTHONPATH=src python3 -m sincro.emit 01_data out/optimal-a A --optimal

# Infer the scenario from RESULTS.csv, or supply A/B/C as the final argument.
PYTHONPATH=src python3 -m sincro.validate 01_data out/B

# Regression checks, including adversarial submissions and changed uploads.
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

The validator returns JSON and exits nonzero for an invalid submission. All
three files are checked, including their schemas, workload, exact occupancy,
completion results, scenario, and hard rules. A quality objective is reported
only when the complete submission passes. Generation validates a staged answer
key before copying it into the output directory; a failed solve leaves existing
answer keys untouched.

## Public results

All checked-in outputs deliver **54 activities / 192 required work units** and
pass every local hard constraint. Scores below use contract completion dates:
every activity's priority multiplier is charged for its **contract's final
lateness**, including activities that finished earlier.

This interpretation exactly reproduces the organiser's reported **1028.3** for
the previous A schedule. The former activity-finish objective gave 222.6 for A
and 135.5 for C, so proving those objectives did not establish optimality under
the organiser's scoring. The original brief is preserved unchanged; the current
scoring formula is explicitly labelled `organiser-inferred-contract-v3`.
The old metric remains available as `activity_finish_weighted_score` for audit.

The organiser validator is unavailable. For the previous C artifact the inferred
formula gives **578.6**, while the reported organiser score was **576.8**; that
small discrepancy remains unverified. No organiser validation attempts were used.

| Scenario | Previous schedule, rescored locally | New local penalty | Contract overrun days | ECLO nights | Contracts late |
| --- | ---: | ---: | ---: | ---: | ---: |
| A | 1028.3 | 1028.3 | 49 | 0 | 5 of 14 |
| B | 60.0 | 60.0 | 0 | 12 | 0 of 14 |
| C | 578.6 | 542.4 | 28 | 6 | 3 of 14 |

These are the `out/optimal-*` schedules. All three primary objectives are proven
for the local model's 30-week horizon; independent 60-week A/C solves reached the
same values. C buys two more ECLO nights to concentrate delay in fewer contracts,
reducing its inferred penalty by 36.2 (6.3%). A/B cannot improve within this
model and horizon. A proof here does not certify the inaccessible organiser score.
All three have zero excess location-nights and no Priority-1 contract overruns.

The dependency-free fallback also improves when scored on this same basis:

| Scenario | Previous heuristic artifact | Improved heuristic | Exact |
| --- | ---: | ---: | ---: |
| A | 1702.4 | 1346.8 | 1028.3 |
| B | 100.0 | 100.0 | 60.0 |
| C | 1285.4 | 854.6 | 542.4 |

The heuristic outputs remain in `out/A`, `out/B`, and `out/C`. Use the
`out/optimal-*` outputs or the web app's **Optimise** engine for the best scores.

The exact model now removes interchangeable possession labels. Under the
existing weekly closure rules, every pair at a common location must already
co-share; therefore each location-week uses at most one possession. Disjoint
sites still require an actual shared location to waive buffer conflicts. This
reduces search size without relaxing any hard rule. Differential tests compare
both representations, including infeasible cases.

The optimiser first minimises the contract-completion penalty, locks a proven
value, and spends up to 30 seconds improving priority-weighted activity completion
among equal primary solutions. Reports expose the local proof status, lower
bound, gap, and horizon. A candidate whose model objective disagrees with its
serialized validation score is rejected before any existing output is replaced.
The [recorded comparison](out/algorithm-results.json) includes baseline scores,
measured solve times, and validation for both backends.

### Which solver runs when

| Entry point | Backend |
| --- | --- |
| `sincro.emit` (default) | heuristic |
| `sincro.emit --optimal` | exact, no primary time cap, falls back to the heuristic |
| `sincro.web` (judges' upload) | exact, 90 s per primary attempt, falls back |

Install the exact backend with `pip install -r requirements-optional.txt`.
Fallbacks are named in the report's `solver` field. Controlled replanning requires
the exact backend to preserve protected decisions.

`horizon_weeks` is a starting point: if workload does not fit, the solver grows
it and retries. Optimality applies to the horizon actually solved; extending a
feasible horizon can improve an arbitrary instance. The priority tie-break uses
`horizon_start`, not the wall clock, so urgency weights do not change with the
date on which the solver runs.

## One model, three policies

The three scenarios share a single CP-SAT model. The physics -- spans,
buffers, capacity, legal mixes, workfronts, co-sharing, predecessors -- is
identical in every scenario and is built once. Only policy differs:

| | A | B | C |
| --- | --- | --- | --- |
| ECLO | forbidden | unrestricted timing | one window per line |
| Planned dates | soft | **hard** | soft |
| Overrun scored | yes | no | yes |
| Excess per location-week | 0 | unbounded | 1 |
| May extend horizon | yes | no (dates bind) | yes |

Capacity limits and excess charges still follow each location's `supply_capacity`
and the scenario allowance. The expanded representation retained for differential
tests uses that capacity plus headroom; production solves use the equivalent
single-possession representation implied by the existing closure rules.

## Scheduling policies

The constructor considers legal co-sharing first, respects per-contract local
night allocation and workfronts, and checks buffers, bound mirroring and the
Live-only interchange crossover. It tries multiple reproducible priority orders
and successor-aware orders, ranking complete candidates by the scenario score.
A/C then use a fixed-seed search across equal-score plateaus and optional release
delays, retaining the best feasible incumbent. Geometry is reused across candidates.

- **A:** Fixed supply, no ECLO. All work continues until delivered, with dates
  allowed to slip.
- **B:** Dates are hard. The search permits extra supply and unrestricted ECLO,
  then removes ECLO when doing so reduces cost without missing a deadline. If
  the heuristic cannot find a deadline-feasible answer, it reports that failure
  and exports no late or partial B submission. This is not a proof of infeasibility.
- **C:** At most one extra possession per location-week. The search compares
  delay with extra-supply cost and beneficial ECLO choices, using one window of
  at most two consecutive calendar weeks per affected line. Cross-line Live
  ECLO must fit both windows.

The objective uses contract priority weights **100/10/1**, multiplied by the
activity multiplier **1.3/1.2/1.0**, summed across each contract and multiplied
by its final overrun days. B/C add **7 per excess
location-night** and **5 per ECLO access**; B excludes overrun from its soft score
because lateness is a hard failure.

The scenario-aware dispatcher additionally calculates duration-adjusted slack
from the current simulated date, closure pressure, data-derived nature risk
(buffer depth and opposite-bound mirroring), and PM/PC/C restrictiveness. A
scales this by avoided delay cost; B makes deadline feasibility dominant; C
accounts for the five-point ECLO trade-off. This ranking is a secondary
decision only: exact solving locks the proven scenario penalty before applying
it, so priority improvements cannot worsen the primary objective.

## Portability to hidden instances

Nothing about the public instance is compiled in. Line codes, bound names,
station and interchange-hub ids, buffer depths, natures of works and the
spelling of location ids are all read from the CSVs, so a hidden instance that
renames any of them behaves identically. `04_LOCATION_SUPPLY.csv` is treated as
the authority on geography: its `location_kind`, `line_code` and `bound`
columns are used directly, and a location id is only ever matched against the
sector and station ids the other files declare. No `SEC:`/`PLAT:` prefix,
component count or delimiter is assumed.

The interchange is derived rather than named: hubs are the stations flagged
`is_interchange`, and a hub tunnel is one running between two of them (or
flagged `is_shared`). Cross-line reach follows the instance's own
`opposite_bound_required` flag -- the signal that a nature cuts traction power
-- rather than a nature literally spelled `Live`. Networks with more than two
lines or two bounds are handled: a power cut at a shared interchange closes the
hub locations on *every* other line meeting there.

`tests/test_portability.py` enforces this by relabelling the public CSVs --
hubs, bounds, lines, natures, buffer depths, and all of them at once -- and
asserting the geometry is isomorphic, plus a synthesised three-line network.
These matter because a hardcoded identifier does not reliably crash: renaming
the hubs previously dropped the Live cross-line closure *silently*, which reads
as a perfectly feasible schedule.

The constants the problem statement fixes for every instance -- the 100/10/1
priority bands, the activity nudge, the 1.5x ECLO yield and the four-way
co-sharing mix -- live in `src/sincro/rules.py`, deliberately separate from
anything instance-shaped.

## Explicit modelling assumptions

These must be checked against the reference validator when it is supplied:

1. Each activity has at most one access per week. Completion is the **end of the
   final access week**. A successor starts in a strictly later week.
2. A sector-to-sector span books every traversed sector and every platform,
   **including both end platforms**. Platform endpoints and reversed routes are
   supported. Non-live (Others) has no closure beyond its occupied span.
3. All possessions use conservative **weekly** closure-footprint checks,
   including the occupied span of buffer-free work. Activities with overlapping
   footprints must legally co-share or use different weeks. There is no global
   calendar-night field in the published output; `access_night` is local to
   contract/type/week and cannot exempt separate groups from closure checks.
4. Co-sharing exempts a conflicting pair only when they actually share a
   location and have matching groups at every common occupied location. A
   group label reused on disjoint locations does not waive their buffers.
   All co-workers in one possession use the same access night, including those
   from different contracts; the night must fit every co-worker's allocation.
5. The supplied LOCATION_SUPPLY format is static: it has no week or date column.
   That recurring weekly supply is reused beyond `horizon_weeks` to deliver all
   work. Maintenance is already deducted from supply; dated maintenance
   closures would require an additional, agreed input format.

Unknown predecessors, cycles, duplicate identifiers, invalid allocations and
missing required locations are rejected. A permanently zero-supply location in
A cannot be repaired by waiting; this is reported instead of hanging or quietly
omitting its workload.

## Layout and diagnostics

```text
01_data/                  Eight public instance CSVs
src/sincro/instance.py     Parsing, topology, spans and closure geometry
src/sincro/rules.py        Constants fixed by the brief, not by the instance
src/sincro/optimal.py      Exact CP-SAT backend, one model with three policies
src/sincro/construct.py    Complete construction and bounded heuristic search
src/sincro/optimal.py      Exact primary optimisation and priority tie-breaking
src/sincro/emit.py         Three-file output, validated before publication
src/sincro/validate.py     Local submission checker and scenario scoring
src/sincro/web.py          Upload/solve/download service
src/sincro/web.html        Browser interface and access timeline
src/sincro/analyse.py      Capacity and optimistic critical-path analysis
src/sincro/report.py       Human-readable instance diagnostics
src/sincro/feasibility_probe.py  Earliest-start pressure probe
src/sincro/extract.py      Column-faithful CSV reader for standalone analysis
src/sincro/priority.py     Legacy tunable and scenario-aware urgency rankings
out/{A,B,C}/              Precomputed heuristic public answer keys
out/optimal-{a,b,c}/      Proven-primary public answer keys
tests/                    Hard-rule, congestion and upload regressions
tests/test_portability.py  Relabelled-instance and multi-line regressions
docs/PROBLEM_STATEMENT.txt The brief, verbatim
REVIEW.md                 Review findings and unresolved deliverables
```

```bash
PYTHONPATH=src python3 -m sincro.report 01_data
PYTHONPATH=src python3 -m sincro.feasibility_probe 01_data

# Rank activities by tunable urgency weights (standalone; edit WEIGHTS to tune).
PYTHONPATH=src python3 -m sincro.priority 01_data
```

The diagnostic lower bounds ignore capacity, workfront contention and some ECLO
restrictions; they are bounds on a relaxation, not evidence of an achievable
schedule. Hidden instances can have very different bottlenecks from the public
54-activity dataset.
