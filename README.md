# Sincro — Railway Track Access Optimisation

A dependency-free Python planner for the dual-line PS1 instance. It delivers the
complete workload, compares Scenarios A/B/C, checks the submission files, and
provides a browser interface for uploading the eight instance CSV or Excel files.

**Validation is local, not a certification from the judges.** The reference
`trackaccess` package, `02_references/`, and `03_submission_sample/` are not in
this repository. The dependency-free solver is a deterministic heuristic;
the optional OR-Tools backend proves the official objective on the public data.

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
The server binds to localhost by default for local use.

### Deploying to Vercel

`api/solve.py`, `vercel.json` and `requirements.txt` at the repo root make this
deployable as a Vercel serverless function with no code changes to the app
itself. `vercel.json` sets `PYTHONPATH=src` so the function can import the
`sincro` package, and rewrites `/` to `src/sincro/web.html` and `/solve` to
`/api/solve`, so the existing frontend works unmodified. Exact/optimal solves
can take up to 90 s per scenario (`EXACT_SECONDS_PER_SCENARIO`), which exceeds
Vercel Hobby's 60 s function limit for multi-scenario runs -- fast preview
(heuristic) mode is unaffected. `requirements.txt` is intentionally empty
(stdlib only); adding `ortools` enables exact solving but adds ~100 MB to the
function bundle.

## Generate and validate answer keys

```bash
# Generate out/A, out/B and out/C, each containing exactly three CSVs.
PYTHONPATH=src python3 -m sincro.emit 01_data out all

# Or generate one scenario to a chosen directory.
PYTHONPATH=src python3 -m sincro.emit 01_data out/B B

# Require an OR-Tools proof of the official objective, then improve its
# priority-weighted completion tie-break (pip install ortools).
PYTHONPATH=src python3 -m sincro.emit 01_data out/optimal-a A --optimal

# Infer the scenario from RESULTS.csv, or supply A/B/C as the final argument.
PYTHONPATH=src python3 -m sincro.validate 01_data out/B
```

Generated answer keys are not checked into this repository; run the commands
above to produce them locally.

The validator returns JSON and exits nonzero for an invalid submission. All
three files are checked, including their schemas, workload, exact occupancy,
completion results, scenario, and hard rules. A quality objective is reported
only when the complete submission passes. Generation validates a staged answer
key before copying it into the output directory; a failed solve leaves existing
answer keys untouched.

## Public results

Regenerating the answer keys (see above) delivers all **54 activities / 192
required work units**. These are results under the documented local model,
not reference-validator scores. Lower penalties are better.

| Scenario | Local hard violations | Penalty | Contract overrun days | ECLO nights | Excess location-nights |
| --- | ---: | ---: | ---: | ---: | ---: |
| A | 0 | 381.5 | 98 | 0 | 0 |
| B | 0 | 100.0 | 0 | 20 | 0 |
| C | 0 | 293.5 | 84 | 2 | 0 |

The former README's claimed `32.2` optimum used incomplete occupancy and overly
broad sharing exemptions. Its score is not comparable with these corrected
checks. All three current schedules avoid Priority-1 overrun; A/C still have
Priority-2 and Priority-3 delays. More search may improve them.

The optional exact backend produces these `--optimal` schedules. It first
proves the official primary objective, fixes that value, and then spends up
to 30 seconds improving priority-weighted completion time among equal primary
solutions. Install it with `pip install ortools`; without it every entry point
falls back to the heuristic and names that in its `solver` field.

| Scenario | Proven primary penalty | Contract overrun days | ECLO nights | Excess location-nights | Contracts late |
| --- | ---: | ---: | ---: | ---: | ---: |
| A | 222.6 | 49 | 0 | 0 | 5 of 14 |
| B | 60.0 | 0 | 12 | 0 | 0 of 14 |
| C | 135.5 | 28 | 4 | 0 | 4 of 14 |

Against the heuristic's 381.5 / 100 / 293.5 that is 42% / 40% / 54% less
penalty. No Priority-1 contract is late in any scenario, under either backend.

