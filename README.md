# Sincro — PS1 Railway Track Access Optimisation

Solver and decision-support tooling for the dual-line (Alpha / Beta) possession
scheduling problem.

## Layout

```
01_data/            the public instance (8 CSVs)
src/sincro/
  instance.py       CSV parsing, network model, span + closure-footprint logic
  analyse.py        capacity pressure, precedence stats, critical-path bound
  report.py         Phase 0 report      -> python3 -m sincro.report 01_data
  feasibility_probe.py  earliest-schedule capacity probe
  construct.py      greedy feasible-always constructor
  emit.py           writes the three submission CSVs
  validate.py       independent re-implementation of the hard rules + scoring
out/A/              Scenario A submission (feasible, 0 hard violations)
```

Run everything with `PYTHONPATH=src`:

```bash
PYTHONPATH=src python3 -m sincro.report 01_data
PYTHONPATH=src python3 -m sincro.feasibility_probe 01_data
PYTHONPATH=src python3 -m sincro.emit 01_data out/A A
PYTHONPATH=src python3 -m sincro.validate 01_data out/A A
```

No third-party dependencies; standard library only.

## What the public instance actually looks like

| | |
|---|---|
| horizon | 2027-01-04 → 2027-08-01, 30 weeks |
| contracts | 14 |
| activities | 54 |
| locations | 76 (36 tunnel sectors, 40 platform sectors) |
| total access-nights of work | 192 |
| model size | 54 × 30 = 1 620 activity-week booleans |

The instance is **small**. A CP-SAT model over it is not a scaling problem.

## Findings that decide the architecture

### 1. Precedence is near-absent

Only **6 of 54** activities (11%) name a predecessor, every chain is **depth 2**,
and there are **no cross-contract links**. The predecessor DAG is not the
problem. One trap: **A038's planned start (2027-01-18) precedes its own
predecessor A037's (2027-02-22)** — a solver that trusts `planned_start_date`
alone emits an infeasible schedule.

### 2. Capacity is globally loose, locally contested

Demand over the whole horizon, counting each activity's full closure footprint
(span + buffers + mirroring), is **1 372 location-weeks against 5 640
possession-slots — a ratio of 0.24**, and 0.06 once co-sharing is allowed.

But the load is not uniform. All the pressure sits on the **12 capacity-1
interchange locations**. `SEC:BET:H01_H02:EB` alone carries 36 activity-weeks of
span demand against 30 possession-slots: **co-sharing there is mandatory, not an
optimisation.** Placing the earliest-possible schedule overflows capacity in 35
location-weeks and **none of them exceed capacity × 4**, so every one is
resolvable by co-sharing within the legal mix.

The problem is a **small hard core (the interchange) inside a large easy shell**
— neither of the two architectures the shape of the spec suggests.

### 3. The score floor is tiny, and the binding constraint is not what it looks like

With infinite capacity, honouring only planned starts, precedence, and the
one-access-night-per-activity-per-week rule, Scenario A's
`priority_weighted_score` floor is **25.2** — two Priority-3 activities (A036,
A059) that physically cannot finish on time because
`planned_start + total_accesses − 1` already runs past their contract's date.
**No Priority-1 or Priority-2 contract is forced to overrun at all.**

That floor is **not reachable**. A075 (`C014`, Live, `PM`) needs one night in
weeks 24–28; A036's seven accesses occupy weeks 22–28 and cannot start earlier;
their closure footprints intersect; and `PM` may not co-share. A075 therefore
slips one week no matter what. Making room by delaying A036 costs 9.1 to save
7.0 — strictly worse.

**Scenario A's optimum is 32.2**, and the greedy constructor in `construct.py`
already reaches it under the *conservative* buffer reading.

### 4. Scenario C degenerates to Scenario A on this instance

* **B** (zero overrun, hard) is feasible and needs **exactly 6 ECLO nights**:
  4 on A036, 2 on A059 → an ECLO penalty of 30, plus whatever excess capacity
  costs.
* **C** caps ECLO at 2 nights per activity via the §2.4 r10 continuity window,
  so A036's 4 required nights are **impossible in C** and it must overrun.
* And ECLO is not worth buying anyway for these two: both sit in Priority-3
  contracts, where a day of overrun costs 1.0–1.3 against 5.0 per ECLO-night.
  For A036, no ECLO scores 18.2 and 2 ECLO scores 19.1.

C's excess-capacity allowance does not help either: A075 is blocked by the
`PM`-alone and buffer rules, not by capacity, and A036 is blocked by the
one-access-per-week rule. **C's optimum is therefore also ≈ 32.2, with zero
ECLO and zero excess access-nights.**

## Scenario A result

`out/A/`, checked by `sincro.validate`:

```
feasible: true, hard_violations: []
priority_weighted_score : 32.2
priority_overrun        : {"3": 28}     # no P1 or P2 overrun
overrun_days_total      : 28  across 3 contracts
excess_access_nights    : 0
eclo_nights             : 0
```

## Open questions that the README alone cannot settle

1. **`trackaccess` is not in this repo.** §2.6 tells participants to run
   `python3 -m trackaccess expand` to generate `co_share_group`, and the rubric
   scores against a reference validator. Neither ships here. `validate.py` is a
   reimplementation and its agreement with the real validator is unverified.
2. **`02_references/` and `03_submission_sample/` are missing**, so the one
   worked example of a feasible submission is unavailable to check against.
3. **Buffer granularity is ambiguous.** §2.4 r4 says closures apply "that
   night"; §2.4 r6 says different `co_share_group`s are "on separate nights"
   yet "buffers apply normally between them". This code takes the conservative
   reading — different possessions in the same week may not have overlapping
   footprints anywhere — so a schedule that passes here should also pass the
   permissive reading.
4. **Completion-date convention.** Overrun is measured from the end of the
   finishing week here. Measuring from the week's start would score 11.4
   instead of 25.2 at the floor.
5. **One access-night per activity per week** is asserted in §2.4 r10 but never
   stated as a rule. `validate.py` enforces it; if the real validator does not,
   every bound above loosens.
