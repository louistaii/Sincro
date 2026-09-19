"""Keep deployed scoring aligned with contract-final lateness."""
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from sincro.construct import objective
from sincro.emit import write_submission
from sincro.instance import Activity, Contract, load_instance
from sincro.validate import validate


class PenaltyTests(unittest.TestCase):
    def test_early_siblings_contribute_and_c_adds_eclo(self):
        public = load_instance(Path(__file__).resolve().parents[1] / '01_data')
        due = public.week_end(1)
        location = 'SEC:ALP:S01_S02:EB'
        contract = Contract('C1', 'Scoring check', 'Works', 'Non-live (Others)',
                            2, due, due, 1, 'C', 3)
        activities = {
            aid: Activity(aid, 'C1', 'Works', location, location, 1,
                          public.week_start(1), None, priority)
            for aid, priority in (('early', 1), ('late', 3))
        }
        inst = replace(public, contracts={'C1': contract}, activities=activities,
                       horizon_weeks=3, _line_sectors={})
        for scenario, eclo, expected in [('A', 0, 161), ('C', 1, 166)]:
            with self.subTest(scenario=scenario), tempfile.TemporaryDirectory() as tmp:
                result = {'finish_week': {'early': 1, 'late': 2},
                          'placements': [('early', 1, 1, eclo, 1), ('late', 1, 2, 0, 1)],
                          'group_of': {('early', 1): 0, ('late', 2): 0},
                          'unfinished': {}, 'excess': 0}
                write_submission(inst, result, Path(tmp), scenario)
                report = validate(inst, tmp, scenario)
                self.assertTrue(report['feasible'], report['hard_violations'])
                scores = report['soft_scores']
                self.assertEqual(scores['overrun_days_total'], 7)
                self.assertEqual(scores['priority_weighted_score'], 161)
                self.assertEqual(scores['activity_finish_weighted_score'], 70)
                self.assertEqual(scores['objective_score'], expected)
                self.assertEqual(sum(scores['penalty_breakdown'].values()), expected)
                self.assertEqual(scores['formula_version'], 'contract-final-delay-v5')
                self.assertEqual(objective(inst, result, scenario), expected)


if __name__ == '__main__':
    unittest.main()