The schedules were regenerated with the combined closure fixes: buffer-free
work still closes its occupied span, cross-line Live closures carry buffers,
and co-workers in one possession must use the same access night. These rules
apply in the heuristic, exact model, and validator for every scenario, including
the A037/A061 overlap at `PLAT:BET:S15:EB`. The priority completion tie-break is
time-limited and is not proven optimal.

### Which solver runs when

| Entry point | Backend |
| --- | --- |
| `sincro.emit` (default) | heuristic |
| `sincro.emit --optimal` | exact, no time cap, falls back to the heuristic |
| `sincro.web` (judges' upload) | exact, 90 s per scenario, falls back |

Falling back is deliberate. Section 1 of the brief forbids ever declaring a
case impossible, so an exact solve that is unavailable, out of time, or
infeasible degrades to the heuristic rather than raising; the report's
`solver` field names which one produced the answer key.

`horizon_weeks` is treated as a starting point, not a ceiling. If the workload
cannot fit inside the declared horizon the model grows it and re-solves, which
cannot change the optimum of an instance that already fitted, because later
weeks only ever add penalty. A 20-week version of the public instance is
infeasible as declared, yet still returns the same proven optimum of 222.6.

The priority tie-break is anchored to `horizon_start`, not to `date.today()`.
Anchoring on the wall clock made the emitted schedule depend on which day the
solver happened to run -- the same instance produced different answer keys on
different days, with tie-break weights varying by five orders of magnitude.

## One model, three policies

The three scenarios share a single CP-SAT model. The physics -- spans,
buffers, capacity, legal mixes, workfronts, co-sharing, predecessors -- is
identical in every scenario and is built once. Only policy differs:

| | A | B | C |
| --- | --- | --- | --- |
| ECLO | forbidden | free | one window per line |
| Planned dates | soft | **hard** | soft |
| Overrun scored | yes | no | yes |
| Excess per location-week | 0 | unbounded | 1 |
| May extend horizon | yes | no (dates bind) | yes |

Possessions modelled per location-week follow the instance: nominal
`supply_capacity` plus whatever excess the scenario tolerates. Modelling a
fixed four would have denied C the extra possession the brief grants it, and
would break outright on an instance whose supply exceeds four.

## Scheduling policies

The constructor considers legal co-sharing first, respects per-contract local
night allocation and workfronts, and checks buffers, bound mirroring and the
Live-only interchange crossover. It tries multiple reproducible priority orders
and two local-search passes, ranking complete candidates by the scenario score.

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
activity nudge **1.3/1.2/1.0**, per late activity-day. B/C add **7 per excess
location-night** and **5 per ECLO access**; B excludes overrun from its soft score
because lateness is a hard failure.

The scenario-aware dispatcher additionally calculates duration-adjusted slack
from the current simulated date, closure pressure, data-derived nature risk
(buffer depth and opposite-bound mirroring), and PM/PC/C restrictiveness. A
scales this by avoided delay cost; B makes deadline feasibility dominant; C
accounts for the five-point ECLO trade-off. This ranking is a secondary
decision only: exact solving locks the proven scenario penalty before applying
it, so priority improvements cannot worsen the official objective.

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

This matters because a hardcoded identifier does not reliably crash: renaming
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
src/sincro/extract.py      Column-faithful CSV reader for standalone analysis
src/sincro/priority.py     Legacy tunable and scenario-aware urgency rankings
api/solve.py              Vercel serverless entry point for POST /solve
vercel.json               Vercel routing/env config
requirements.txt          Runtime dependencies for the deployed function (stdlib only)
```

```bash
# Rank activities by tunable urgency weights (standalone; edit WEIGHTS to tune).
PYTHONPATH=src python3 -m sincro.priority 01_data
```

The diagnostic lower bounds ignore capacity, workfront contention and some ECLO
restrictions; they are bounds on a relaxation, not evidence of an achievable
schedule. Hidden instances can have very different bottlenecks from the public
54-activity dataset.
