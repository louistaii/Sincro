# Sincro — Railway Track Access Optimisation

A dependency-free Python planner for the dual-line PS1 instance. It delivers the
complete workload, compares Scenarios A/B/C, checks the submission files, and
provides a browser interface for uploading the eight instance CSV or Excel files.

**Validation is local, not a certification from the judges.** The reference
`trackaccess` package, `02_references/`, and `03_submission_sample/` are not in
this repository. The dependency-free solver is a deterministic heuristic;
the optional OR-Tools backend proves the contract-final-delay
objective, including an extended-horizon improvement check. Organiser-validator
equivalence is unverified.
Development tests, audits, and saved answer keys remain on the `dev` branch;
this branch retains the deployment-only layout.

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
The server binds to localhost by default for local use.

### Deploying to Google Cloud Run

The `Dockerfile` at the repo root packages `src/`, `01_data/` and
`requirements.txt` into a container that runs the same `sincro.web` server
unmodified, listening on `$PORT` (Cloud Run sets this; the container defaults
to 8080 if unset). Deploy with:

```bash
gcloud run deploy sincro --source . --region <region> --allow-unauthenticated
```

`requirements.txt` includes OR-Tools for Precision mode. The merged optimiser
has no primary solve-time limit; the previous 90-second cap no longer applies.
The HTTP request waits for the solve to complete, so hosting/proxy request
limits can still interrupt a difficult instance. Configure and test deployment
timeouts against representative uploads; long-running jobs may require an
asynchronous worker. Precision does not silently downgrade to the heuristic.

### Deployed-service safeguards

The container defaults to one CP-SAT worker (`SINCRO_SOLVER_WORKERS=1`) to avoid
mistaking shared-host CPU count for the instance's allocation. This changes
parallelism, not the objective or optimality requirements. Each HTTP solve uses
a fresh child process so native solver memory is released after the response.
Only one solve runs per instance; overlapping requests receive a JSON 503 with
`Retry-After`. The threaded HTTP listener continues serving `/healthz` and the
homepage during optimisation. `/healthz` also identifies the service version.

The browser requests A/B/C sequentially and preserves each completed result.
Non-JSON gateway failures now show the HTTP error instead of a JSON parse error.
These safeguards do not override Cloud Run memory or request-timeout limits;
if HTTP 503/504 persists, inspect the revision's logs and resource settings.

Deployment checks (plus the full development test suite on `dev`):

```bash
PYTHONPATH=src python -m unittest discover -s checks
node checks/test_frontend.cjs
```

## Generate and validate answer keys

```bash
# Generate out/A, out/B and out/C, each containing exactly three CSVs.
PYTHONPATH=src python3 -m sincro.emit 01_data out all

# Or generate one scenario to a chosen directory.
PYTHONPATH=src python3 -m sincro.emit 01_data out/B B

# Optimise contract-final delay penalties, then improve the
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

## Public results and corrected penalty formula

The current scoring formula is `contract-final-delay-v5`. For each contract:

`delay penalty = final contract late days * sum(activity daily rates)`

Each activity's daily rate is its contract tier weight (100/10/1) multiplied by
its activity multiplier (1.3/1.2/1.0). All activities in a delayed contract
contribute their rates, including siblings completed earlier. Contract overrun
days are counted once per contract; weighted penalty therefore cannot be inferred
from that day count alone.

The unchanged saved schedules on `dev` now reproduce the expected values:

| Scenario | Contract overrun days | Delay penalty | ECLO cost | Total penalty |
| --- | ---: | ---: | ---: | ---: |
| A | 49 | 1028.3 | 0 | 1028.3 |
| B | 0 | 0 | 60 | 60 |
| C | 28 | 558.6 | 20 | 578.6 |

There is no excess-access cost in these schedules. C's total includes four ECLO
accesses at five points each. The former 222.6 for A and 135.5 total for C used
a different, activity-own-delay formula and must not be shown as the intended
penalty. `activity_finish_weighted_score` remains an explicitly separate
diagnostic. `penalty_breakdown` exposes the actual components of the total.

The optimiser, heuristic and validator now use the same contract-final objective.
Existing schedule CSVs were not changed to perform this correction. Fresh
optimisation can return a different valid schedule and a different penalty:
the local-model primary minima are A 1028.3, B 60 and C 542.4. In particular,
the saved C schedule's corrected 578.6 is a regression fixture, not a claim
that it is optimal under the restored objective.

Precision proves the primary objective and checks a finite extended horizon
before claiming global optimality within the implemented local model. Priority
tie-breaking cannot worsen that primary penalty. Model/export score disagreement
is rejected. These proofs do not establish equivalence to an inaccessible
organiser validator on all inputs.

### Which solver runs when

| Entry point | Backend |
| --- | --- |
| `sincro.emit` (default) | heuristic |
| `sincro.emit --optimal` | exact, no primary time cap, falls back to the heuristic |
| `sincro.web` **Precision** | exact, no primary time cap, extended-horizon certificate |
| `sincro.web` **Fast preview** | heuristic, explicitly not optimal |

Install the exact backend with `pip install -r requirements.txt`.
Fallbacks are named in the report's `solver` field. Controlled replanning requires
the exact backend to preserve protected decisions.

`horizon_weeks` is a starting point: if workload does not fit, the solver grows
it and retries. A horizon-only optimum is not reported as global unless the
strict-improvement certificate also succeeds. The priority tie-break uses
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

The objective sums, for each activity, its contract priority weight **100/10/1**
times its activity multiplier **1.3/1.2/1.0** times **its contract's final late days**.
Thus daily rates are 130/120/100 in tier 1, 13/12/10 in tier 2, and 1.3/1.2/1
in tier 3; an activity nudge never crosses a tier's band. The exact model uses
integer tenths to preserve these rates without rounding away small penalties.
B/C add **7 per excess
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
Dockerfile                Container image for Google Cloud Run
requirements.txt          Runtime dependencies, including OR-Tools
```

```bash
# Rank activities by tunable urgency weights (standalone; edit WEIGHTS to tune).
PYTHONPATH=src python3 -m sincro.priority 01_data
```

The diagnostic lower bounds ignore capacity, workfront contention and some ECLO
restrictions; they are bounds on a relaxation, not evidence of an achievable
schedule. Hidden instances can have very different bottlenecks from the public
54-activity dataset.
