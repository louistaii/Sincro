"""Deterministic greedy construction with full workload delivery.

All possessions use conservative weekly closure checks, including occupied
locations with no extra buffer. Overlapping activities must legally co-share.
This is a heuristic, not a proof of optimality or reference-validator equivalence.
"""
from __future__ import annotations

import math
import random
import sys
from collections import defaultdict

from .analyse import ACTIVITY_NUDGE, CONTRACT_WEIGHT
from .instance import Instance, load_instance
from .priority import calculate_priority
from .rules import night_yield


class SchedulingError(ValueError):
    """No complete schedule was produced; never export a partial answer key."""


class Geometry:
    def __init__(self, inst: Instance):
        self.spans = {aid: set(inst.span_locations(a)) for aid, a in inst.activities.items()}
        self.feet = {aid: inst.closure_footprint(a) for aid, a in inst.activities.items()}
        self.kinds = {aid: inst.contracts[a.contract_number].access_type for aid, a in inst.activities.items()}
        self.release = {aid: max(1, inst.week_of(a.planned_start_date))
                        for aid, a in inst.activities.items()}
        self.deadline = {
            aid: ((inst.contracts[a.contract_number].planned_completion_date - inst.horizon_start).days + 1) // 7
            for aid, a in inst.activities.items()}
        self.weight = {
            aid: CONTRACT_WEIGHT[inst.contracts[a.contract_number].contract_priority]
                 * (1 + ACTIVITY_NUDGE[a.activity_priority])
            for aid, a in inst.activities.items()}
        self.latest = dict(self.deadline)
        self.chain_weight = dict(self.weight)
        # Backward propagation makes work which unlocks a successor inherit
        # that successor's latest start and its completion penalty.
        pending = set(inst.activities)
        while pending:
            leaves = pending - {inst.activities[aid].predecessor_activity_id for aid in pending}
            if not leaves:
                raise ValueError('activity precedence cycle')
            for aid in sorted(leaves):
                a = inst.activities[aid]
                if a.predecessor_activity_id:
                    pred = a.predecessor_activity_id
                    self.latest[pred] = min(self.latest[pred], self.latest[aid] - a.total_accesses)
                    self.chain_weight[pred] = max(self.chain_weight[pred], self.chain_weight[aid])
            pending.difference_update(leaves)


