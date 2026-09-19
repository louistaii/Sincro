from __future__ import annotations

import csv
import copy
import datetime as dt
import importlib.util
import shutil
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from sincro.instance import load_instance
from sincro.priority import calculate_priority
from sincro.validate import validate


ROOT = Path(__file__).resolve().parents[1]
EXPECTED = {
    "A": {
        "objective_score": 222.6,
        "overrun_days_total": 49,
        "contracts_overrunning": 5,
        "excess_access_nights_total": 0,
        "eclo_nights_total": 0,
    },
    "B": {
        "objective_score": 60,
        "overrun_days_total": 0,
        "contracts_overrunning": 0,
        "excess_access_nights_total": 0,
        "eclo_nights_total": 12,
    },
    "C": {
        "objective_score": 135.5,
        "overrun_days_total": 28,
        "contracts_overrunning": 4,
        "excess_access_nights_total": 0,
        "eclo_nights_total": 4,
    },
}


class OptimalArtifactTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.instance = load_instance(ROOT / "01_data")

    def test_a_local_score_is_not_conflated_with_tester_matching_diagnostic(self) -> None:
        report = validate(self.instance, ROOT / "out" / "optimal-a", "A")
        self.assertTrue(report['feasible'], report['hard_violations'])
        scores = report['soft_scores']
        # The user reports 1028.3 externally for this completion pattern.
        # A matching diagnostic is evidence, not certified tester equivalence.
        self.assertEqual(scores['objective_score'], 222.6)
        self.assertEqual(scores['contract_finish_weighted_score'], 1028.3)
        self.assertEqual(scores['overrun_days_total'], 49)
        self.assertEqual(scores['contracts_overrunning'], 5)

    def test_all_optimal_artifacts_are_feasible_and_match_proven_scores(self) -> None:
        for scenario, expected in EXPECTED.items():
            with self.subTest(scenario=scenario):
                result = validate(
                    self.instance,
                    ROOT / "out" / f"optimal-{scenario.lower()}",
                    scenario,
                )
                self.assertTrue(result["feasible"], result["hard_violations"])
                for field, value in expected.items():
                    self.assertEqual(result["soft_scores"][field], value)

    def test_access_night_must_be_within_the_contract_limit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            submission = Path(temp_dir)
            source = ROOT / "out" / "optimal-a"
            for filename in ("SCHEDULE_ACCESS.csv", "SCHEDULE_OCCUPANCY.csv", "RESULTS.csv"):
                shutil.copy2(source / filename, submission / filename)

            access_file = submission / "SCHEDULE_ACCESS.csv"
            with access_file.open(newline="") as stream:
                reader = csv.DictReader(stream)
                rows = list(reader)
                fieldnames = reader.fieldnames
            self.assertIsNotNone(fieldnames)
            rows[0]["access_night"] = "99"
            with access_file.open("w", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(rows)

            result = validate(self.instance, submission, "A")
            self.assertFalse(result["feasible"])
            self.assertTrue(
                any(v["rule"] == "allocation" for v in result["hard_violations"]),
                result["hard_violations"],
            )


class PriorityCalculationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.instance = load_instance(ROOT / "01_data")
        cls.as_of = dt.date(2026, 9, 19)
        cls.aid = "A001"

    def _with_contract(self, **changes):
        instance = copy.deepcopy(self.instance)
        activity = instance.activities[self.aid]
        contract = instance.contracts[activity.contract_number]
        instance.contracts[activity.contract_number] = replace(contract, **changes)
        return instance

    def test_priority_increases_as_duration_consumes_due_date_runway(self) -> None:
        short = calculate_priority(
            self.instance, self.aid, "A", as_of_date=self.as_of, remaining_accesses=1
        )
        long = calculate_priority(
            self.instance, self.aid, "A", as_of_date=self.as_of, remaining_accesses=8
        )
        self.assertGreater(long.score, short.score)
        self.assertLess(long.slack_days, short.slack_days)

    def test_closer_due_date_and_higher_penalty_raise_priority(self) -> None:
        far = self._with_contract(planned_completion_date=self.as_of + dt.timedelta(days=280))
        near = self._with_contract(planned_completion_date=self.as_of + dt.timedelta(days=35))
        low = calculate_priority(far, self.aid, "A", as_of_date=self.as_of)
        urgent = calculate_priority(near, self.aid, "A", as_of_date=self.as_of)
        self.assertGreater(urgent.score, low.score)

        high_tier = self._with_contract(contract_priority=1)
        high = calculate_priority(high_tier, self.aid, "A", as_of_date=self.as_of)
        base = calculate_priority(self.instance, self.aid, "A", as_of_date=self.as_of)
        self.assertGreater(high.weekly_delay_penalty, base.weekly_delay_penalty)
        self.assertGreater(high.score, base.score)

    def test_activity_nature_access_and_scenario_are_explicit(self) -> None:
        instance = copy.deepcopy(self.instance)
        activity = instance.activities[self.aid]
        instance.activities[self.aid] = replace(activity, activity_priority=1)
        high_activity = calculate_priority(instance, self.aid, "A", as_of_date=self.as_of)
        base = calculate_priority(self.instance, self.aid, "A", as_of_date=self.as_of)
        self.assertGreater(high_activity.activity_multiplier, base.activity_multiplier)

        restricted = self._with_contract(nature_of_activity="Live", access_type="PM")
        restricted_score = calculate_priority(
            restricted, self.aid, "A", as_of_date=self.as_of
        )
        self.assertGreater(restricted_score.nature_multiplier, base.nature_multiplier)
        self.assertGreater(restricted_score.access_multiplier, base.access_multiplier)
        self.assertGreater(restricted_score.restriction_multiplier, base.restriction_multiplier)

        scores = {
            scenario: calculate_priority(
                self.instance, self.aid, scenario, as_of_date=self.as_of
            ).score
            for scenario in "ABC"
        }
        self.assertEqual(len(set(scores.values())), 3)


@unittest.skipUnless(importlib.util.find_spec("ortools"), "OR-Tools is not installed")
class ExactSolverTests(unittest.TestCase):
    def test_all_scenarios_are_proven_optimal(self) -> None:
        from sincro.optimal import solve_exact

        instance = load_instance(ROOT / "01_data")
        cases = (
            ("A", 2226.0, 192, 59),
            ("B", 600.0, 186, 30),
            ("C", 1355.0, 190, 47),
        )
        for scenario, expected_objective, expected_nights, certificate_horizon in cases:
            with self.subTest(scenario=scenario):
                result = solve_exact(instance, scenario, secondary_seconds=1)
                self.assertEqual(result["objective"], expected_objective)
                self.assertTrue(result["proven"])
                self.assertTrue(result["global_proven"])
                self.assertEqual(result["certificate_horizon_weeks"], certificate_horizon)
                self.assertEqual(result["best_bound"], expected_objective)
                self.assertGreater(result["priority_objective"], 0)
                self.assertEqual(len(result["placements"]), expected_nights)
                self.assertEqual(len(result["finish_week"]), len(instance.activities))


@unittest.skipUnless(importlib.util.find_spec("ortools"), "OR-Tools is not installed")
class ExactSolverPolicyTests(unittest.TestCase):
    """The exact solver must be instance-driven and must never give up."""

    def setUp(self) -> None:
        self.instance = load_instance(ROOT / "01_data")

    def test_tie_break_is_anchored_to_the_instance_not_the_clock(self) -> None:
        """Two runs on different days must produce the same schedule."""
        from sincro.optimal import _default_as_of

        self.assertEqual(_default_as_of(self.instance), self.instance.horizon_start)

    def test_possession_count_follows_supply_and_the_scenario_allowance(self) -> None:
        from sincro.optimal import POLICIES, _possessions_per_location

        cap = max(self.instance.supply.values())
        # A tolerates no excess, so it models exactly the nominal supply; C is
        # granted one extra possession per location-week and must be able to
        # use it.
        self.assertEqual(len(_possessions_per_location(self.instance, POLICIES["A"])), cap)
        self.assertEqual(len(_possessions_per_location(self.instance, POLICIES["C"])), cap + 1)
        self.assertGreater(len(_possessions_per_location(self.instance, POLICIES["B"])), cap)

    def test_congested_instance_extends_the_horizon_instead_of_failing(self) -> None:
        """Section 1: a congested case must still produce a schedule."""
        from dataclasses import replace

        from sincro.optimal import solve_exact

        squeezed = replace(self.instance, horizon_weeks=20, _line_sectors={})
        # The primary proof is the point of this test; the tie-break pass only
        # reorders equally-scoring schedules, so give it a token budget.
        result = solve_exact(squeezed, "A", secondary_seconds=1.0)
        self.assertTrue(result["extended"])
        self.assertGreater(result["horizon_weeks"], 20)
        # The declared horizon was an artificial cap, so the true optimum is
        # unchanged by lifting it.
        self.assertEqual(result["objective"], 2226.0)
        self.assertEqual(len(result["finish_week"]), len(squeezed.activities))
        # A tie-break that runs out of budget still returns the proven
        # schedule, so it must report that schedule's own priority value
        # rather than a nominal zero.
        self.assertGreater(result["priority_objective"], 0)


class FallbackTests(unittest.TestCase):
    def test_emit_falls_back_rather_than_raising(self) -> None:
        """An exact solve that cannot deliver must not sink the submission."""
        import tempfile

        from sincro import emit as emit_module

        instance = load_instance(ROOT / "01_data")
        result, label = emit_module._solve_best(instance, "A", optimal=False,
                                                primary_seconds=None)
        self.assertEqual(label, "heuristic")
        self.assertEqual(len(result["finish_week"]), len(instance.activities))
        del tempfile


if __name__ == "__main__":
    unittest.main()
