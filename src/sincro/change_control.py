"""Controlled amendments to an existing rail-access plan."""
from __future__ import annotations

import csv
import datetime as dt
from pathlib import Path

from .instance import Instance

ACTIVITY_FIELDS = (
    'activity_id', 'contract_number', 'activity_type', 'start_location_id',
    'end_location_id', 'total_accesses', 'planned_start_date',
    'predecessor_activity_id', 'activity_priority',
)
CONTRACT_FIELDS = (
    'contract_number', 'contract_description', 'contract_award_date',
    'activity_type', 'nature_of_activity', 'contract_priority',
    'contract_completion_date', 'planned_completion_date',
    'number_of_workfronts', 'access_type',
    'number_of_maximum_access_per_week',
)


def _read(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open(newline='', encoding='utf-8-sig') as handle:
        reader = csv.DictReader(handle)
        return list(reader.fieldnames or ()), list(reader)


def _write(path: Path, fields: list[str], rows: list[dict[str, str]]) -> None:
    with path.open('w', newline='', encoding='utf-8') as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator='\n')
        writer.writeheader()
        writer.writerows(rows)


def _positive_int(value: object, label: str, *, maximum: int | None = None) -> int:
    try:
        number = int(str(value))
    except (TypeError, ValueError) as exc:
        raise ValueError(f'{label} must be a whole number') from exc
    if number < 1 or (maximum is not None and number > maximum):
        suffix = f' between 1 and {maximum}' if maximum else ' greater than zero'
        raise ValueError(f'{label} must be{suffix}')
    return number


def _iso_date(value: object, label: str) -> str:
    try:
        return dt.date.fromisoformat(str(value)).isoformat()
    except ValueError as exc:
        raise ValueError(f'{label} must be a valid date') from exc


def _set_contract_total(rows: list[dict[str, str]], contract: str, target: int) -> None:
    members = [row for row in rows if row['contract_number'] == contract]
    if not members:
        raise ValueError(f'Unknown contract {contract}')
    if target < len(members):
        raise ValueError(f'{contract} needs at least one planned day per activity ({len(members)} total)')
    current = sum(int(row['total_accesses']) for row in members)
    delta = target - current
    if delta > 0:
        # Feed additional work into the most urgent activities first.
        order = sorted(members, key=lambda row: (int(row['activity_priority']), row['activity_id']))
        for index in range(delta):
            row = order[index % len(order)]
            row['total_accesses'] = str(int(row['total_accesses']) + 1)
    elif delta < 0:
        # Remove work from the least urgent activities, never deleting an activity.
        order = sorted(members, key=lambda row: (-int(row['activity_priority']), row['activity_id']))
        remaining = -delta
        while remaining:
            changed = False
            for row in order:
                if int(row['total_accesses']) > 1:
                    row['total_accesses'] = str(int(row['total_accesses']) - 1)
                    remaining -= 1
                    changed = True
                    if not remaining:
                        break
            if not changed:
                raise ValueError(f'{contract} cannot be reduced to {target} planned days')


def parse_append_csv(text: str) -> list[dict[str, str]]:
    reader = csv.DictReader(text.splitlines())
    if tuple(reader.fieldnames or ()) != ACTIVITY_FIELDS:
        raise ValueError('Append CSV must use the standard activity-details header')
    rows = list(reader)
    if not rows:
        raise ValueError('Append CSV has no activities')
    return rows


def apply_changes(data_dir: Path, changes: list[dict]) -> list[str]:
    """Apply validated append/edit operations to temporary instance CSVs."""
    if not isinstance(changes, list) or not changes:
        raise ValueError('Provide at least one append, edit or postpone change')
    activity_path = data_dir / '08_ACTIVITY_DETAILS.csv'
    contract_path = data_dir / '07_PROJECT_DETAILS.csv'
    activity_fields, activities = _read(activity_path)
    contract_fields, contracts = _read(contract_path)
    if tuple(activity_fields) != ACTIVITY_FIELDS or tuple(contract_fields) != CONTRACT_FIELDS:
        raise ValueError('Change control requires the standard project and activity schemas')
    ids = {row['activity_id'] for row in activities}
    contract_ids = {row['contract_number'] for row in contracts}
    notices: list[str] = []

    for change in changes:
        if not isinstance(change, dict):
            raise ValueError('Each change must be an object')
        kind = change.get('kind')
        if kind == 'append':
            rows = change.get('activities')
            if not isinstance(rows, list) or not rows:
                raise ValueError('Append requires at least one activity')
            for supplied in rows:
                if not isinstance(supplied, dict):
                    raise ValueError('Every appended activity must be an object')
                row = {field: str(supplied.get(field, '')).strip() for field in ACTIVITY_FIELDS}
                aid, contract = row['activity_id'], row['contract_number']
                if not aid or aid in ids:
                    raise ValueError(f'Activity id {aid or "(blank)"} already exists or is invalid')
                if contract not in contract_ids:
                    raise ValueError(f'Unknown contract {contract}')
                row['total_accesses'] = str(_positive_int(row['total_accesses'], f'{aid} planned days'))
                row['activity_priority'] = str(_positive_int(
                    row['activity_priority'], f'{aid} priority', maximum=3))
                row['planned_start_date'] = _iso_date(row['planned_start_date'], f'{aid} start date')
                activities.append(row)
                ids.add(aid)
                notices.append(f'Added {aid} to {contract}')
        elif kind == 'edit_activity':
            aid = str(change.get('activity_id', ''))
            row = next((item for item in activities if item['activity_id'] == aid), None)
            if row is None:
                raise ValueError(f'Unknown activity {aid}')
            row['total_accesses'] = str(_positive_int(change.get('total_accesses'), f'{aid} planned days'))
            if change.get('planned_start_date'):
                row['planned_start_date'] = _iso_date(change['planned_start_date'], f'{aid} start date')
            notices.append(f'Updated {aid} to {row["total_accesses"]} planned days')
        elif kind == 'edit_contract':
            contract = str(change.get('contract_number', ''))
            target = _positive_int(change.get('total_accesses'), f'{contract} planned days')
            _set_contract_total(activities, contract, target)
            if change.get('planned_completion_date'):
                row = next((item for item in contracts if item['contract_number'] == contract), None)
                if row is None:
                    raise ValueError(f'Unknown contract {contract}')
                row['planned_completion_date'] = _iso_date(
                    change['planned_completion_date'], f'{contract} planned completion')
            notices.append(f'Updated {contract} to {target} total planned days')
        elif kind == 'postpone':
            # Postponement changes schedule constraints, not underlying workload.
            continue
        else:
            raise ValueError(f'Unknown change type {kind!r}')

    _write(activity_path, activity_fields, activities)
    _write(contract_path, contract_fields, contracts)
    return notices


