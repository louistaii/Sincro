# Sincro — Railway Track Access Optimisation

A dependency-free Python planner for the dual-line PS1 instance. It delivers the
complete workload, compares Scenarios A/B/C, checks the submission files, and
provides a browser interface for uploading the eight instance CSVs.

**Validation is local, not a certification from the judges.** The reference
`trackaccess` package, `02_references/`, and `03_submission_sample/` are not in
this repository. The solver is a deterministic heuristic, not an optimality proof.
See [REVIEW.md](REVIEW.md) for the branch review and remaining delivery gaps.
The full brief is kept verbatim in [docs/PROBLEM_STATEMENT.txt](docs/PROBLEM_STATEMENT.txt).

## Run the web app

Requires Python 3.10 or later; no packages to install. From the repository root:

```bash
PYTHONPATH=src python3 -m sincro.web
```

Open <http://127.0.0.1:8000>. Select all eight instance CSVs and a scenario, or
choose **Use public example** to run the bundled data. The app shows:

- Completion of every activity, local validation, and the scenario's penalty.
- A weekly access timeline, ECLO nights, delayed work, locations and predecessors.
- Capacity hotspots and the full validation report.
- A ZIP per successful scenario containing exactly the three required CSV files.

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

The checked-in outputs deliver all **54 activities / 192 required work units**.
These are results under the documented local model, not reference-validator
scores or proven optima. Lower penalties are better.

| Scenario | Local hard violations | Penalty | Contract overrun days | ECLO nights | Excess location-nights |
| --- | ---: | ---: | ---: | ---: | ---: |
| A | 0 | 316.4 | 112 | 0 | 0 |
| B | 0 | 110.0 | 0 | 22 | 0 |
| C | 0 | 229.3 | 91 | 4 | 0 |

The former README's claimed `32.2` optimum used incomplete occupancy and overly
broad sharing exemptions. Its score is not comparable with these corrected
checks. All three current schedules avoid Priority-1 overrun; A/C still have
Priority-2 and Priority-3 delays. More search may improve them.

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
3. Buffered possessions use conservative **weekly** footprint checks. Different
   buffer-free possessions may occupy the same location on separate weekly
   slots, subject to supply. There is no global calendar-night field in the
   published output; `access_night` is local to contract/type/week and is not
   used to infer simultaneous nights across different contracts.
4. Co-sharing exempts a conflicting pair only when they actually share a
   location and have matching groups at every common occupied location. A
   group label reused on disjoint locations does not waive their buffers.
   Same-contract/type co-workers in one possession use the same local night,
   so co-sharing cannot disguise a workfront breach.
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
src/sincro/construct.py    Complete construction and bounded heuristic search
src/sincro/emit.py         Three-file output, validated before publication
src/sincro/validate.py     Local submission checker and scenario scoring
src/sincro/web.py          Upload/solve/download service
src/sincro/web.html        Browser interface and access timeline
src/sincro/analyse.py      Capacity and optimistic critical-path analysis
src/sincro/report.py       Human-readable instance diagnostics
src/sincro/feasibility_probe.py  Earliest-start pressure probe
src/sincro/extract.py      Column-faithful CSV reader for standalone analysis
src/sincro/priority.py     Tunable activity urgency ranking (standalone)
out/{A,B,C}/              Precomputed public answer keys
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
