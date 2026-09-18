"""Adversarial fixtures for the hard rules, not just solver self-validation."""
import csv
import importlib.util
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from sincro.construct import SchedulingError, Week, construct, objective, solve
from sincro.emit import emit, write_submission
from sincro.instance import Activity, Contract, load_instance
from sincro.validate import SCHEMAS, validate

ROOT = Path(__file__).resolve().parents[1]
PUBLIC = load_instance(ROOT / '01_data')
LOC = 'SEC:ALP:S01_S02:EB'


def contract(cn='C1', *, nature='Non-live (Others)', kind='C', priority=3, fronts=1, nights=3, deadline=20):
    return Contract(cn, 'Fixture', 'Works', nature, priority, PUBLIC.week_end(deadline),
                    PUBLIC.week_end(deadline), fronts, kind, nights)


def activity(aid='A1', cn='C1', *, loc=LOC, end=None, total=1, start=1, pred=None, priority=3):
    return Activity(aid, cn, 'Works', loc, end or loc, total, PUBLIC.week_start(start), pred, priority)


def instance(contracts=None, activities=None, horizon=2, capacity=None):
    inst = replace(PUBLIC, contracts={c.contract_number: c for c in (contracts or [contract()])},
                   activities={a.activity_id: a for a in (activities or [activity()])},
                   horizon_weeks=horizon, supply=dict(PUBLIC.supply), _line_sectors={})
    if capacity is not None:
        inst.supply = {loc: capacity for loc in inst.supply}
    inst.check()
    return inst


def candidate(inst, rows, labels=None):
    """Rows are (activity, week, ECLO, local night); groups default to separate."""
    seq, fin, placements, groups = {}, {}, [], {}
    for i, (aid, week, eclo, night) in enumerate(rows):
        seq[aid] = seq.get(aid, 0) + 1
        fin[aid] = max(fin.get(aid, 0), week)
        placements.append((aid, seq[aid], week, eclo, night))
        groups[(aid, week)] = labels[i] if labels is not None else i
    return {'placements': placements, 'finish_week': fin, 'group_of': groups, 'unfinished': {}}


def closure_pair(kind='C'):
    """The reported boundary-platform clash, with one access per activity."""
    acts = [replace(PUBLIC.activities[aid], total_accesses=1,
                    planned_start_date=PUBLIC.week_start(8)) for aid in ('A037', 'A061')]
    contracts = [replace(PUBLIC.contracts[a.contract_number], access_type=kind) for a in acts]
    return instance(contracts, acts, horizon=9)


class SubmissionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.out = Path(self.tmp.name)

    def publish(self, inst, rows, scenario='A', labels=None):
        write_submission(inst, candidate(inst, rows, labels), self.out, scenario)
        return validate(inst, self.out, scenario)

    def edit(self, name, change):
        path = self.out / name
        with path.open(newline='') as f:
            rows = list(csv.DictReader(f))
        change(rows)
        with path.open('w', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=SCHEMAS[name])
            writer.writeheader()
            writer.writerows(rows)

    def rules(self, report):
        return {v['rule'] for v in report['hard_violations']}

    def test_empty_occupancy_and_missing_results_cannot_pass(self):
        inst = instance()
        self.assertTrue(self.publish(inst, [('A1', 1, 0, 1)])['feasible'])
        self.edit('SCHEDULE_OCCUPANCY.csv', lambda rows: rows.clear())
        self.assertIn('occupancy', self.rules(validate(inst, self.out)))
        (self.out / 'RESULTS.csv').unlink()
        self.assertIn('schema', self.rules(validate(inst, self.out)))

    def test_missing_endpoint_platform_and_orphan_occupancy(self):
        inst = instance()
        self.publish(inst, [('A1', 1, 0, 1)])
        self.edit('SCHEDULE_OCCUPANCY.csv', lambda rows: rows.pop(0))
        self.assertIn('occupancy', self.rules(validate(inst, self.out)))
        self.edit('SCHEDULE_OCCUPANCY.csv', lambda rows: rows[0].update(week='2'))
        self.assertIn('occupancy', self.rules(validate(inst, self.out)))

    def test_invalid_access_values_report_errors_without_crashing(self):
        inst = instance()
        for column, value, rule in [('activity_id', 'unknown', 'schema'), ('eclo', '2', 'schema'),
                                    ('week', '0', 'schema'), ('access_seq', '0', 'schema'),
                                    ('week', 'abc', 'schema'), ('access_night', '0', 'allocation'),
                                    ('access_night', '4', 'allocation')]:
            with self.subTest(column=column, value=value):
                self.publish(inst, [('A1', 1, 0, 1)])
                self.edit('SCHEDULE_ACCESS.csv', lambda rows: rows[0].update({column: value}))
                report = validate(inst, self.out)
                self.assertIn(rule, self.rules(report))
                self.assertNotIn('objective_score', report['soft_scores'])

    def test_sequences_duplicates_and_workload_gate(self):
        inst = instance(activities=[activity(total=2)])
        self.publish(inst, [('A1', 1, 0, 1), ('A1', 2, 0, 1)])
        self.edit('SCHEDULE_ACCESS.csv', lambda rows: rows[1].update(access_seq='1', week='1'))
        report = validate(inst, self.out)
        self.assertTrue({'sequence', 'multi_access_week'} <= self.rules(report))
        self.edit('SCHEDULE_ACCESS.csv', lambda rows: rows.pop())
        self.assertIn('workload', self.rules(validate(inst, self.out)))

    def test_results_are_verified_and_scenario_is_inferred(self):
        inst = instance()
        self.publish(inst, [('A1', 1, 0, 1)])
        self.assertEqual(validate(inst, self.out)['scenario'], 'A')
        self.assertFalse(validate(inst, self.out, 'B')['feasible'])
        self.edit('RESULTS.csv', lambda rows: rows[0].update(overrun_days='50'))
        self.assertIn('results', self.rules(validate(inst, self.out)))
        self.edit('RESULTS.csv', lambda rows: rows.append({**rows[0], 'scenario': 'B'}))
        self.assertIn('schema', self.rules(validate(inst, self.out)))

    def test_start_and_cross_contract_precedence(self):
        inst = instance([contract(), contract('C2')], [activity(start=2), activity('A2', 'C2', pred='A1')])
        report = self.publish(inst, [('A1', 1, 0, 1), ('A2', 1, 0, 1)])
        self.assertTrue({'planned_start', 'precedence'} <= self.rules(report))

    def test_legal_mix_and_scenario_capacity_policies(self):
        inst = instance([contract('C1', kind='PM'), contract('C2', kind='PM'), contract('C3', kind='PM')],
                        [activity('A1', 'C1'), activity('A2', 'C2'), activity('A3', 'C3')], capacity=1)
        rows = [(aid, 1, 0, 1) for aid in inst.activities]
        for scenario, capacity_violation in [('A', True), ('B', False), ('C', True)]:
            report = self.publish(inst, rows, scenario)
            self.assertEqual('capacity' in self.rules(report), capacity_violation, report)
            self.assertIn('closure', self.rules(report))
            self.assertEqual(report['soft_scores']['excess_access_nights_total'], 6)
        report = self.publish(inst, rows, 'B', labels=[0, 0, 0])
        self.assertIn('mix', self.rules(report))
        # One possession above zero nominal supply exercises elasticity
        # independently of overlapping closures.
        inst = instance(capacity=0)
        for scenario, feasible in [('A', False), ('B', True), ('C', True)]:
            report = self.publish(inst, rows[:1], scenario)
            self.assertEqual(report['feasible'], feasible, report)
            self.assertEqual(report['soft_scores']['excess_access_nights_total'], 3)

    def test_buffer_free_boundary_platform_requires_co_sharing_in_every_scenario(self):
        inst = closure_pair()
        rows = [('A037', 8, 0, 1), ('A061', 8, 0, 2)]
        for scenario in 'ABC':
            with self.subTest(scenario=scenario):
                report = self.publish(inst, rows, scenario)
                closures = [v for v in report['hard_violations'] if v['rule'] == 'closure']
                self.assertEqual(len(closures), 1, report)
                for detail in ('wk8', 'A037', 'A061', 'PLAT:BET:S15:EB'):
                    self.assertIn(detail, closures[0]['detail'])
                self.assertNotIn('objective_score', report['soft_scores'])
                shared = self.publish(inst, rows, scenario, labels=[0, 0])
                self.assertTrue(shared['feasible'], shared)
                separate_weeks = self.publish(inst, [('A037', 8, 0, 1), ('A061', 9, 0, 2)], scenario)
                self.assertTrue(separate_weeks['feasible'], separate_weeks)

    def test_pc_plus_c_shares_one_slot_but_two_pcs_do_not(self):
        inst = instance([contract(kind='PC'), contract('C2')], [activity(), activity('A2', 'C2')], capacity=1)
        rows = [('A1', 1, 0, 1), ('A2', 1, 0, 1)]
        report = self.publish(inst, rows, labels=[0, 0])
        self.assertTrue(report['feasible'], report)
        inst.contracts['C2'] = replace(inst.contracts['C2'], access_type='PC')
        self.assertIn('mix', self.rules(self.publish(inst, rows, labels=[0, 0])))

    def test_fifth_co_worker_rejected(self):
        inst = instance([contract(f'C{i}') for i in range(5)], [activity(f'A{i}', f'C{i}') for i in range(5)])
        self.assertIn('mix', self.rules(self.publish(inst, [(a, 1, 0, 1) for a in inst.activities], labels=[0]*5)))

    def test_local_night_indices_and_workfronts(self):
        inst = instance(activities=[activity(), activity('A2', loc='SEC:ALP:S07_S08:EB')])
        self.assertIn('workfront', self.rules(self.publish(inst, [('A1', 1, 0, 1), ('A2', 1, 0, 1)])))
        self.assertTrue(self.publish(inst, [('A1', 1, 0, 1), ('A2', 1, 0, 2)])['feasible'])
        inst.activities['A2'] = replace(inst.activities['A2'], start_location_id=LOC, end_location_id=LOC)
        self.assertIn('workfront', self.rules(self.publish(inst, [('A1', 1, 0, 1), ('A2', 1, 0, 2)], labels=[0, 0])))

    def test_disjoint_reused_label_does_not_waive_buffer(self):
        inst = instance([contract(nature='Non-live (Consist)'), contract('C2')],
                        [activity(), activity('A2', 'C2', loc='SEC:ALP:S03_S04:EB')])
        report = self.publish(inst, [('A1', 1, 0, 1), ('A2', 1, 0, 1)], labels=[0, 0])
        self.assertIn('closure', self.rules(report))

    def test_live_mirrors_and_crosses_lines_only_for_live(self):
        contracts = [contract(nature='Live', kind='PM'), contract('C2')]
        acts = [activity(loc='SEC:ALP:H01_H02:EB'), activity('A2', 'C2', loc='SEC:BET:H01_H02:WB')]
        inst = instance(contracts, acts)
        rows = [('A1', 1, 0, 1), ('A2', 1, 0, 1)]
        self.assertIn('closure', self.rules(self.publish(inst, rows)))
        inst.contracts['C1'] = replace(inst.contracts['C1'], nature_of_activity='Non-live (Consist)')
        self.assertTrue(self.publish(inst, rows)['feasible'])

    def test_a_forbids_eclo_and_b_forbids_lateness(self):
        inst = instance([contract(deadline=1)])
        self.assertIn('eclo', self.rules(self.publish(inst, [('A1', 1, 1, 1)])))
        self.assertIn('planned_date', self.rules(self.publish(inst, [('A1', 2, 0, 1)], 'B')))

    def test_c_eclo_calendar_window_and_b_exemption(self):
        inst = instance(activities=[activity(total=3)])
        rows = [('A1', 1, 1, 1), ('A1', 3, 1, 1)]
        self.assertIn('eclo_window', self.rules(self.publish(inst, rows, 'C')))
        self.assertTrue(self.publish(inst, rows, 'B')['feasible'])
        self.assertTrue(self.publish(inst, [('A1', 1, 1, 1), ('A1', 2, 1, 1)], 'C')['feasible'])

    def test_c_has_independent_line_windows_and_live_must_fit_both(self):
        inst = instance([contract(), contract('C2')],
                        [activity(total=3), activity('A2', 'C2', total=3, loc='SEC:BET:S17_S18:EB')])
        rows = [('A1', 1, 1, 1), ('A1', 2, 1, 1), ('A2', 5, 1, 1), ('A2', 6, 1, 1)]
        self.assertTrue(self.publish(inst, rows, 'C')['feasible'])
        inst.contracts['C1'] = replace(inst.contracts['C1'], nature_of_activity='Live')
        inst.activities['A1'] = replace(inst.activities['A1'], start_location_id='SEC:ALP:H01_H02:EB',
                                        end_location_id='SEC:ALP:H01_H02:EB')
        self.assertIn('eclo_window', self.rules(self.publish(inst, rows, 'C')))

    def test_priority_score_uses_contract_band_and_real_earliness(self):
        inst = instance([contract(priority=1, deadline=1), contract('C2', priority=3, deadline=3)],
                        [activity(priority=3), activity('A2', 'C2', priority=1, loc='SEC:ALP:S07_S08:EB')])
        report = self.publish(inst, [('A1', 2, 0, 1), ('A2', 1, 0, 1)])
        self.assertEqual(report['soft_scores']['objective_score'], 700)
        self.assertEqual(report['soft_scores']['priority_overrun'], {'1': 7})
        self.assertEqual(report['soft_scores']['earliness_days_total'], 14)

    def test_invalid_cli_returns_nonzero(self):
        self.publish(instance(), [('A1', 1, 0, 1)])
        self.edit('SCHEDULE_OCCUPANCY.csv', lambda rows: rows.clear())
        proc = subprocess.run([sys.executable, '-m', 'sincro.validate', str(ROOT/'01_data'), str(self.out)],
                              capture_output=True, text=True)
        self.assertEqual(proc.returncode, 1)


