"""Gemini-callable tools: read the last solved schedule, or change the plan.

This module does not reimplement any scheduling logic. It exposes:

1. Plain Python callables (:func:`add_activity`, :func:`edit_activity`,
   :func:`edit_contract`, :func:`postpone_access`) that build the same
   ``change`` dicts the web UI sends, then run them through the existing
   :func:`~sincro.change_control.apply_changes` /
   :func:`~sincro.change_control.build_schedule_constraints` functions.
2. Read-only lookups (:func:`get_contract`, :func:`get_activity`,
   :func:`get_week_schedule`, :func:`list_activities`) that answer questions
   from the schedule the browser already solved via ``/solve`` -- passed
   straight through in the ``/ask`` request body -- rather than re-running
   the solver.
3. Gemini "function calling" declarations (:data:`TOOL_DECLARATIONS`) so a
   Gemini model can decide which of these to call from a natural-language
   request (e.g. "add 3 more nights to A007", or "what's happening week 4").
4. A small dispatcher (:func:`dispatch_tool_call`) that Gemini's returned
   function-call name/arguments can be routed through, and
   :data:`READ_TOOL_NAMES` so a caller can tell a lookup apart from a change.

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
# 2. Read-only lookups over an already-solved schedule
# ---------------------------------------------------------------------------
#
# ``schedule`` is the payload the browser already holds after calling
# ``/solve``: ``{"A": {"activities": [...], "contracts": [...], "report": {...}},
# "B": {...}, ...}``. These never touch the filesystem or the solver.


def _scenario_data(schedule: dict[str, Any] | None, scenario: str) -> dict[str, Any]:
    if not schedule or scenario not in schedule:
        raise ValueError(
            f"Scenario {scenario!r} has not been solved yet. Ask the user to run the "
            'scheduler for it first, or use a scenario that has already been run.'
        )
    return schedule[scenario]


def get_contract(schedule: dict[str, Any] | None, *, scenario: str, contract_number: str) -> dict:
    """Look up one contract's scheduled stats (``kind: 'lookup'``, read-only)."""
    for contract in _scenario_data(schedule, scenario).get('contracts', []):
        if contract['id'] == contract_number:
            return contract
    raise ValueError(f'Unknown contract {contract_number!r} in scenario {scenario}')


def get_activity(schedule: dict[str, Any] | None, *, scenario: str, activity_id: str) -> dict:
    """Look up one activity's scheduled nights and stats (read-only)."""
    for activity in _scenario_data(schedule, scenario).get('activities', []):
        if activity['id'] == activity_id:
            return activity
    raise ValueError(f'Unknown activity {activity_id!r} in scenario {scenario}')


def get_week_schedule(schedule: dict[str, Any] | None, *, scenario: str, week: int) -> list[dict]:
    """List every scheduled access falling in one week number (read-only)."""
    events = []
    for activity in _scenario_data(schedule, scenario).get('activities', []):
        for night in activity.get('nights', []):
            if night['week'] == week:
                events.append({'activity_id': activity['id'], 'contract': activity['contract'], **night})
    return events


def list_activities(schedule: dict[str, Any] | None, *, scenario: str,
                     contract_number: str | None = None) -> list[dict]:
    """List activity ids/summaries, optionally filtered to one contract (read-only).

    Useful for finding an exact activity id before calling :func:`get_activity`.
    """
    activities = _scenario_data(schedule, scenario).get('activities', [])
    if contract_number:
        activities = [a for a in activities if a['contract'] == contract_number]
    return [{'id': a['id'], 'contract': a['contract'], 'workload': a['workload'],
             'start_date': a['start_date'], 'overrun_days': a['overrun_days']} for a in activities]


# ---------------------------------------------------------------------------
# 3. Gemini function-calling declarations
# ---------------------------------------------------------------------------

