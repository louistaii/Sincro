"""Gemini-callable tool wrappers around the existing add/edit/postpone change kinds.

This module does not reimplement any scheduling logic. It exposes the three
change kinds already understood by :mod:`sincro.change_control`
(``append``, ``edit_activity``/``edit_contract``, ``postpone``) as:

1. Plain Python callables (:func:`add_activity`, :func:`edit_activity`,
   :func:`edit_contract`, :func:`postpone_access`) that build the same
   ``change`` dicts the web UI sends, then run them through the existing
   :func:`~sincro.change_control.apply_changes` /
   :func:`~sincro.change_control.build_schedule_constraints` functions.
2. Gemini "function calling" declarations (:data:`TOOL_DECLARATIONS`) so a
   Gemini model can decide which of these to call from a natural-language
   request (e.g. "add 3 more nights to A007").
3. A small dispatcher (:func:`dispatch_tool_call`) that Gemini's returned
   function-call name/arguments can be routed through.

Nothing here talks to the network; only :mod:`sincro.gemini_client` does that.
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

from .change_control import apply_changes, build_schedule_constraints
from .instance import Instance

# ---------------------------------------------------------------------------
# 1. Plain callables — thin wrappers around the existing change-control kinds
# ---------------------------------------------------------------------------


def add_activity(data_dir: Path, *, activity_id: str, contract_number: str, activity_type: str,
                  start_location_id: str, end_location_id: str, total_accesses: int,
                  planned_start_date: str, predecessor_activity_id: str = '',
                  activity_priority: int = 1) -> list[str]:
    """Add a new activity to the instance (``kind: 'append'``)."""
    change = {
        'kind': 'append',
        'activities': [{
            'activity_id': activity_id, 'contract_number': contract_number,
            'activity_type': activity_type, 'start_location_id': start_location_id,
            'end_location_id': end_location_id, 'total_accesses': total_accesses,
            'planned_start_date': planned_start_date,
            'predecessor_activity_id': predecessor_activity_id,
            'activity_priority': activity_priority,
        }],
    }
    return apply_changes(data_dir, [change])


def edit_activity(data_dir: Path, *, activity_id: str, total_accesses: int,
                   planned_start_date: str | None = None) -> list[str]:
    """Change an existing activity's planned days / start date (``kind: 'edit_activity'``)."""
    change: dict[str, Any] = {
        'kind': 'edit_activity', 'activity_id': activity_id, 'total_accesses': total_accesses,
    }
    if planned_start_date:
        change['planned_start_date'] = planned_start_date
    return apply_changes(data_dir, [change])


def edit_contract(data_dir: Path, *, contract_number: str, total_accesses: int,
                   planned_completion_date: str | None = None) -> list[str]:
    """Rebalance a contract's total planned days across its activities (``kind: 'edit_contract'``)."""
    change: dict[str, Any] = {
        'kind': 'edit_contract', 'contract_number': contract_number, 'total_accesses': total_accesses,
    }
    if planned_completion_date:
        change['planned_completion_date'] = planned_completion_date
    return apply_changes(data_dir, [change])


def postpone_access(inst: Instance, baseline: list[dict], *, activity_id: str, week: int,
                     access_seq: int, as_of_date: str) -> tuple[dict, dict]:
    """Postpone a single scheduled access (``kind: 'postpone'``).

    Unlike the other three, this does not rewrite the instance CSVs. It
    freezes/relaxes the 14-day stability window and returns
    ``(schedule_constraints, metadata)`` for the next solve, exactly as
    ``web.py`` does today.
    """
    change = {'kind': 'postpone', 'activity_id': activity_id, 'week': week, 'access_seq': access_seq}
    parsed_date = dt.date.fromisoformat(as_of_date)
    return build_schedule_constraints(inst, baseline, [change], parsed_date)


# ---------------------------------------------------------------------------
# 2. Gemini function-calling declarations
# ---------------------------------------------------------------------------

TOOL_DECLARATIONS: list[dict[str, Any]] = [
    {
        'name': 'add_activity',
        'description': (
            'Add a brand-new activity to a contract in the rail-access plan. '
            'Use this when the user wants to add/insert/create new work.'
        ),
        'parameters': {
            'type': 'object',
            'properties': {
                'activity_id': {'type': 'string', 'description': 'New unique activity id, e.g. A090'},
                'contract_number': {'type': 'string', 'description': 'Existing contract number, e.g. C001'},
                'activity_type': {'type': 'string', 'description': 'Activity type from the selected existing contract'},
                'start_location_id': {'type': 'string', 'description': 'Location id where the activity starts'},
                'end_location_id': {'type': 'string', 'description': 'Location id where the activity ends'},
                'total_accesses': {'type': 'integer', 'description': 'Total planned access nights'},
                'planned_start_date': {'type': 'string', 'description': 'ISO date, e.g. 2027-03-15'},
                'predecessor_activity_id': {'type': 'string', 'description': 'Optional predecessor activity id'},
                'activity_priority': {'type': 'integer', 'description': '1 (highest) to 3 (lowest); defaults to 2'},
            },
            'required': ['activity_id', 'contract_number', 'activity_type', 'start_location_id',
                         'end_location_id', 'total_accesses', 'planned_start_date'],
        },
    },
    {
        'name': 'edit_activity',
        'description': (
            'Change the total planned access nights and/or planned start date of an '
            'existing activity. Use when the user wants to modify/adjust a single activity.'
        ),
        'parameters': {
            'type': 'object',
            'properties': {
                'activity_id': {'type': 'string', 'description': 'Existing activity id, e.g. A007'},
                'total_accesses': {'type': 'integer', 'description': 'New total planned access nights'},
                'planned_start_date': {'type': 'string', 'description': 'Optional new ISO planned start date'},
            },
            'required': ['activity_id'],
        },
    },
    {
        'name': 'edit_contract',
        'description': (
            'Rebalance the total planned access nights across all of a contract\'s '
            'activities, and/or update its planned completion date. Use when the user '
            'talks about a whole contract rather than one activity.'
        ),
        'parameters': {
            'type': 'object',
            'properties': {
                'contract_number': {'type': 'string', 'description': 'Existing contract number, e.g. C001'},
                'total_accesses': {'type': 'integer', 'description': 'New total planned days for the contract'},
                'planned_completion_date': {'type': 'string', 'description': 'Optional new ISO completion date'},
            },
            'required': ['contract_number'],
        },
    },
    {
        'name': 'postpone_access',
        'description': (
            'Postpone a single already-scheduled access (one activity, one week, one '
            'access sequence) from the current live plan. Use when the user wants to '
            'delay, push back, or cancel a specific upcoming night without changing '
            'total workload.'
        ),
        'parameters': {
            'type': 'object',
            'properties': {
                'activity_id': {'type': 'string', 'description': 'Activity id whose access is postponed'},
                'week': {'type': 'integer', 'description': 'Week number of the access being postponed'},
                'access_seq': {'type': 'integer', 'description': 'Access sequence number within that activity'},
                'as_of_date': {'type': 'string', 'description': 'ISO date the change is being made, e.g. today'},
            },
            'required': ['activity_id', 'week', 'access_seq'],
        },
    },
]


