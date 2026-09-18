# Review of claude/railway-track-access-napyo6

Reviewed against the supplied **Problem Statement 1 — Railway Track Access
Optimisation**, starting at commit `0c85fec`.

## What the branch got right

- A compact, readable standard-library implementation, with separate instance,
  analysis, construction, serialization and validation modules.
- Explicit tunnel/platform geography, nature-specific sector buffers,
  opposite-bound mirroring, and a Live-only crossover between the two lines.
- Planned-start and strictly later-week predecessor handling, including the
  public A038/A037 inverted planned-start case.
- Full access workload accounting in the public A example, local contract/type
  night indices, and the right contract-priority bands in the weighted score.
- Useful public-instance diagnostics and recognition that the judges' validator
  and example/reference files were absent.

## Correctness gaps fixed

| Finding in the original branch | Why it mattered | Change |
| --- | --- | --- |
| Validator accepted an empty occupancy CSV and no RESULTS.csv | A submission could claim feasibility without booking any track or supplying the required completion report | Require all three files, exact occupancy per access, and matching contract results |
| Span expansion omitted both outer station platforms | Platform capacity and conflicts were invisible, including single-sector activities | Book both endpoint platforms; support platform-only and reversed spans |
| Any reused group label waived every pair's closures across the week | Disjoint worksites could bypass buffers by both using `b1` | Require an actual common location and consistent sharing across common locations |
| Different buffer-free possessions were treated as weekly closure conflicts | Nominal supply above one slot could not be used for independent possessions | Allow separate buffer-free possessions within the scenario's weekly supply |
| Co-workers of one contract could share a possession while using different local nights | A single simultaneous possession could bypass the workfront limit | Keep same-contract/type co-workers on one local night and validate that relationship |
| Construction stopped at horizon_weeks; export ignored unfinished work | Congested hidden instances could silently lose demand or crash during RESULTS generation | Extend static supply until all work finishes; reject incomplete exports |
| B/C export simply reused the A constructor | B could miss hard dates, and C had no balanced policy | Scenario-specific supply/ECLO policies, candidate scoring and validated outputs for A/B/C |
| C's two-week per-line ECLO window was not checked | Invalid C schedules could be labelled feasible, including cross-line Live work | Track all affected lines and enforce each line's calendar window |
| IDs, eclo values, local night ranges, sequences and duplicate rows were unchecked | Malformed submissions could crash, fake workload or evade allocations | Structured hard violations, no objective for invalid submissions, nonzero CLI status |
| RESULTS dates/scenario were ignored; earliness/hotspots were placeholders | Completion evidence and operational diagnostics were unreliable | Recompute and verify dates/overruns, infer scenario, calculate earliness and hotspots |
| No uploaded-instance execution path | Judges could not submit the eight hidden-instance CSVs through a UI | Add a local browser app with temporary uploads, timeline, validation and ZIP downloads |

The geometry and sharing changes deliberately make assumptions explicit. The
problem statement combines nightly wording with weekly output and local night
indices; the reference implementation is needed to settle exact equivalence.
The local solver and checker share geometry, so their agreement alone is not an
independent proof of physical correctness.

## Claims corrected

The former README called 32.2 an optimum and concluded C effectively reduces to
A. Neither was proved. Its critical-path bound ignored important constraints;
the conflict argument could not establish a global optimum; and the submitted
occupancy did not include all required platforms. Counting demand below four
times supply also does not prove that legal mixes, workfronts and buffers fit.

The replacement documentation reports measured local scores and bounded-search
limitations. The current A/C schedules differ, and B uses ECLO to meet all planned
dates. None is described as optimal.

## Verification

The regression suite covers hand-built valid and invalid submissions, all three
scenario policies, cross-contract precedence, buffer and Live crossover cases,
co-sharing, local workfront accounting, full delivery beyond the horizon, input
cycles, output/schema tampering, and uploads with changed demand. It also
regenerates and validates all public scenario outputs. Run:

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

The browser flow is exercised locally. The committed output folders contain
strictly the three specified files each, with no quality objective awarded to a
submission that fails a hard rule.

## Remaining competition deliverables and limitations

- **Reference validation:** obtain `trackaccess`, its expansion rules and the
  supplied feasible example, compare geometry/closures/date conventions, and run
  every answer key through the official checker. Local validation is not a
  substitute for this gate.
- **Hosted URL:** the repository now has a working local upload app, but no public
  deployment has been provisioned. A hosting target is still needed.
- **Video:** no three-minute YouTube walkthrough has been recorded or published.
- **Repository destination:** this repository's configured remote is GitHub;
  the statement requests a GitLab URL. No GitLab destination was supplied.
- **Optimization:** the search is heuristic and may miss a feasible B schedule
  or a better A/C schedule. Its reported B failure is not a proof of
  impossibility. Exact optimization and differential checks against the
  reference solver remain useful next work.
- **Replanning bonus:** there is no dated disruption input or minimal-churn
  re-optimization yet. The existing supply CSV describes recurring weekly
  capacity, not individual maintenance nights.
