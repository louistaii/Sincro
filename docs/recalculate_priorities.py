"""Regenerate A/B/C and compare schedules under the confirmed activity formula.

Run from the repository root with PYTHONPATH=src. The report separates rescoring
from actual schedule improvement. Existing optimal outputs are replaced only
after the new candidates validate and are no worse on the confirmed objective.
"""
import json
import tempfile
from pathlib import Path
from shutil import copyfile

from sincro.emit import emit
from sincro.instance import load_instance
from sincro.validate import SCHEMAS, validate


def compare():
    root = Path(__file__).resolve().parents[1]
    data = root / '01_data'
    inst = load_instance(data)
    runs = []
    with tempfile.TemporaryDirectory() as tmp:
        for scenario in 'ABC':
            old = root / 'out' / f'optimal-{scenario.lower()}'
            baseline = validate(inst, old, scenario)
            if not baseline['feasible']:
                raise ValueError(baseline['hard_violations'])
            output = Path(tmp) / scenario
            candidate = emit(str(data), str(output), scenario,
                             optimal=True, require_proof=True)
            before = baseline['soft_scores']['objective_score']
            after = candidate['soft_scores']['objective_score']
            if after > before + 0.05:
                raise ValueError(f'{scenario} would regress from {before} to {after}')
            row = {'scenario': scenario, 'baseline_rescored': baseline['soft_scores'],
                   'candidate': candidate, 'actual_improvement': round(before - after, 1)}
            runs.append(row)
            print(json.dumps(row), flush=True)
        # Publish as a batch only after all three are certified and checked.
        for scenario in 'ABC':
            for filename in SCHEMAS:
                copyfile(Path(tmp) / scenario / filename,
                         root / 'out' / f'optimal-{scenario.lower()}' / filename)
    report = {'formula_version': 'activity-own-delay-v4',
              'daily_rate': 'contract_weight * (1 + activity_nudge)',
              'late_days': 'max(0, activity completion - contract planned completion)',
              'reference_validator_used': False, 'runs': runs}
    (root / 'out' / 'priority-recalculation.json').write_text(
        json.dumps(report, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    compare()