TOOL_DECLARATIONS: list[dict[str, Any]] = [
    {
        'name': 'add_activity',
        'description': (
            'Add a brand-new activity to a contract in the rail-access plan. '
            'Use this when the user wants to add/insert/create new work. This queues a '
            'real change against the scenario\'s plan and re-solves it -- it is not a preview.'
        ),
        'parameters': {
            'type': 'object',
            'properties': {
                'scenario': {'type': 'string', 'description': "Scenario to apply this to: 'A', 'B', or 'C'"},
                'activity_id': {'type': 'string', 'description': 'New unique activity id, e.g. A090'},
                'contract_number': {'type': 'string', 'description': 'Existing contract number, e.g. C001'},
                'activity_type': {'type': 'string', 'description': 'e.g. Renewal, Construction'},
                'start_location_id': {'type': 'string', 'description': 'Location id where the activity starts'},
                'end_location_id': {'type': 'string', 'description': 'Location id where the activity ends'},
                'total_accesses': {'type': 'integer', 'description': 'Total planned access nights'},
                'planned_start_date': {'type': 'string', 'description': 'ISO date, e.g. 2027-03-15'},
                'predecessor_activity_id': {'type': 'string', 'description': 'Optional predecessor activity id'},
                'activity_priority': {'type': 'integer', 'description': '1 (highest) to 3 (lowest)'},
            },
            'required': ['scenario', 'activity_id', 'contract_number', 'activity_type', 'start_location_id',
                         'end_location_id', 'total_accesses', 'planned_start_date'],
        },
    },
    {
        'name': 'edit_activity',
        'description': (
            'Change the total planned access nights and/or planned start date of an '
            'existing activity. Use when the user wants to modify/adjust a single activity. '
            'This queues a real change against the scenario\'s plan and re-solves it.'
        ),
        'parameters': {
            'type': 'object',
            'properties': {
                'scenario': {'type': 'string', 'description': "Scenario to apply this to: 'A', 'B', or 'C'"},
                'activity_id': {'type': 'string', 'description': 'Existing activity id, e.g. A007'},
                'total_accesses': {'type': 'integer', 'description': 'New total planned access nights'},
                'planned_start_date': {'type': 'string', 'description': 'Optional new ISO planned start date'},
            },
            'required': ['scenario', 'activity_id', 'total_accesses'],
        },
    },
    {
        'name': 'edit_contract',
        'description': (
            'Rebalance the total planned access nights across all of a contract\'s '
            'activities, and/or update its planned completion date. Use when the user '
            'talks about a whole contract rather than one activity. This queues a real '
            'change against the scenario\'s plan and re-solves it.'
        ),
        'parameters': {
            'type': 'object',
            'properties': {
                'scenario': {'type': 'string', 'description': "Scenario to apply this to: 'A', 'B', or 'C'"},
                'contract_number': {'type': 'string', 'description': 'Existing contract number, e.g. C001'},
                'total_accesses': {'type': 'integer', 'description': 'New total planned days for the contract'},
                'planned_completion_date': {'type': 'string', 'description': 'Optional new ISO completion date'},
            },
            'required': ['scenario', 'contract_number', 'total_accesses'],
        },
    },
    {
        'name': 'postpone_access',
        'description': (
            'Postpone a single already-scheduled access (one activity, one week, one '
            'access sequence) from the current live plan. Use when the user wants to '
            'delay, push back, or cancel a specific upcoming night without changing '
            'total workload. This queues a real change against the scenario\'s plan and '
            're-solves it, and requires that scenario to already be solved.'
        ),
        'parameters': {
            'type': 'object',
            'properties': {
                'scenario': {'type': 'string', 'description': "Scenario to apply this to: 'A', 'B', or 'C'"},
                'activity_id': {'type': 'string', 'description': 'Activity id whose access is postponed'},
                'week': {'type': 'integer', 'description': 'Week number of the access being postponed'},
                'access_seq': {'type': 'integer', 'description': 'Access sequence number within that activity'},
                'as_of_date': {'type': 'string', 'description': 'ISO date the change is being made, e.g. today'},
            },
            'required': ['scenario', 'activity_id', 'week', 'access_seq', 'as_of_date'],
        },
    },
    {
        'name': 'get_contract',
        'description': (
            'Look up one contract\'s scheduled stats in a given scenario: description, '
            'priority, due/completion dates, activity and access counts, overrun days. '
            'Use when the user asks about a specific contract by number.'
        ),
        'parameters': {
            'type': 'object',
            'properties': {
                'scenario': {'type': 'string', 'description': "Scenario letter: 'A', 'B', or 'C'"},
                'contract_number': {'type': 'string', 'description': 'Existing contract number, e.g. C001'},
            },
            'required': ['scenario', 'contract_number'],
        },
    },
    {
        'name': 'get_activity',
        'description': (
            'Look up one activity\'s scheduled nights and stats in a given scenario: dates, '
            'workload, priority, locations, overrun days. Use when the user asks about a '
            'specific activity by id.'
        ),
        'parameters': {
            'type': 'object',
            'properties': {
                'scenario': {'type': 'string', 'description': "Scenario letter: 'A', 'B', or 'C'"},
                'activity_id': {'type': 'string', 'description': 'Existing activity id, e.g. A007'},
            },
            'required': ['scenario', 'activity_id'],
        },
    },
    {
        'name': 'get_week_schedule',
        'description': (
            'List every scheduled access (activity, contract, ECLO flag) occurring in one '
            'week number of a given scenario. Use when the user asks what is happening in a '
            'specific week.'
        ),
        'parameters': {
            'type': 'object',
            'properties': {
                'scenario': {'type': 'string', 'description': "Scenario letter: 'A', 'B', or 'C'"},
                'week': {'type': 'integer', 'description': '1-based week number within the horizon'},
            },
            'required': ['scenario', 'week'],
        },
    },
    {
        'name': 'list_activities',
        'description': (
            'List activity ids and short summaries in a given scenario, optionally filtered '
            'to one contract. Use this to find an exact activity id before calling '
            'get_activity, or to see everything under a contract.'
        ),
        'parameters': {
            'type': 'object',
            'properties': {
                'scenario': {'type': 'string', 'description': "Scenario letter: 'A', 'B', or 'C'"},
                'contract_number': {'type': 'string', 'description': 'Optional contract number to filter by'},
            },
            'required': ['scenario'],
        },
    },
]

# Tool names that only read the already-solved schedule and never touch the
# filesystem or apply a change. A caller running a multi-turn tool loop can
# execute these automatically and keep going; the rest are one-shot changes.
READ_TOOL_NAMES = frozenset({'get_contract', 'get_activity', 'get_week_schedule', 'list_activities'})


# ---------------------------------------------------------------------------
# 4. Dispatcher — routes a Gemini function-call (name + arguments) to the
#    matching callable above.
# ---------------------------------------------------------------------------


def dispatch_tool_call(name: str, arguments: dict[str, Any], *, data_dir: Path | None = None,
                        inst: Instance | None = None, baseline: list[dict] | None = None,
                        schedule: dict[str, Any] | None = None) -> Any:
    """Execute the tool Gemini asked for and return its raw result.

    ``data_dir`` is required for ``add_activity``/``edit_activity``/``edit_contract``.
    ``inst`` and ``baseline`` are required for ``postpone_access``.
    ``schedule`` is required for the read-only lookups in :data:`READ_TOOL_NAMES`.
    """
    if name in READ_TOOL_NAMES:
        return {'get_contract': get_contract, 'get_activity': get_activity,
                'get_week_schedule': get_week_schedule,
                'list_activities': list_activities}[name](schedule, **arguments)
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