class Week:
    def __init__(self, inst: Instance, week: int, geometry: Geometry | None = None,
                 extra_capacity: int | None = 0):
        self.inst, self.week = inst, week
        self.geo = geometry or Geometry(inst)
        self.extra_capacity = extra_capacity  # None = Scenario B's unlimited elasticity
        self.groups: list[list[str]] = []
        self.group_of: dict[str, int] = {}
        self.night_of: dict[str, int] = {}
        self.nights = defaultdict(lambda: defaultdict(list))
        self.groups_at = defaultdict(set)

    def _mix_ok(self, aid: str, group: int) -> bool:
        if group == -1:
            return True
        for loc in self.geo.spans[aid]:
            kinds = [self.geo.kinds[m] for m in self.groups[group] if loc in self.geo.spans[m]]
            kinds.append(self.geo.kinds[aid])
            if len(kinds) > 4 or ('PM' in kinds and len(kinds) > 1) or kinds.count('PC') > 1:
                return False
        return True

    def _buffers_ok(self, aid: str, group: int) -> bool:
        for gi, members in enumerate(self.groups):
            for other in members:
                # Zero buffer still closes the occupied span. Local night
                # numbers do not waive another possession's weekly closure.
                if self.geo.feet[aid] & self.geo.feet[other]:
                    if gi != group or not (self.geo.spans[aid] & self.geo.spans[other]):
                        return False
        return True

    def _capacity_ok(self, aid: str, group: int) -> bool:
        if self.extra_capacity is None:
            return True
        for loc in self.geo.spans[aid]:
            used = len(self.groups_at[loc]) + (group not in self.groups_at[loc])
            if used > self.inst.supply[loc] + self.extra_capacity:
                return False
        return True

    def _night_ok(self, aid: str, group: int) -> int | None:
        a = self.inst.activities[aid]
        c = self.inst.contracts[a.contract_number]
        key = (a.contract_number, a.activity_type)
        # Rule 6: the possession at a shared location is a single access night,
        # so every co-worker standing on one of this activity's locations fixes
        # its night -- not only the ones from its own contract.
        peers = [] if group == -1 else [m for m in self.groups[group]
                                        if self.geo.spans[aid] & self.geo.spans[m]]
        required = {self.night_of[m] for m in peers}
        if len(required) > 1:
            return None
        options = sorted(required) if required else range(1, c.max_access_per_week + 1)
        for night in options:
            # A peer's night can sit outside this contract's weekly allowance.
            if not 1 <= night <= c.max_access_per_week:
                continue
            if len(self.nights[key][night]) < c.number_of_workfronts:
                return night
        return None

    def try_place(self, aid: str) -> int | None:
        # Prefer a real shared location over spending another possession slot.
        options = [g for g, members in enumerate(self.groups)
                   if any(self.geo.spans[aid] & self.geo.spans[m] for m in members)] + [-1]
        for group in options:
            night = self._night_ok(aid, group)
            if night is None or not self._mix_ok(aid, group) or not self._buffers_ok(aid, group):
                continue
            if not self._capacity_ok(aid, group):
                continue
            if group == -1:
                group = len(self.groups)
                self.groups.append([])
            self.groups[group].append(aid)
            self.group_of[aid] = group
            self.night_of[aid] = night
            for loc in self.geo.spans[aid]:
                self.groups_at[loc].add(group)
            a = self.inst.activities[aid]
            self.nights[(a.contract_number, a.activity_type)][night].append(aid)
            return night
        return None


