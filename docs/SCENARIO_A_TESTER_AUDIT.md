# Scenario A: actual submission score investigation

The user reports **1028.3**, **49 overrun days**, and **five delayed contracts**
on the actual submission tester. These figures match the checked-in
`out/optimal-a` schedule when each activity's rate is multiplied by its
contract's final delay. The local activity-own-delay objective returns 222.6
for that same schedule. The difference is scoring, not scheduling improvement.

One matching result supports the contract-final interpretation but does not
establish full equivalence to the external validator. The earlier per-activity
confirmation remains implemented; no scoring definition was silently changed.

## Where the inferred 1028.3 comes from

| Contract | Contract delay days | Sum of activity daily rates | Penalty |
| --- | ---: | ---: | ---: |
| C001 | 7 | 7.2 | 50.4 |
| C002 | 14 | 60 | 840 |
| C006 | 14 | 6.1 | 85.4 |
| C010 | 7 | 6.5 | 45.5 |
| C014 | 7 | 1 | 7 |
| Total | 49 | | 1028.3 |

All other contracts, including every Priority-1 contract, finish on time.
The exact A output is locally feasible, uses no ECLO and no excess capacity,
and delivers all 54 activities / 192 work units.

## Independent search check

Re-ran the separately constructed public-instance relaxation in
`verify_public_lower_bound.py`, for **A only**, using incumbent 1028.3:

- Retained eight activities: A001, A007, A008, A017, A036, A059, A074, A075.
- Removed the other 46 activities and all capacity, workfront and predecessor
  restrictions. Retained release dates, workload and necessary closure conflicts.
- Used a 174-week horizon covering any strict improvement on the incumbent.
- Result: **OPTIMAL; objective 1028.3; lower bound 1028.3**.

This is a more permissive model than the scheduler, yet it cannot beat the
current A schedule. Changing dispatch weights or replacing the search algorithm
alone cannot reduce this inferred score under the same rules. This proof still
shares the local geometry assumptions; it is not a proof about unavailable
tester code.

Reproduce from the repository root:

```powershell
$env:PYTHONPATH='src'
.\.tools\python\python.exe -c "from docs.verify_public_lower_bound import verify; print(verify('A', 1028.3))"
```

## Published README review

Read the current [official PS1 README](https://github.com/aochinwen/NebulaX-Hackathon-ProblemStatement/blob/main/PS1/PS1_README.md)
directly after the user confirmed the validator source is unavailable.

- Section 2.4 rule 10 explicitly states at most one access per activity-week.
  Multiple weekly accesses cannot legitimately accelerate A036 or A059.
- Section 2.5 prohibits both ECLO and excess capacity in A.
- Section 2.6 describes `access_night` as a local contract/type accounting
  index, not a global calendar-night coordinate.
- Section 2.7 describes the weighted score as a sum per overrunning activity.
  That wording does not establish the contract-final interpretation which
  reproduces the user's reported score.

No clear published rule correction was found that justifies lowering A's
penalty by altering safety, supply, releases or workload. Separate local night
labels are not established as an exemption from overlapping weekly closures.
Earlier external rejections documented in `REVIEW.md` caution against relaxing
that restriction merely to manufacture a lower local score.

The unresolved issue is now a concrete discrepancy between the published
activity-scoring wording and the reported tester result. A full downloadable
tester report or organiser clarification could resolve it without access to
validator source. Neither scoring interpretation should be presented as
externally certified on the basis of one matching number.

A one/two-activity reinsertion experiment also failed to improve A's preview
score (372.4); that unsuccessful implementation was discarded. No replacement
schedule, changed deadlines, reduced workload, weakened safety constraints or
new lower tester score is claimed. B and C were not changed in this investigation.