def prepare_tool_change(name: str, arguments: dict[str, Any], *, inst: Instance,
                        as_of_date: str) -> tuple[dict, str]:
    """Validate a model proposal and translate it to the shared /solve protocol.

    This deliberately does not execute a mutation. All proposals go through one
    controlled solve, so a rejected proposal can never partly update a plan.
    """
    declaration = next((tool for tool in TOOL_DECLARATIONS if tool['name'] == name), None)
    if declaration is None:
        raise ValueError(f'Unknown assistant tool {name!r}')
    if not isinstance(arguments, dict):
        raise ValueError(f'{name} arguments must be an object')
    schema = declaration['parameters']
    missing = set(schema['required']) - set(arguments)
    extra = set(arguments) - set(schema['properties'])
    if missing or extra:
        raise ValueError(f'{name} has missing or unsupported arguments: {", ".join(sorted(missing | extra))}')
    args = dict(arguments)
    for key, value in args.items():
        expected = schema['properties'][key]['type']
        if expected == 'integer':
            if type(value) is not int or value < 1 or value > 100_000:
                raise ValueError(f'{key} must be a positive whole number no greater than 100000')
        elif not isinstance(value, str) or len(value) > 1000:
            raise ValueError(f'{key} must be text of at most 1000 characters')
        elif not value.strip() and key != 'predecessor_activity_id':
            raise ValueError(f'{key} must not be blank')
        if key.endswith('_date'):
            try:
                dt.date.fromisoformat(value)
            except ValueError as exc:
                raise ValueError(f'{key} must be a valid ISO date') from exc
    if name == 'add_activity':
        args.setdefault('activity_priority', 2)
        args.setdefault('predecessor_activity_id', '')
        if args['activity_priority'] not in (1, 2, 3):
            raise ValueError('activity_priority must be 1, 2 or 3')
        change = {'kind': 'append', 'activities': [args]}
    elif name == 'edit_activity':
        activity = inst.activities.get(args['activity_id'])
        if activity is None:
            raise ValueError(f'Unknown activity {args["activity_id"]}')
        if 'total_accesses' not in args and 'planned_start_date' not in args:
            raise ValueError('An activity edit needs a new workload or start date')
        args.setdefault('total_accesses', activity.total_accesses)
        change = {'kind': 'edit_activity', **args}
    elif name == 'edit_contract':
        if args['contract_number'] not in inst.contracts:
            raise ValueError(f'Unknown contract {args["contract_number"]}')
        if 'total_accesses' not in args and 'planned_completion_date' not in args:
            raise ValueError('A contract edit needs a new workload or completion date')
        args.setdefault('total_accesses', sum(a.total_accesses for a in inst.activities.values()
                                            if a.contract_number == args['contract_number']))
        change = {'kind': 'edit_contract', **args}
    else:
        as_of_date = args.pop('as_of_date', as_of_date)
        change = {'kind': 'postpone', **args}
    return change, as_of_date


# ---------------------------------------------------------------------------
# 3. Dispatcher — routes a Gemini function-call (name + arguments) to the
#    matching callable above.
# ---------------------------------------------------------------------------


def dispatch_tool_call(name: str, arguments: dict[str, Any], *, data_dir: Path | None = None,
                        inst: Instance | None = None, baseline: list[dict] | None = None) -> Any:
    """Execute the tool Gemini asked for and return its raw result.

    ``data_dir`` is required for ``add_activity``/``edit_activity``/``edit_contract``.
    ``inst`` and ``baseline`` are required for ``postpone_access``.
    """
    if name == 'add_activity':
        if data_dir is None:
            raise ValueError('add_activity requires data_dir')
        return add_activity(data_dir, **arguments)
    if name == 'edit_activity':
        if data_dir is None:
            raise ValueError('edit_activity requires data_dir')
        return edit_activity(data_dir, **arguments)
    if name == 'edit_contract':
        if data_dir is None:
            raise ValueError('edit_contract requires data_dir')
        return edit_contract(data_dir, **arguments)
    if name == 'postpone_access':
        if inst is None or baseline is None:
            raise ValueError('postpone_access requires inst and baseline')
        return postpone_access(inst, baseline, **arguments)
    raise ValueError(f'Unknown tool {name!r}')