def construct(inst: Instance, scenario: str = 'A', *, eclo_quotas: dict[str, int] | None = None,
              eclo_windows: dict[str, int] | None = None, extra_capacity: int | None = 0,
              ordering: str = 'slack', dispatch_bias: dict[str, float] | None = None,
              not_before: dict[str, int] | None = None, _geometry: Geometry | None = None) -> tuple[dict, list]:
    if scenario not in ('A', 'B', 'C'):
        raise ValueError('scenario must be A, B or C')
    if scenario == 'A' and (extra_capacity != 0 or any((eclo_quotas or {}).values())):
        raise ValueError('Scenario A forbids ECLO and extra capacity')
    if scenario == 'C' and extra_capacity not in (0, 1):
        raise ValueError('Scenario C permits at most one extra possession per location-week')
    if _geometry is None:
        inst.check()
    geo = _geometry or Geometry(inst)
    for aid, span in geo.spans.items():
        if extra_capacity is not None and any(inst.supply[loc] + extra_capacity < 1 for loc in span):
            raise SchedulingError(f'{aid}: required location has zero recurring supply')
    quotas = eclo_quotas or {}
    windows = eclo_windows or {}
    remaining = {aid: float(a.total_accesses) for aid, a in inst.activities.items()}
    finish_week: dict[str, int] = {}
    placements = []
    group_of = {}
    seq = defaultdict(int)
    eclo_used = defaultdict(int)
    # Static weekly supply repeats after the nominal horizon. Serial execution
    # is a finite fallback bound; never silently truncate at horizon_weeks.
    release = {aid: max(geo.release[aid], (not_before or {}).get(aid, 1)) for aid in inst.activities}
    last_release = max(release.values())
    limit = last_release + sum(a.total_accesses for a in inst.activities.values())
    excess = 0

    for week in range(1, limit + 1):
        wk = Week(inst, week, geo, extra_capacity)
        ready = [aid for aid, a in inst.activities.items() if remaining[aid] > 0
                 and release[aid] <= week
                 and (not a.predecessor_activity_id or
                      finish_week.get(a.predecessor_activity_id, limit + 1) < week)]

        def urgency(aid: str) -> tuple:
            a = inst.activities[aid]
            c = inst.contracts[a.contract_number]
            weight = CONTRACT_WEIGHT[c.contract_priority] * (1 + ACTIVITY_NUDGE[a.activity_priority])
            # A week completes on its last day, even for a midweek deadline.
            deadline = geo.deadline[aid]
            needed = math.ceil(remaining[aid])
            slack = deadline - week - needed + 1
            if ordering == 'chain':
                return (geo.latest[aid] - week - needed + 1 + (dispatch_bias or {}).get(aid, 0),
                        -geo.chain_weight[aid], aid)
            if ordering == 'priority':
                return (-weight, slack, aid)
            if ordering == 'cost':
                return (-weight * max(0, 1 - slack), slack, -weight, aid)
            if ordering == 'scenario':
                detail = calculate_priority(
                    inst, aid, scenario, as_of_date=inst.week_start(week),
                    remaining_accesses=remaining[aid], eclo_used=eclo_used[aid])
                return (detail.slack_days, -detail.score, aid)
            return (slack + (dispatch_bias or {}).get(aid, 0), -weight, aid)

        for aid in sorted(ready, key=urgency):
            night = wk.try_place(aid)
            if night is None:
                continue
            eclo = scenario != 'A' and eclo_used[aid] < quotas.get(aid, 0) and remaining[aid] > 1
            if eclo and scenario == 'C':
                eclo = all(line in windows and windows[line] <= week <= windows[line] + 1
                           for line in inst.affected_lines(inst.activities[aid]))
            eclo = int(eclo)
            seq[aid] += 1
            eclo_used[aid] += eclo
            group_of[(aid, week)] = wk.group_of[aid]
            placements.append((aid, seq[aid], week, eclo, night))
            remaining[aid] = max(0, remaining[aid] - night_yield(eclo))
            if remaining[aid] == 0:
                finish_week[aid] = week
        excess += sum(max(0, len(gs) - inst.supply[loc]) for loc, gs in wk.groups_at.items())
        if len(finish_week) == len(inst.activities):
            break
    unfinished = {aid: r for aid, r in remaining.items() if r > 0}
    if unfinished:
        raise SchedulingError(f'construction made insufficient progress: {unfinished}')
    return {'placements': placements, 'finish_week': finish_week, 'group_of': group_of,
            'unfinished': {}, 'excess': excess, 'eclo_quotas': dict(quotas),
            'eclo_windows': dict(windows), 'ordering': ordering,
            'dispatch_bias': dict(dispatch_bias or {}), 'not_before': dict(not_before or {})}, []


def score(inst: Instance, finish_week: dict[str, int]) -> dict:
    """Price each activity's own late days; retain contract scoring for audit."""
    tier_days = defaultdict(int)
    total = 0.0
    activity_total = 0.0
    rows = []
    contract_finish = {}
    for aid, fin in finish_week.items():
        cn = inst.activities[aid].contract_number
        contract_finish[cn] = max(contract_finish.get(cn, 0), fin)
    for aid, fin in finish_week.items():
        a = inst.activities[aid]
        c = inst.contracts[a.contract_number]
        weight = CONTRACT_WEIGHT[c.contract_priority] * (1 + ACTIVITY_NUDGE[a.activity_priority])
        activity_total += weight * max(0, (inst.week_end(fin) - c.planned_completion_date).days)
        total += weight * max(0, (inst.week_end(contract_finish[a.contract_number])
                                 - c.planned_completion_date).days)
        days = max(0, (inst.week_end(fin) - c.planned_completion_date).days)
        if days:
            tier_days[c.contract_priority] += days
            rows.append((aid, a.contract_number, c.contract_priority, a.activity_priority, days, round(weight * days, 1)))
    return {'priority_weighted_score': round(activity_total, 1), 'priority_overrun': dict(sorted(tier_days.items())),
            'activity_finish_weighted_score': round(activity_total, 1),
            'contract_finish_weighted_score': round(total, 1),
            'rows': sorted(rows, key=lambda r: (-r[5], r[0]))}


