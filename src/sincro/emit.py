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


def emit(data_dir: str, out_dir: str, scenario: str = 'A') -> dict:
    inst = load_instance(data_dir)
    result = solve(inst, scenario)
    with tempfile.TemporaryDirectory(prefix='sincro-') as temporary:
        staging = Path(temporary)
        write_submission(inst, result, staging, scenario)
        report = validate(inst, staging, scenario)
        if not report['feasible']:
            raise SchedulingError(f"Candidate failed local validation: {report['hard_violations'][:5]}")
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        for name in SCHEMAS:
            shutil.copyfile(staging / name, out / name)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('data_dir')
    parser.add_argument('out_dir')
    parser.add_argument('scenario', choices=['A', 'B', 'C', 'all'], nargs='?', default='A')
    args = parser.parse_args()
    try:
        for scenario in ('A', 'B', 'C') if args.scenario == 'all' else (args.scenario,):
            out = str(Path(args.out_dir) / scenario) if args.scenario == 'all' else args.out_dir
            report = emit(args.data_dir, out, scenario)
            print(json.dumps({'output': out, 'feasible': report['feasible'], **report['soft_scores']}))
    except (OSError, ValueError, KeyError, csv.Error) as exc:
        parser.exit(1, f'Scheduling failed: {exc}\n')


if __name__ == '__main__':
    main()