def build_schedule_constraints(inst: Instance, baseline: list[dict], changes: list[dict],
                               as_of_date: dt.date, *,
                               unavailable_accesses: list[dict] | None = None) -> tuple[dict, dict]:
    """Freeze the 14-day window and apply the same-contract notice exception."""
    if not isinstance(baseline, list) or not baseline:
        raise ValueError('A current schedule is required before applying changes')
    cutoff = as_of_date + dt.timedelta(days=14)
    baseline_by_key: dict[tuple[str, int], dict] = {}
    for row in baseline:
        try:
            aid, week = str(row['activity_id']), int(row['week'])
            normalised = {
                'activity_id': aid, 'week': week, 'eclo': int(row['eclo']),
                'access_night': int(row['access_night']),
                'access_seq': int(row.get('access_seq', 0)),
            }
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError('The current schedule contains an invalid access row') from exc
        if aid in inst.activities:
            baseline_by_key[aid, week] = normalised

    postponed: list[tuple[str, int, int]] = []
    relaxed_contracts: set[str] = set()
    policies: list[str] = []
    for change in changes:
        if change.get('kind') != 'postpone':
            continue
        aid = str(change.get('activity_id', ''))
        week = _positive_int(change.get('week'), 'Postponed week')
        seq = _positive_int(change.get('access_seq'), 'Postponed access sequence')
        activity = inst.activities.get(aid)
        row = baseline_by_key.get((aid, week))
        if activity is None or row is None or row['access_seq'] != seq:
            raise ValueError('The postponed access is not present in the current schedule')
        event_date = inst.week_start(week)
        notice = (event_date - as_of_date).days
        if notice < 0:
            raise ValueError('A completed access cannot be postponed')
        postponed.append((aid, week, seq))
        if 2 < notice < 14:
            contract = activity.contract_number
            relaxed_contracts.add(contract)
            policies.append(f'{aid}: {notice}-day notice; only {contract} may move inside the freeze window')
        elif notice < 14:
            policies.append(f'{aid}: {notice}-day notice; replacement work starts after the freeze window')
        else:
            policies.append(f'{aid}: outside the freeze window; future work may be re-optimised')

    visible_horizon = max([inst.horizon_weeks, *(row['week'] for row in baseline_by_key.values())])
    frozen_weeks = [week for week in range(1, visible_horizon + 1)
                    if inst.week_start(week) < cutoff]
    fixed: dict[tuple[str, int], dict | None] = {}
    for aid, activity in inst.activities.items():
        for week in frozen_weeks:
            # The same-contract exception begins only after the two-day
            # operational lock; completed and truly imminent work never moves.
            may_swap = (activity.contract_number in relaxed_contracts
                        and inst.week_start(week) > as_of_date + dt.timedelta(days=2))
            if may_swap:
                continue
            row = baseline_by_key.get((aid, week))
            fixed[aid, week] = ({'eclo': row['eclo'], 'access_night': row['access_night']}
                                if row else None)
    for aid, week, _ in postponed:
        fixed[aid, week] = None

    # Access sequence numbers can change after a replan. Earlier postponements
    # therefore retain their unavailable activity/week, without replaying their
    # old sequence numbers or notice exceptions against today's baseline.
    unavailable = set()
    for change in unavailable_accesses or []:
        aid = str(change.get('activity_id', ''))
        week = _positive_int(change.get('week'), 'Previously postponed week')
        if aid not in inst.activities:
            raise ValueError(f'Unknown previously postponed activity {aid}')
        unavailable.add((aid, week))
        fixed[aid, week] = None

    # Give an immediate, specific error instead of an opaque infeasible solve.
    locked_work: dict[str, int] = {}
    for (aid, _), row in fixed.items():
        if row is not None:
            locked_work[aid] = locked_work.get(aid, 0) + (3 if row['eclo'] else 2)
    for aid, half_days in locked_work.items():
        if half_days > 2 * inst.activities[aid].total_accesses:
            raise ValueError(
                f'{aid} cannot be reduced below work already protected inside the 14-day window')

    metadata = {
        'as_of_date': as_of_date.isoformat(), 'frozen_until': cutoff.isoformat(),
        'protected_accesses': sum(row is not None for row in fixed.values()),
        'relaxed_contracts': sorted(relaxed_contracts), 'policies': policies,
        'unavailable_accesses': len(unavailable),
    }
    return {'fixed': fixed}, metadata
