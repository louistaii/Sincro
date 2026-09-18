"""Local submission validator; reference-validator equivalence is unverified.

Checks are independent of the constructor, but share the instance geometry.
"""
from __future__ import annotations

import csv
import argparse
import datetime as dt
import json
from collections import defaultdict
from pathlib import Path

from .analyse import ACTIVITY_NUDGE, CONTRACT_WEIGHT
from . import rules
from .rules import night_yield
from .instance import Instance, load_instance

SCHEMAS = {
    'SCHEDULE_ACCESS.csv': ['activity_id', 'access_seq', 'week', 'eclo', 'access_night'],
    'SCHEDULE_OCCUPANCY.csv': ['activity_id', 'week', 'location_id', 'co_share_group'],
    'RESULTS.csv': ['scenario', 'contract_number', 'simulated_completion_date', 'overrun_days'],
}


def failure(detail: str, scenario: str | None = None) -> dict:
    return {'scenario': scenario, 'feasible': False, 'hard_violations': [
        {'rule': 'schema', 'severity': 'hard', 'detail': detail}], 'soft_scores': {}, 'detail': {}}


def validate(inst: Instance, sub_dir: str | Path, scenario: str | None = None) -> dict:
    violations: list[dict] = []

    def bad(rule, detail):
        violations.append({'rule': rule, 'severity': 'hard', 'detail': detail})

    try:
        tables = {}
        for name, header in SCHEMAS.items():
            with (Path(sub_dir) / name).open(newline='', encoding='utf-8-sig') as f:
                reader = csv.DictReader(f)
                if reader.fieldnames != header:
                    raise ValueError(f'{name}: expected columns {",".join(header)}')
                tables[name] = list(reader)
                if any(None in r or None in r.values() for r in tables[name]):
                    raise ValueError(f'{name}: malformed CSV row')
        results = tables['RESULTS.csv']
        scenarios = {r['scenario'] for r in results}
        if len(scenarios) != 1 or not scenarios <= {'A', 'B', 'C'}:
            raise ValueError('RESULTS.csv must contain exactly one scenario: A, B or C')
        file_scenario = next(iter(scenarios))
        if scenario is not None and scenario != file_scenario:
            raise ValueError(f'requested scenario {scenario} differs from RESULTS.csv ({file_scenario})')
        scenario = file_scenario
    except (OSError, ValueError, csv.Error, UnicodeError) as exc:
        return failure(str(exc), scenario)

    spans = {aid: set(inst.span_locations(a)) for aid, a in inst.activities.items()}
    feet = {aid: inst.closure_footprint(a) for aid, a in inst.activities.items()}
    yields = defaultdict(float)
    weeks_of = defaultdict(list)
    access_keys = set()
    night_by_access = {}
    sequences = defaultdict(list)
    by_week = defaultdict(set)
    ct_week = defaultdict(lambda: defaultdict(set))
    eclo_by_line = defaultdict(list)
    eclo_nights = 0
    for i, r in enumerate(tables['SCHEDULE_ACCESS.csv'], 2):
        aid = r['activity_id']
        try:
            seq, wk, e, night = (int(r[k]) for k in ('access_seq', 'week', 'eclo', 'access_night'))
            if aid not in inst.activities:
                raise ValueError(f'unknown activity {aid}')
            if seq < 1 or wk < 1 or e not in (0, 1):
                raise ValueError('sequence/week must be positive; eclo must be 0 or 1')
        except ValueError as exc:
            bad('schema', f'SCHEDULE_ACCESS.csv row {i}: {exc}')
            continue
        a = inst.activities[aid]
        c = inst.contracts[a.contract_number]
        if not 1 <= night <= c.max_access_per_week:
            bad('allocation', f'{aid} wk{wk}: access_night {night} outside 1..{c.max_access_per_week}')
        if (aid, wk) in access_keys:
            bad('multi_access_week', f'{aid}: multiple accesses in wk{wk}')
        access_keys.add((aid, wk))
        night_by_access[(aid, wk)] = night
        sequences[aid].append((wk, seq))
        yields[aid] += night_yield(e)
        weeks_of[aid].append(wk)
        by_week[wk].add(aid)
        ct_week[(a.contract_number, a.activity_type, wk)][night].add(aid)
        eclo_nights += e
        if e:
            for line in inst.affected_lines(a):
                eclo_by_line[line].append(wk)

    occupancy = defaultdict(dict)
    groups = defaultdict(set)
    groups_at = defaultdict(set)
    for i, r in enumerate(tables['SCHEDULE_OCCUPANCY.csv'], 2):
        aid, loc, label = r['activity_id'], r['location_id'], r['co_share_group'].strip()
        try:
            wk = int(r['week'])
            if aid not in inst.activities or loc not in inst.supply or wk < 1 or not label:
                raise ValueError('unknown activity/location, nonpositive week or empty co_share_group')
        except ValueError as exc:
            bad('schema', f'SCHEDULE_OCCUPANCY.csv row {i}: {exc}')
            continue
        if (aid, wk) not in access_keys:
            bad('occupancy', f'{aid} wk{wk}: occupancy has no access row')
        if loc in occupancy[(aid, wk)]:
            bad('occupancy', f'{aid} wk{wk}: duplicate location {loc}')
        occupancy[(aid, wk)][loc] = label
        groups[(loc, wk, label)].add(aid)
        groups_at[(loc, wk)].add(label)
    for aid, wk in sorted(access_keys):
        actual = set(occupancy[(aid, wk)])
        if actual != spans[aid]:
            bad('occupancy', f'{aid} wk{wk}: missing {sorted(spans[aid] - actual)}, '
                            f'unexpected {sorted(actual - spans[aid])}')

    for aid, a in inst.activities.items():
        if yields[aid] < a.total_accesses:
            bad('workload', f'{aid}: yielded {yields[aid]} < required {a.total_accesses}')
        wks = weeks_of[aid]
        if wks and min(wks) < max(1, inst.week_of(a.planned_start_date)):
            bad('planned_start', f'{aid}: begins before its planned start week')
        if [seq for _, seq in sorted(sequences[aid])] != list(range(1, len(wks) + 1)):
            bad('sequence', f'{aid}: access_seq must be chronological and consecutive from 1')
        pred = a.predecessor_activity_id
        if pred and wks and (not weeks_of[pred] or min(wks) <= max(weeks_of[pred])):
            bad('precedence', f'{aid}: must start in a strictly later week than {pred} finishes')

    for (loc, wk, label), acts in sorted(groups.items()):
        kinds = [inst.contracts[inst.activities[x].contract_number].access_type for x in acts]
        exclusive = [k for k in kinds if rules.ACCESS_ROLES[k]['exclusive']]
        hosts = [k for k in kinds if rules.ACCESS_ROLES[k]['hosts']]
        if (len(acts) > rules.MAX_CO_SHARE or (exclusive and len(acts) > 1)
                or len(exclusive) > 1 or len(hosts) > 1):
            bad('mix', f'wk{wk} {loc} group {label}: illegal mix {sorted(kinds)}')
        local_nights = defaultdict(set)
        for aid in acts:
            a = inst.activities[aid]
            if (aid, wk) in night_by_access:
                local_nights[(a.contract_number, a.activity_type)].add(night_by_access[(aid, wk)])
        if any(len(nights) > 1 for nights in local_nights.values()):
            bad('workfront', f'wk{wk} {loc} group {label}: same-contract co-workers must use the same local night')
    excess = 0
    hotspots = []
    for (loc, wk), labels in sorted(groups_at.items()):
        cap, used = inst.supply[loc], len(labels)
        over = max(0, used - cap)
        excess += over
        if used >= cap:
            hotspots.append({'location_id': loc, 'week': wk, 'used': used, 'capacity': cap, 'excess': over})
        if over and (scenario == 'A' or (scenario == 'C' and over > 1)):
            bad('capacity', f'wk{wk} {loc}: {used} possessions > supply {cap}')

    for wk, acts in sorted(by_week.items()):
        ordered = sorted(acts)
        for i, x in enumerate(ordered):
            for y in ordered[i + 1:]:
                # A zero buffer does not remove the occupied span's closure.
                # Separate local night indices are not a co-sharing exemption.
                clash = feet[x] & feet[y]
                if not clash:
                    continue
                common = spans[x] & spans[y]
                ox, oy = occupancy[(x, wk)], occupancy[(y, wk)]
                # A label reused at disjoint locations does not create a possession.
                sharing = bool(common) and all(ox.get(loc) and ox.get(loc) == oy.get(loc) for loc in common)
                if not sharing:
                    bad('closure', f'wk{wk}: {y} inside closure of [\'{x}\'] at {sorted(clash)[:3]}')

    for (cn, activity_type, wk), nights in sorted(ct_week.items()):
        c = inst.contracts[cn]
        if len(nights) > c.max_access_per_week:
            bad('allocation', f'wk{wk} {cn}/{activity_type}: too many distinct access nights')
        for night, acts in sorted(nights.items()):
            if len(acts) > c.number_of_workfronts:
                bad('workfront', f'wk{wk} {cn}/{activity_type} night {night}: '
                                f'{len(acts)} activities > {c.number_of_workfronts} workfronts')
    if scenario == 'A' and eclo_nights:
        bad('eclo', 'ECLO is forbidden in Scenario A')
    if scenario == 'C':
        for line, weeks in sorted(eclo_by_line.items()):
            if max(weeks) - min(weeks) > 1:
                bad('eclo_window', f'{line}: ECLO spans wk{min(weeks)}..wk{max(weeks)} (maximum 2 calendar weeks)')

    weighted = 0.0
    tier_days = defaultdict(int)
    contract_fin = {}
    for aid, wks in weeks_of.items():
        if not wks:
            continue
        a = inst.activities[aid]
        c = inst.contracts[a.contract_number]
        fin = max(wks)
        contract_fin[c.contract_number] = max(contract_fin.get(c.contract_number, 0), fin)
        days = max(0, (inst.week_end(fin) - c.planned_completion_date).days)
        tier_days[c.contract_priority] += days
        weighted += CONTRACT_WEIGHT[c.contract_priority] * (1 + ACTIVITY_NUDGE[a.activity_priority]) * days
    dates = {cn: inst.week_end(wk) for cn, wk in contract_fin.items()}
    overruns = {cn: max(0, (date - inst.contracts[cn].planned_completion_date).days) for cn, date in dates.items()}
    if scenario == 'B':
        for cn, days in sorted(overruns.items()):
            if days:
                bad('planned_date', f'{cn}: overruns planned completion by {days} days')

    seen_contracts = set()
    for i, r in enumerate(results, 2):
        cn = r['contract_number']
        try:
            date = dt.date.fromisoformat(r['simulated_completion_date'])
            days = int(r['overrun_days'])
            if cn not in inst.contracts or cn in seen_contracts:
                raise ValueError(f'unknown or duplicate contract {cn}')
        except ValueError as exc:
            bad('results', f'RESULTS.csv row {i}: {exc}')
            continue
        seen_contracts.add(cn)
        if cn not in dates or date != dates[cn] or days != overruns[cn]:
            bad('results', f'{cn}: completion date/overrun do not match scheduled access rows')
    if seen_contracts != set(inst.contracts):
        bad('results', f'missing contract results: {sorted(set(inst.contracts) - seen_contracts)}')

    soft = {
        'scenario': scenario, 'overrun_days_total': sum(overruns.values()),
        'contracts_overrunning': sum(days > 0 for days in overruns.values()),
        'earliness_days_total': sum(max(0, (inst.contracts[cn].planned_completion_date - date).days)
                                   for cn, date in dates.items()),
        'excess_access_nights_total': excess, 'eclo_nights_total': eclo_nights,
        'priority_overrun': {str(k): v for k, v in sorted(tier_days.items()) if v},
        'priority_weighted_score': round(weighted, 1),
    }
    if not violations:
        soft['objective_score'] = round((weighted if scenario != 'B' else 0)
                                       + (7 * excess + 5 * eclo_nights if scenario != 'A' else 0), 1)
        soft['formula_version'] = 'ps1-2.5-local-v2'
    return {'scenario': scenario, 'feasible': not violations, 'hard_violations': violations,
            'soft_scores': soft, 'detail': {'capacity_hotspots': hotspots,
            'nights_scheduled': len(tables['SCHEDULE_ACCESS.csv']), 'eclo_nights': eclo_nights}}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('data_dir')
    parser.add_argument('submission_dir')
    parser.add_argument('scenario', choices=['A', 'B', 'C'], nargs='?')
    args = parser.parse_args()
    try:
        report = validate(load_instance(args.data_dir), args.submission_dir, args.scenario)
    except (OSError, ValueError, KeyError, csv.Error) as exc:
        report = failure(str(exc))
    print(json.dumps(report, indent=2))
    raise SystemExit(0 if report['feasible'] else 1)


if __name__ == '__main__':
    main()