def objective(inst: Instance, result: dict, scenario: str) -> float:
    delay = score(inst, result['finish_week'])['priority_weighted_score']
    if scenario == 'B' and delay:
        return math.inf
    return round((delay if scenario != 'B' else 0) +
                 (7 * result['excess'] + 5 * sum(p[3] for p in result['placements']) if scenario != 'A' else 0), 1)


def improve_dispatch(inst: Instance, best: dict, scenario: str, extra: int | None,
                     geometry: Geometry | None = None) -> dict:
    """Two bounded local-search passes to repair costly greedy packing choices."""
    for _ in range(2):
        changed = False
        for aid in sorted(inst.activities):
            for shift in (-12, -4, 4, 12):
                biases = dict(best['dispatch_bias'])
                biases[aid] = biases.get(aid, 0) + shift
                trial, _ = construct(inst, scenario, eclo_quotas=best['eclo_quotas'],
                                     eclo_windows=best['eclo_windows'], extra_capacity=extra,
                                     ordering='slack', dispatch_bias=biases,
                                     not_before=best['not_before'], _geometry=geometry)
                if objective(inst, trial, scenario) < objective(inst, best, scenario):
                    best, changed = trial, True
        if not changed:
            break
    return best


def explore_dispatch(inst: Instance, best: dict, scenario: str, extra: int | None,
                     geometry: Geometry) -> dict:
    """Cross flat penalty plateaus without losing the incumbent.

    Several sibling activities can need rearranging before their contract's
    last finish improves. A seeded, bounded search admits temporary regressions
    in its working schedule while retaining only a strictly better answer.
    Release delays also let a broad closure wait for compatible work to finish.
    Every candidate still goes through the same hard-rule constructor.
    """
    if objective(inst, best, scenario) == 0:
        return best
    rng = random.Random(9171)
    aids = sorted(inst.activities)
    current = best
    best_value = current_value = objective(inst, best, scenario)
    for attempt in range(min(1000, 20 * len(aids))):
        biases = dict(current['dispatch_bias'])
        releases = dict(current['not_before'])
        count = 1 if attempt % 5 else rng.randint(2, 5)
        if attempt % 4:
            for aid in rng.sample(aids, min(count, len(aids))):
                biases[aid] = biases.get(aid, 0) + rng.choice((-12, -4, -2, -1, 1, 2, 4, 12))
        else:
            aid = rng.choice(aids)
            releases[aid] = max(geometry.release[aid],
                                releases.get(aid, geometry.release[aid]) + rng.choice((-2, -1, 1, 2)))
        trial, _ = construct(inst, scenario, eclo_quotas=best['eclo_quotas'],
                             eclo_windows=best['eclo_windows'], extra_capacity=extra,
                             ordering='slack', dispatch_bias=biases,
                             not_before=releases, _geometry=geometry)
        value = objective(inst, trial, scenario)
        temperature = 60 * (1 - (attempt % 500) / 500)
        if value < current_value or rng.random() < math.exp(min(0, (current_value - value) / temperature)):
            current, current_value = trial, value
        if value < best_value:
            best, best_value = trial, value
        if attempt % 500 == 499:
            current, current_value = best, best_value
    return best


