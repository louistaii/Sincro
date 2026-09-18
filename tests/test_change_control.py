import csv
import datetime as dt
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from shutil import copytree

from sincro.change_control import apply_changes, build_schedule_constraints, parse_append_csv
from sincro.instance import load_instance
from sincro.optimal import solve_exact
from sincro.web import DATA


class ChangeDataTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.data = Path(self.temporary.name) / 'data'
        copytree(DATA, self.data)

    def activities(self):
        with (self.data / '08_ACTIVITY_DETAILS.csv').open(newline='', encoding='utf-8') as handle:
            return list(csv.DictReader(handle))

    def test_append_and_activity_and_contract_workload_edits(self):
        original_c002 = [row for row in self.activities() if row['contract_number'] == 'C002']
        target = sum(int(row['total_accesses']) for row in original_c002) + 2
        changes = [
            {'kind': 'append', 'activities': [{
                'activity_id': 'A999', 'contract_number': 'C001', 'activity_type': 'Renewal',
                'start_location_id': 'SEC:ALP:S01_S02:EB',
                'end_location_id': 'SEC:ALP:S01_S02:EB', 'total_accesses': '2',
                'planned_start_date': '2027-01-04', 'predecessor_activity_id': '',
                'activity_priority': '1',
            }]},
            {'kind': 'edit_activity', 'activity_id': 'A001', 'total_accesses': 4,
             'planned_start_date': '2027-02-01'},
            {'kind': 'edit_contract', 'contract_number': 'C002', 'total_accesses': target,
             'planned_completion_date': '2027-07-11'},
        ]
        notices = apply_changes(self.data, changes)
        instance = load_instance(self.data)
        self.assertEqual(instance.activities['A999'].total_accesses, 2)
        self.assertEqual(instance.activities['A001'].total_accesses, 4)
        self.assertEqual(instance.activities['A001'].planned_start_date, dt.date(2027, 2, 1))
        self.assertEqual(sum(a.total_accesses for a in instance.activities.values()
                             if a.contract_number == 'C002'), target)
        self.assertEqual(instance.contracts['C002'].planned_completion_date, dt.date(2027, 7, 11))
        self.assertEqual(len(notices), 3)

    def test_append_csv_requires_the_official_header(self):
        with self.assertRaisesRegex(ValueError, 'standard activity-details header'):
            parse_append_csv('activity_id,contract_number\nA999,C001\n')


class FreezePolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.instance = load_instance(DATA)

    def baseline(self):
        return [
            {'activity_id': 'A001', 'week': 1, 'eclo': 0, 'access_night': 1, 'access_seq': 1},
            {'activity_id': 'A002', 'week': 2, 'eclo': 0, 'access_night': 1, 'access_seq': 1},
            {'activity_id': 'A008', 'week': 1, 'eclo': 0, 'access_night': 1, 'access_seq': 1},
        ]

    def test_two_day_notice_protects_every_other_access(self):
        constraints, metadata = build_schedule_constraints(
            self.instance, self.baseline(),
            [{'kind': 'postpone', 'activity_id': 'A001', 'week': 1, 'access_seq': 1}],
            self.instance.horizon_start,
        )
        fixed = constraints['fixed']
        self.assertIsNone(fixed['A001', 1])
        self.assertEqual(fixed['A002', 2]['access_night'], 1)
        self.assertEqual(fixed['A008', 1]['eclo'], 0)
        self.assertEqual(metadata['relaxed_contracts'], [])

    def test_more_than_two_days_relaxes_only_the_same_contract(self):
        constraints, metadata = build_schedule_constraints(
            self.instance, self.baseline(),
            [{'kind': 'postpone', 'activity_id': 'A002', 'week': 2, 'access_seq': 1}],
            self.instance.horizon_start,
        )
        fixed = constraints['fixed']
        self.assertEqual(metadata['relaxed_contracts'], ['C001'])
        self.assertIsNotNone(fixed['A001', 1])
        self.assertIsNone(fixed['A002', 2])
        self.assertIsNotNone(fixed['A008', 1])

    def test_exact_solver_honours_a_protected_week_and_night(self):
        activity = replace(self.instance.activities['A001'], total_accesses=1,
                           planned_start_date=self.instance.horizon_start,
                           predecessor_activity_id=None)
        contract = self.instance.contracts[activity.contract_number]
        mini = replace(self.instance, activities={activity.activity_id: activity},
                       contracts={contract.contract_number: contract}, horizon_weeks=3,
                       _line_sectors={})
        result = solve_exact(
            mini, 'A', secondary_seconds=1.0,
            schedule_constraints={'fixed': {(activity.activity_id, 1): None,
                                             (activity.activity_id, 2): {
                                                 'eclo': 0, 'access_night': 2}}},
        )
        self.assertEqual(result['placements'][0][2:], (2, 0, 2))


if __name__ == '__main__':
    unittest.main()
