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
| Buffer-free work skipped closure checks (corrected after the A037/A061 report) | Different groups could occupy the same platform-week and pass local validation | Check every occupied closure footprint in the constructor, exact solver and validator for A/B/C; only legal co-sharing exempts overlaps |
| Co-workers of one contract could share a possession while using different local nights | A single simultaneous possession could bypass the workfront limit | Keep same-contract/type co-workers on one local night and validate that relationship |
| Construction stopped at horizon_weeks; export ignored unfinished work | Congested hidden instances could silently lose demand or crash during RESULTS generation | Extend static supply until all work finishes; reject incomplete exports |
| B/C export simply reused the A constructor | B could miss hard dates, and C had no balanced policy | Scenario-specific supply/ECLO policies, candidate scoring and validated outputs for A/B/C |
| C's two-week per-line ECLO window was not checked | Invalid C schedules could be labelled feasible, including cross-line Live work | Track all affected lines and enforce each line's calendar window |
| IDs, eclo values, local night ranges, sequences and duplicate rows were unchecked | Malformed submissions could crash, fake workload or evade allocations | Structured hard violations, no objective for invalid submissions, nonzero CLI status |
| RESULTS dates/scenario were ignored; earliness/hotspots were placeholders | Completion evidence and operational diagnostics were unreliable | Recompute and verify dates/overruns, infer scenario, calculate earliness and hotspots |
| No uploaded-instance execution path | Judges could not submit the eight hidden-instance CSVs through a UI | Add a local browser app with temporary uploads, timeline, validation and ZIP downloads |
| The `Live` cross-line closure at the interchange carried no buffer | The buffer was applied to the worked line only, then the neighbouring line's hub tunnel and platforms were added unbuffered, so a `Live` closure stopped dead at `H01`/`H02` instead of clearing two sectors beyond. The scheduler booked work inside a live-rail possession and the local validator agreed: the reference validator rejected `A065` inside `A074`'s closure at `PLAT:BET:S13:WB` in wk19 | Buffer the whole closure: carry the activity's own buffer around the cross-line hub locations too |
| The one-night-per-possession rule was enforced only between co-workers of the same contract | Rule 6 makes a `(location, week, co_share_group)` one possession and one access-night slot, but the constraint was keyed on `(contract_number, activity_type)`, so a label could span two nights whenever its members came from different contracts. Those are two possessions wearing one name: each sits in the other's closure without being co-shared, and the capacity count scores them as one, so supply-1 locations quietly held two possessions. The reference validator rejected `A037` and `A061` as being in each other's closure at `PLAT:BET:S15:EB` in wk9 | Bind the night across every co-worker sharing a location, whatever contract it belongs to, in the heuristic, the exact model and the validator |

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

## Hardcoding removed

The branch loaded correctly but was tuned to the public instance's names. Four
one-change relabellings of `01_data` were run through `load_instance`:

| Relabelling | Before | After |
| --- | --- | --- |
| `Live` buffer 2 -> 3 sectors | rejected: "buffer rules must match the three PS1 safety rules" | read from the CSV; closure widens 44 -> 60 locations |
| Bounds `EB`/`WB` -> `NB`/`SB` | rejected: "invalid bound in SEC:BET:S15_S16:NB" | bounds derived from the supply table |
| Lines `ALP`/`BET` -> `NOR`/`STH` | rejected: missing supply for a line that no longer exists | lines derived from the instance |
| Hubs `H01`/`H02` -> `X01`/`X02` | **loaded, and silently dropped the Live cross-line closure** | interchange derived from `is_interchange` topology |

The fourth is the one that mattered: no error, a schedule still reported
feasible, and the neighbouring line's tunnel left open under a 750V power cut.
`04_LOCATION_SUPPLY.csv` already states each location's kind, line and bound as
columns; those are now used instead of re-deriving them by splitting the id on
`:` and assuming four components.

Geometry was verified unchanged on the public instance: spans, closure
footprints and affected lines are identical for all 54 activities, and the
committed `out/A`, `out/B` and `out/C` regenerate byte-for-byte.

`priority.py` could not be imported at all (`from extract import ...` inside a
package, so `ModuleNotFoundError`); nothing imported it and no test covered it,
which is why the suite still passed. Its import is fixed and it now takes a data
directory argument, but it remains standalone and outside the solver path.

## Exact solver review

The CP-SAT backend is a large, real improvement: it proves the Section 2.5
objective rather than approximating it, cutting the public scores from
381.5/100/293.5 to 222.6/60/135.5. Splitting policy per scenario is the right
call -- A carries no ECLO variables at all, B turns planned dates into a domain
restriction, and only C needs the per-line window. Four issues were fixed.

| Issue | Why it mattered | Change |
| --- | --- | --- |
| A congested instance raised `INFEASIBLE` | Section 1 forbids declaring a case impossible; a 20-week version of the public instance crashed with an uncaught `RuntimeError` | Treat `horizon_weeks` as a start, not a ceiling: grow and re-solve, then fall back to the heuristic. The same instance now returns the same proven optimum, 222.6 |
| The tie-break was anchored to `date.today()` | The emitted schedule depended on the day it was generated; weights moved by five orders of magnitude between dates and the top-ranked activity changed | Anchor on `horizon_start`, so a given instance always yields the same answer key |
| Possessions per location-week were fixed at four | Denied C the extra possession the brief grants it, and silently under-models any instance whose `supply_capacity` exceeds four | Derive from the instance: nominal supply plus the scenario's own excess allowance |
| An exclusive possession was only capped at one per group, not kept alone | The model could emit a `PM` co-sharing with three co-workers, which its own validator rejects. The public instance hides this because its only `PM` contract is `Live`, so the buffer rule happens to cover it | Enforce "alone in its possession" directly, driven by `rules.ACCESS_ROLES` |

The two CP-SAT models also duplicated the railway physics -- spans, buffers,
capacity, mixes, workfronts, predecessors -- so a fix in one would not reach
the other. They are now one `_build` plus a `ScenarioPolicy`. All three proven
optima are unchanged by the merge.

`PM`/`PC` literals, a hardcoded `<= 4`, the objective's `5` and `7`, the
two-week ECLO window and `ACCESS_MULTIPLIER` had reappeared across
`optimal.py`, `priority.py`, `validate.py` and `feasibility_probe.py`. All now
come from `rules.py`, which is the only module naming an access code.

The web app -- the judges' live upload path -- called the heuristic, so an
uploaded instance would have scored 381.5/100/293.5 while the repository
advertised 222.6/60/135.5. It now reaches for the exact solver with a 90-second
budget per scenario and falls back rather than hanging.

**Still open:** OR-Tools is an undeclared dependency (no `requirements.txt` or
`pyproject.toml`), so a judge cloning the repository gets the heuristic
silently. The exact solve takes roughly 150 seconds for all three scenarios on
the 54-activity public instance; a larger hidden instance may not prove
optimality inside the web budget, in which case the fallback decides the score.

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
