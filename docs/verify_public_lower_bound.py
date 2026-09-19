"""Historical contract-final relaxation; not a current objective certificate.

The user has since confirmed activity-own-delay scoring. See
docs/PRIORITY_RECALCULATION.md for the current calculation and proof.

Run from the repository root:
    PYTHONPATH=src .venv/bin/python docs/verify_public_lower_bound.py

Only eight activities are retained. Capacity, workfronts, predecessors and all
other activities are omitted. Geometry and scoring constants remain shared with
the local model, so this does not certify organiser-validator equivalence.
"""
import json
from pathlib import Path

from ortools.sat.python import cp_model

from sincro.instance import load_instance
from sincro import rules


ACTIVITIES = ('A007', 'A001', 'A008', 'A017', 'A074', 'A036', 'A075', 'A059')
SCALE = 10


def verify(scenario, incumbent):
    inst = load_instance(Path(__file__).resolve().parents[1] / '01_data')
    weights = {
        cn: sum(round(SCALE * rules.CONTRACT_WEIGHT[c.contract_priority]
                      * (1 + rules.ACTIVITY_NUDGE[a.activity_priority]))
                for a in inst.activities.values() if a.contract_number == cn)
        for cn, c in inst.contracts.items()
    }
    # A strictly better full schedule must fit this horizon: no contract can
    # consume more than the entire target score in its own delay cost.
    target = round(SCALE * incumbent) - 1
    horizon = max(((c.planned_completion_date - inst.horizon_start).days + 1
                   + target // weights[cn]) // 7 for cn, c in inst.contracts.items())
    weeks = range(1, horizon + 1)
    model = cp_model.CpModel()
    normal, eclo, active, finish = {}, {}, {}, {}
    windows = {line: model.new_int_var(1, horizon, f'window_{line}') for line in inst.lines}
    for aid in ACTIVITIES:
        activity = inst.activities[aid]
        for week in weeks:
            normal[aid, week] = model.new_bool_var(f'n_{aid}_{week}')
            eclo[aid, week] = model.new_bool_var(f'e_{aid}_{week}')
            active[aid, week] = normal[aid, week] + eclo[aid, week]
            model.add(active[aid, week] <= 1)
            if week < inst.week_of(activity.planned_start_date):
                model.add(active[aid, week] == 0)
            if scenario == 'A':
                model.add(eclo[aid, week] == 0)
            for line in inst.affected_lines(activity):
                model.add(windows[line] <= week).only_enforce_if(eclo[aid, week])
                model.add(windows[line] >= week - rules.ECLO_WINDOW_WEEKS + 1
                          ).only_enforce_if(eclo[aid, week])
        model.add(sum(2 * normal[aid, week] + 3 * eclo[aid, week] for week in weeks)
                  == 2 * activity.total_accesses)
        finish[aid] = model.new_int_var(1, horizon, f'finish_{aid}')
        model.add_max_equality(finish[aid], [week * active[aid, week] for week in weeks])

    spans = {aid: set(inst.span_locations(inst.activities[aid])) for aid in ACTIVITIES}
    feet = {aid: inst.closure_footprint(inst.activities[aid]) for aid in ACTIVITIES}
    pairs = []
    for index, aid in enumerate(ACTIVITIES):
        for other in ACTIVITIES[index + 1:]:
            exclusive = any(rules.ACCESS_ROLES[inst.contracts[
                inst.activities[member].contract_number].access_type]['exclusive']
                            for member in (aid, other))
            if feet[aid] & feet[other] and (exclusive or not spans[aid] & spans[other]):
                pairs.append((aid, other))
                for week in weeks:
                    model.add(active[aid, week] + active[other, week] <= 1)

    terms = []
    for cn in sorted({inst.activities[aid].contract_number for aid in ACTIVITIES}):
        due = inst.contracts[cn].planned_completion_date
        fin = model.new_int_var(1, horizon, f'finish_{cn}')
        model.add_max_equality(fin, [finish[aid] for aid in ACTIVITIES
                                    if inst.activities[aid].contract_number == cn])
        late = model.new_int_var(0, max(0, (inst.week_end(horizon) - due).days), f'late_{cn}')
        model.add_max_equality(late, [0, 7 * fin - 1 - (due - inst.horizon_start).days])
        # Full contract weights are retained, but omitted siblings can only
        # increase actual contract completion. This remains a lower bound.
        terms.append(weights[cn] * late)
    model.minimize(sum(terms) + SCALE * rules.ECLO_NIGHT_WEIGHT * sum(eclo.values()))
    solver = cp_model.CpSolver()
    solver.parameters.num_search_workers = 2
    solver.parameters.max_time_in_seconds = 30
    status = solver.solve(model)
    return {
        'scenario': scenario, 'activities': list(ACTIVITIES),
        'conflicting_pairs': pairs, 'horizon': horizon,
        'status': solver.status_name(status),
        'lower_bound': solver.best_objective_bound / SCALE,
        'objective': solver.objective_value / SCALE if status in (cp_model.OPTIMAL, cp_model.FEASIBLE) else None,
        'solver_seconds': solver.wall_time,
    }


if __name__ == '__main__':
    print(json.dumps([verify('A', 1028.3), verify('C', 542.4)], indent=2))