class ConstructionTests(unittest.TestCase):
    def test_buffer_free_closure_cannot_be_bypassed_by_extra_capacity(self):
        for scenario, extra in [('A', 0), ('B', None), ('C', 1)]:
            for kind in ('C', 'PM'):
                with self.subTest(scenario=scenario, kind=kind):
                    inst = closure_pair(kind)
                    week = Week(inst, 8, extra_capacity=extra)
                    self.assertIsNotNone(week.try_place('A037'))
                    second = week.try_place('A061')
                    if kind == 'C':
                        self.assertIsNotNone(second)
                        self.assertEqual(week.group_of['A037'], week.group_of['A061'])
                    else:
                        self.assertIsNone(second)
                    result, _ = construct(inst, scenario, extra_capacity=extra)
                    weeks = {aid: week for aid, _, week, _, _ in result['placements']}
                    self.assertEqual(weeks['A037'] == weeks['A061'], kind == 'C')

    def test_inclusive_and_reversed_spans_and_platform_only_work(self):
        inst = instance()
        self.assertEqual(set(inst.span_locations(activity())), {LOC, 'PLAT:ALP:S01:EB', 'PLAT:ALP:S02:EB'})
        forward = activity(end='SEC:ALP:S03_S04:EB')
        reverse = replace(forward, start_location_id=forward.end_location_id, end_location_id=forward.start_location_id)
        self.assertEqual(inst.span_locations(forward), inst.span_locations(reverse))
        platform = activity(loc='PLAT:ALP:S03:WB')
        self.assertEqual(inst.span_locations(platform), ['PLAT:ALP:S03:WB'])
        self.assertEqual(inst.closure_footprint(platform), {'PLAT:ALP:S03:WB'})

    def test_full_workload_extends_past_horizon_and_waits_for_predecessor(self):
        inst = instance([contract(), contract('C2')], [activity(total=4), activity('A2', 'C2', total=2, pred='A1')], horizon=1)
        result, unfinished = construct(inst)
        self.assertFalse(unfinished)
        self.assertEqual(result['finish_week'], {'A1': 4, 'A2': 6})
        self.assertEqual(len(result['placements']), 6)

    def test_future_release_is_not_truncated(self):
        inst = instance(activities=[activity(start=10, total=2)], horizon=1)
        self.assertEqual(construct(inst)[0]['finish_week']['A1'], 11)

    def test_cycles_and_missing_predecessors_fail_before_scheduling(self):
        inst = instance(activities=[activity(), activity('A2')])
        inst.activities['A1'] = replace(inst.activities['A1'], predecessor_activity_id='A2')
        inst.activities['A2'] = replace(inst.activities['A2'], predecessor_activity_id='A1')
        with self.assertRaisesRegex(ValueError, 'cycle'):
            construct(inst)
        inst.activities['A2'] = replace(inst.activities['A2'], predecessor_activity_id='unknown')
        with self.assertRaisesRegex(ValueError, 'unknown predecessor'):
            construct(inst)

    def test_zero_supply_does_not_hang(self):
        with self.assertRaises(SchedulingError):
            solve(instance(capacity=0), 'A')
        self.assertEqual(objective(instance(capacity=0), solve(instance(capacity=0), 'B'), 'B'), 21)

    def test_b_uses_only_necessary_eclo(self):
        inst = instance([contract(deadline=2)], [activity(total=3)])
        result = solve(inst, 'B')
        self.assertEqual(result['finish_week']['A1'], 2)
        self.assertEqual(sum(p[3] for p in result['placements']), 2)
        self.assertEqual(sum(p[3] for p in solve(instance(), 'B')['placements']), 0)
        with self.assertRaises(SchedulingError):
            solve(instance([contract(deadline=1)], [activity(total=3)]), 'B')

    def test_c_can_buy_a_two_week_window_when_worthwhile(self):
        inst = instance([contract(priority=1, deadline=2)], [activity(total=3)])
        result = solve(inst, 'C')
        self.assertEqual(result['finish_week']['A1'], 2)
        self.assertEqual(objective(inst, result, 'C'), 10)

    def test_public_all_scenarios_conserve_work_and_validate(self):
        with tempfile.TemporaryDirectory() as tmp:
            for scenario in ('A', 'B', 'C'):
                with self.subTest(scenario=scenario):
                    report = emit(str(ROOT/'01_data'), str(Path(tmp)/scenario), scenario)
                    self.assertTrue(report['feasible'], report['hard_violations'])
                    self.assertEqual(set(p.name for p in (Path(tmp)/scenario).iterdir()), set(SCHEMAS))
                    if scenario == 'B':
                        self.assertEqual(report['soft_scores']['overrun_days_total'], 0)


@unittest.skipUnless(importlib.util.find_spec('ortools'), 'OR-Tools is not installed')
class ExactClosureTests(unittest.TestCase):
    def test_each_policy_requires_a_legal_shared_possession_at_the_boundary(self):
        from ortools.sat.python import cp_model
        from sincro.optimal import POLICIES, _build

        for scenario in 'ABC':
            for kind, second_group, feasible in [('C', 1, False), ('C', 0, True), ('PM', 0, False)]:
                with self.subTest(scenario=scenario, kind=kind, second_group=second_group):
                    inst = closure_pair(kind)
                    model, _, _, parts = _build(inst, POLICIES[scenario], 9, inst.horizon_start)
                    model.add(parts['normal']['A037', 8, 0] == 1)
                    model.add(parts['normal']['A061', 8, second_group] == 1)
                    solver = cp_model.CpSolver()
                    solver.parameters.max_time_in_seconds = 10
                    status = solver.solve(model)
                    self.assertEqual(status, cp_model.OPTIMAL if feasible else cp_model.INFEASIBLE)


if __name__ == '__main__':
    unittest.main()
