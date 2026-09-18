"""Compare the reduced search model with the independent possession encoding."""
from __future__ import annotations

import importlib.util
import unittest
from dataclasses import replace
from pathlib import Path

from sincro.instance import Activity, Contract, load_instance


@unittest.skipUnless(importlib.util.find_spec("ortools"), "OR-Tools is not installed")
class CompactModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.public = load_instance(Path(__file__).resolve().parents[1] / "01_data")

    def fixture(self, *, same_contract=False, kind="C", capacity=2,
                buffered=False, second_nights=2):
        base = self.public
        nature = "Non-live (Consist)" if buffered else "Non-live (Others)"
        contracts = {
            cn: Contract(cn, "Fixture", "Works", nature, 3, base.week_end(4),
                         base.week_end(2), 1, kind if cn == "C1" else "C", nights)
            for cn, nights in (("C1", 1), ("C2", second_nights))
        }
        if same_contract:
            contracts.pop("C2")
        first = "SEC:ALP:S01_S02:EB"
        # The second buffered span is disjoint but inside the first's buffer.
        second = "SEC:ALP:S03_S04:EB" if buffered else first
        activities = {
            aid: Activity(aid, cn, "Works", loc, loc, 2, base.week_start(1), None, 2)
            for aid, cn, loc in (("A1", "C1", first),
                                 ("A2", "C1" if same_contract else "C2", second))
        }
        return replace(base, contracts=contracts, activities=activities, horizon_weeks=4,
                       supply={loc: capacity for loc in base.supply}, _line_sectors={})

    def optimum(self, instance, scenario, compact, fixed=None):
        from ortools.sat.python import cp_model
        from sincro.optimal import POLICIES, _build

        model, primary, _, _ = _build(
            instance, POLICIES[scenario], 4, instance.horizon_start,
            {"fixed": fixed} if fixed else None, compact=compact)
        model.minimize(primary)
        solver = cp_model.CpSolver()
        solver.parameters.num_search_workers = 1
        solver.parameters.max_time_in_seconds = 10
        status = solver.solve(model)
        self.assertIn(status, (cp_model.OPTIMAL, cp_model.INFEASIBLE))
        return (status, solver.objective_value if status == cp_model.OPTIMAL else None)

    def test_compaction_preserves_optima_and_infeasibility(self):
        fixtures = {
            "shared": (self.fixture(), None),
            "workfront": (self.fixture(same_contract=True), None),
            "exclusive": (self.fixture(kind="PM"), None),
            "buffer": (self.fixture(buffered=True), None),
            "zero_supply": (self.fixture(capacity=0), None),
            "split_night": (self.fixture(), {
                ("A1", 1): {"eclo": 0, "access_night": 1},
                ("A2", 1): {"eclo": 0, "access_night": 2},
            }),
        }
        for name, (instance, fixed) in fixtures.items():
            for scenario in "ABC":
                with self.subTest(fixture=name, scenario=scenario):
                    self.assertEqual(self.optimum(instance, scenario, False, fixed),
                                     self.optimum(instance, scenario, True, fixed))

    def test_compaction_reduces_variables_and_constraints(self):
        from sincro.optimal import POLICIES, _build

        instance = self.fixture()
        expanded, *_ = _build(instance, POLICIES["C"], 4, instance.horizon_start)
        compact, *_ = _build(instance, POLICIES["C"], 4, instance.horizon_start, compact=True)
        self.assertLess(len(compact.proto.variables), len(expanded.proto.variables))
        self.assertLess(len(compact.proto.constraints), len(expanded.proto.constraints))


if __name__ == "__main__":
    unittest.main()
