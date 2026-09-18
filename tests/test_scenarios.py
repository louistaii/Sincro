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
        "objective_score": 131.6,
        "overrun_days_total": 42,
        "excess_access_nights_total": 0,
        "eclo_nights_total": 0,
    },
    "B": {
        "objective_score": 50,
        "overrun_days_total": 0,
        "excess_access_nights_total": 0,
        "eclo_nights_total": 10,
    },
    "C": {
        "objective_score": 44.5,
        "overrun_days_total": 21,
        "excess_access_nights_total": 0,
        "eclo_nights_total": 4,
    },
}


class OptimalArtifactTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.instance = load_instance(ROOT / "01_data")

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
        from sincro.optimal import (
            solve_scenario_a,
            solve_scenario_b,
            solve_scenario_c,
        )

        instance = load_instance(ROOT / "01_data")
        cases = (
            ("A", solve_scenario_a, 1316.0, 192),
            ("B", solve_scenario_b, 500.0, 187),
            ("C", solve_scenario_c, 445.0, 190),
        )
        for scenario, solve, expected_objective, expected_nights in cases:
            with self.subTest(scenario=scenario):
                result = solve(instance)
                self.assertEqual(result["objective"], expected_objective)
                self.assertGreater(result["priority_objective"], 0)
                self.assertEqual(len(result["placements"]), expected_nights)
                self.assertEqual(len(result["finish_week"]), len(instance.activities))


if __name__ == "__main__":
    unittest.main()
