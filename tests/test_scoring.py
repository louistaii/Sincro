"""Contract completion drives the organiser-compatible penalty."""
from __future__ import annotations

import tempfile
import importlib.util
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from sincro.analyse import score_from_schedule
from sincro.construct import objective, score
from sincro.emit import write_submission
from sincro.instance import Activity, Contract, load_instance
from sincro.validate import validate


ROOT = Path(__file__).resolve().parents[1]


class ContractCompletionScoringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        public = load_instance(ROOT / '01_data')
        due = public.week_end(1)
        contract = Contract('C1', 'Scoring fixture', 'Works', 'Non-live (Others)',
                            2, due, due, 1, 'C', 3)
        location = 'SEC:ALP:S01_S02:EB'
        activities = {
            aid: Activity(aid, 'C1', 'Works', location, location, 1,
                          public.week_start(1), None, priority)
            for aid, priority in (('early', 1), ('late', 3))
        }
        cls.inst = replace(public, contracts={'C1': contract}, activities=activities,
                           horizon_weeks=3, supply=dict(public.supply), _line_sectors={})
        cls.inst.check()

    def candidate(self, *, early_finish=1, late_finish=2, eclo=False):
        finish = {'early': early_finish, 'late': late_finish}
        placements = [(aid, 1, week, int(eclo and aid == 'early'), 1)
                      for aid, week in finish.items()]
        return {'finish_week': finish, 'placements': placements,
                'group_of': {(aid, week): i for i, (aid, week) in enumerate(finish.items())},
                'unfinished': {}, 'excess': 0}

    def report(self, result, scenario='A'):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            write_submission(self.inst, result, directory, scenario)
            return validate(self.inst, directory, scenario)

    def test_early_sibling_inherits_contract_delay_in_every_scorer(self):
        result = self.candidate()
        constructed = score(self.inst, result['finish_week'])
        analysed = score_from_schedule(
            self.inst, {aid: (week, week) for aid, week in result['finish_week'].items()})
        report = self.report(result)
        self.assertTrue(report['feasible'], report['hard_violations'])

        # The contract is 7 days late: both activity weights apply, even though
        # the priority-1 sibling finished on time: (13 + 10) * 7 = 161.
        for scored in (constructed, analysed, report['soft_scores']):
            self.assertEqual(scored['priority_weighted_score'], 161)
            self.assertEqual(scored['activity_finish_weighted_score'], 70)
        self.assertEqual(constructed['priority_overrun'], {2: 14})
        self.assertEqual(analysed['priority_overrun'], {2: 14})
        self.assertEqual(report['soft_scores']['priority_overrun'], {'2': 14})
        self.assertEqual(report['soft_scores']['overrun_days_total'], 7)
        self.assertEqual(report['soft_scores']['contracts_overrunning'], 1)
        self.assertEqual(report['soft_scores']['objective_score'], 161)
        self.assertEqual(report['soft_scores']['formula_version'], 'organiser-inferred-contract-v3')
        self.assertEqual(objective(self.inst, result, 'A'), 161)
        self.assertEqual({row[0]: row[-1] for row in constructed['rows']},
                         {'early': 91, 'late': 70})

    def test_finishing_sibling_earlier_changes_diagnostic_only(self):
        early = score(self.inst, {'early': 1, 'late': 3})
        later = score(self.inst, {'early': 2, 'late': 3})
        self.assertEqual(early['priority_weighted_score'], 322)
        self.assertEqual(later['priority_weighted_score'], 322)
        self.assertEqual(early['activity_finish_weighted_score'], 140)
        self.assertEqual(later['activity_finish_weighted_score'], 231)

    def test_c_adds_eclo_cost_to_contract_completion_penalty(self):
        result = self.candidate(eclo=True)
        report = self.report(result, 'C')
        self.assertTrue(report['feasible'], report['hard_violations'])
        self.assertEqual(report['soft_scores']['priority_weighted_score'], 161)
        self.assertEqual(report['soft_scores']['objective_score'], 166)
        self.assertEqual(objective(self.inst, result, 'C'), 166)

    def test_b_lateness_still_fails_before_objective_is_awarded(self):
        report = self.report(self.candidate(), 'B')
        self.assertFalse(report['feasible'])
        self.assertEqual(report['soft_scores']['priority_weighted_score'], 161)
        self.assertNotIn('objective_score', report['soft_scores'])
        self.assertTrue(any(item['rule'] == 'planned_date' for item in report['hard_violations']))

    def test_analysis_week_start_sensitivity_uses_same_contract_finish(self):
        result = score_from_schedule(self.inst, {'early': (1, 1), 'late': (2, 2)},
                                     use_week_end=False)
        self.assertEqual(result['priority_weighted_score'], 23)
        self.assertEqual(result['activity_finish_weighted_score'], 10)
        self.assertEqual(result['overrun_days_total'], 1)

    @unittest.skipUnless(importlib.util.find_spec('ortools'), 'OR-Tools is not installed')
    def test_exact_model_prices_early_sibling_at_contract_completion(self):
        from sincro.optimal import solve_exact

        result = solve_exact(self.inst, 'A', secondary_seconds=0.1,
                             schedule_constraints={'fixed': {
                                 ('early', 1): {'eclo': 0, 'access_night': 1},
                                 ('late', 2): {'eclo': 0, 'access_night': 1},
                             }})
        self.assertTrue(result['proven'])
        self.assertEqual(result['objective'], 1610)
        self.assertEqual(result['best_bound'], 1610)
        report = self.report(result)
        self.assertTrue(report['feasible'], report['hard_violations'])
        self.assertEqual(report['soft_scores']['objective_score'], 161)

    @unittest.skipUnless(importlib.util.find_spec('ortools'), 'OR-Tools is not installed')
    def test_model_cannot_inflate_completion_before_optimality_is_proven(self):
        from ortools.sat.python import cp_model
        from sincro.optimal import POLICIES, _build

        model, _, _, parts = _build(
            self.inst, POLICIES['A'], 3, self.inst.horizon_start,
            {'fixed': {('late', 2): {'eclo': 0, 'access_night': 1}}}, compact=True)
        # One required night is fixed at week 2. Even an arbitrary feasible
        # incumbent must report week 2, rather than an inflated finish date.
        model.add(parts['finish_vars']['late'] == 3)
        solver = cp_model.CpSolver()
        self.assertEqual(solver.solve(model), cp_model.INFEASIBLE)

    @unittest.skipUnless(importlib.util.find_spec('ortools'), 'OR-Tools is not installed')
    def test_export_rejects_scoring_drift_before_replacing_existing_files(self):
        from sincro.construct import SchedulingError
        from sincro.emit import emit

        result = {**self.candidate(), 'objective': 700, 'proven': True}
        with tempfile.TemporaryDirectory() as temp:
            existing = Path(temp) / 'RESULTS.csv'
            existing.write_text('keep existing answer key')
            with patch('sincro.emit.load_instance', return_value=self.inst), \
                    patch('sincro.emit._solve_best', return_value=(result, 'exact')):
                with self.assertRaisesRegex(SchedulingError, 'differs from validated score'):
                    emit('unused', temp, 'A', optimal=True)
            self.assertEqual(existing.read_text(), 'keep existing answer key')


if __name__ == '__main__':
    unittest.main()