def solve(inst: Instance, scenario: str = 'A') -> dict:
    """Compare deterministic priority orderings, then search ECLO trade-offs.

    B starts with ECLO available and removes it when deadlines remain feasible.
    C greedily adds beneficial two-night ECLO choices within one window per line.
    Every accepted candidate conserves the full workload.
    """
    if scenario not in ('A', 'B', 'C'):
        raise ValueError('scenario must be A, B or C')
    inst.check()
    geometry = Geometry(inst)
    candidates = []
    policies = [('slack', {}), ('scenario', {}), ('priority', {}), ('cost', {})]
    if scenario != 'B':
        policies.append(('chain', {}))
    # A fixed seed makes the bounded multi-start search reproducible. Biases
    # vary priority pressure and packing order; feasibility never changes.
    rng = random.Random(2027)
    for attempt in range(48):
        pressure = (0, 2, 5, 10, 20, 40)[attempt % 6]
        spread = (2, 5, 10, 20)[(attempt // 6) % 4]
        biases = {aid: rng.uniform(-spread, spread) - pressure *
                  math.log10(CONTRACT_WEIGHT[inst.contracts[a.contract_number].contract_priority])
                  for aid, a in sorted(inst.activities.items())}
        policies.append(('slack', biases))
        if scenario != 'B':
            policies.append(('chain', biases))
    capacities = [0] if scenario == 'A' else ([0, None] if scenario == 'B' else [0, 1])
    for extra in capacities:
        quotas = ({aid: a.total_accesses for aid, a in inst.activities.items()} if scenario == 'B' else {})
        for order, biases in policies:
            try:
                result, _ = construct(inst, scenario, eclo_quotas=quotas, extra_capacity=extra,
                                      ordering=order, dispatch_bias=biases, _geometry=geometry)
            except SchedulingError:
                continue
            candidates.append((result, extra))
    if not candidates:
        raise SchedulingError('No complete schedule found with the permitted recurring supply')
    best, extra = min(candidates, key=lambda item: (objective(inst, item[0], scenario), len(item[0]['placements'])))
    best = improve_dispatch(inst, best, scenario, extra, geometry)
    if scenario == 'A':
        return explore_dispatch(inst, best, scenario, extra, geometry)
    if scenario == 'B':
        if math.isinf(objective(inst, best, scenario)):
            raise SchedulingError('No deadline-feasible Scenario B schedule found by this heuristic; '
                                  'no partial or late submission was exported')
        # Coordinate descent: preserve deadline feasibility and reduce actual cost.
        quotas = dict(best['eclo_quotas'])
        improved = True
        while improved:
            improved = False
            for aid in sorted(quotas):
                actual = sum(p[3] for p in best['placements'] if p[0] == aid)
                for count in range(actual):
                    trial_quotas = {**quotas, aid: count}
                    trial, _ = construct(inst, scenario, eclo_quotas=trial_quotas,
                                         extra_capacity=extra, ordering=best['ordering'],
                                         dispatch_bias=best['dispatch_bias'],
                                         not_before=best['not_before'], _geometry=geometry)
                    if objective(inst, trial, scenario) < objective(inst, best, scenario):
                        quotas, best, improved = trial_quotas, trial, True
                        break
        return best

    # A/C supply trade-offs have already been compared above. Explore ECLO on
    # any activity: accelerating on-time work can release a bottleneck too.
    while True:
        improvement = None
        for aid in sorted(inst.activities):
            if best['eclo_quotas'].get(aid, 0):
                continue
            weeks = [p[2] for p in best['placements'] if p[0] == aid]
            if len(weeks) < 2:
                continue
            lines = inst.affected_lines(inst.activities[aid])
            starts = sorted({w for w in weeks} | {max(1, w - 1) for w in weeks})
            for start in starts:
                windows = dict(best['eclo_windows'])
                if any(line in windows and windows[line] != start for line in lines):
                    continue
                windows.update({line: start for line in lines})
                trial, _ = construct(inst, scenario, eclo_quotas={**best['eclo_quotas'], aid: 2},
                                     eclo_windows=windows, extra_capacity=extra, ordering=best['ordering'],
                                     dispatch_bias=best['dispatch_bias'],
                                     not_before=best['not_before'], _geometry=geometry)
                if objective(inst, trial, scenario) < objective(inst, best, scenario):
                    if improvement is None or objective(inst, trial, scenario) < objective(inst, improvement, scenario):
                        improvement = trial
        if improvement is None:
            return explore_dispatch(inst, best, scenario, extra, geometry)
        best = improvement


def main(data_dir: str = '01_data') -> None:
    inst = load_instance(data_dir)
    result = solve(inst, 'A')
    print(f"Complete: {len(result['finish_week'])}/{len(inst.activities)} activities")
    print(f"Last week: {max(result['finish_week'].values())}; score: {objective(inst, result, 'A')}")


if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else '01_data')
