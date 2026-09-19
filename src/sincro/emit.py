"""Generate and validate all three CSVs before publishing an answer key."""
from __future__ import annotations

import argparse
import csv
import json
import shutil
import tempfile
from pathlib import Path

from .construct import SchedulingError, solve
from .instance import Instance, load_instance
from .validate import SCHEMAS, validate


def write_submission(inst: Instance, result: dict, out: Path, scenario: str) -> None:
    """Serialize a complete candidate. Call validate before treating it as feasible."""
    if result.get('unfinished') or set(result['finish_week']) != set(inst.activities):
        raise SchedulingError('Refusing to write an incomplete workload')
    out.mkdir(parents=True, exist_ok=True)
    rows = {'SCHEDULE_ACCESS.csv': result['placements'], 'SCHEDULE_OCCUPANCY.csv': [], 'RESULTS.csv': []}
    for aid, _, week, _, _ in result['placements']:
        group = result['group_of'][(aid, week)]
        for loc in inst.span_locations(inst.activities[aid]):
            rows['SCHEDULE_OCCUPANCY.csv'].append((aid, week, loc, f'b{group + 1}'))
    for cn, contract in sorted(inst.contracts.items()):
        fin = max(wk for aid, wk in result['finish_week'].items() if inst.activities[aid].contract_number == cn)
        date = inst.week_end(fin)
        rows['RESULTS.csv'].append((scenario, cn, date.isoformat(), max(0, (date - contract.planned_completion_date).days)))
    for filename, header in SCHEMAS.items():
        with (out / filename).open('w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f, lineterminator='\n')
            writer.writerow(header)
            writer.writerows(rows[filename])


def _solve_best(inst: Instance, scenario: str, optimal: bool,
                primary_seconds: float | None,
                schedule_constraints: dict | None = None,
                require_proof: bool = False) -> tuple[dict, str]:
    """Prove the optimum when asked and able; otherwise schedule heuristically.

    The brief forbids ever declaring a case impossible, so an exact solve that
    fails -- unavailable, out of time, or infeasible even after the horizon is
    extended -- degrades to the heuristic rather than raising.
    """
    if not optimal:
        return solve(inst, scenario), 'heuristic'
    try:
        from .optimal import ExactSolveFailed, solve_exact
    except ImportError as exc:
        if schedule_constraints or require_proof:
            raise RuntimeError('Precision optimisation requires OR-Tools') from exc
        return solve(inst, scenario), 'heuristic (OR-Tools not installed)'
    try:
        result = solve_exact(inst, scenario, primary_seconds=primary_seconds,
                             schedule_constraints=schedule_constraints)
    except ExactSolveFailed:
        if schedule_constraints or require_proof:
            raise
        return solve(inst, scenario), 'heuristic (no exact schedule found)'
    if require_proof and (not result.get('proven') or not result.get('global_proven')):
        raise RuntimeError('Precision optimisation did not prove the global optimum')
    return result, 'exact' if result.get('proven') else 'exact (not proven optimal)'


def emit(data_dir: str, out_dir: str, scenario: str = 'A', *, optimal: bool = False,
         primary_seconds: float | None = None,
         schedule_constraints: dict | None = None,
         require_proof: bool = False) -> dict:
    inst = load_instance(data_dir)
    if require_proof and not optimal:
        raise ValueError('A proof can only be required from the exact optimiser')
    if schedule_constraints and not optimal:
        raise ValueError('Controlled replanning requires the exact optimiser')
    result, solver = _solve_best(
        inst, scenario, optimal, primary_seconds, schedule_constraints, require_proof)
    with tempfile.TemporaryDirectory(prefix='sincro-') as temporary:
        staging = Path(temporary)
        write_submission(inst, result, staging, scenario)
        report = validate(inst, staging, scenario)
        if not report['feasible']:
            raise SchedulingError(f"Candidate failed local validation: {report['hard_violations'][:5]}")
        if 'objective' in result:
            from .optimal import SCALE

            value = result['objective'] / SCALE
            checked = report['soft_scores']['objective_score']
            if abs(value - checked) > 0.05:
                raise SchedulingError(
                    f'Solver objective {value} differs from validated score {checked}')
            bound = result.get('best_bound')
            bound = bound / SCALE if bound is not None else None
            report['optimisation'] = {
                'proven': result['proven'],
                'objective': checked,
                'lower_bound': bound,
                'absolute_gap': round(max(0.0, checked - bound), 1) if bound is not None else None,
                'horizon_weeks': result.get('horizon_weeks', inst.horizon_weeks),
                'primary_seconds': result.get('primary_wall_seconds'),
                'priority_proven': result.get('priority_proven', False),
                'global_proven': result.get('global_proven', False),
                'certificate_horizon_weeks': result.get('certificate_horizon_weeks'),
                'scope': ('local model with extended-horizon improvement certificate; '
                          'each activity charged for its own late days'),
            }
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        for name in SCHEMAS:
            shutil.copyfile(staging / name, out / name)
    report['solver'] = solver
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('data_dir')
    parser.add_argument('out_dir')
    parser.add_argument('scenario', choices=['A', 'B', 'C', 'all'], nargs='?', default='A')
    parser.add_argument('--optimal', action='store_true',
                        help='optimise activity delay penalties, then improve the priority tie-break')
    args = parser.parse_args()
    try:
        for scenario in ('A', 'B', 'C') if args.scenario == 'all' else (args.scenario,):
            out = str(Path(args.out_dir) / scenario) if args.scenario == 'all' else args.out_dir
            report = emit(args.data_dir, out, scenario, optimal=args.optimal)
            print(json.dumps({'output': out, 'feasible': report['feasible'],
                              'solver': report['solver'], **report['soft_scores']}))
    except (OSError, ValueError, KeyError, csv.Error) as exc:
        parser.exit(1, f'Scheduling failed: {exc}\n')


if __name__ == '__main__':
    main()
