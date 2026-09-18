"""Small independent regressions for the dependency-free dispatch search."""
import unittest
from dataclasses import replace
from pathlib import Path

from sincro.construct import Geometry, Week, construct, objective, solve
from sincro.instance import Activity, Contract, load_instance


PUBLIC = load_instance(Path(__file__).resolve().parents[1] / '01_data')
LOCATION = 'SEC:ALP:S01_S02:EB'


def fixture():
    contracts = {}
    for cn, priority, due in [('P', 3, 10), ('S', 1, 3), ('C', 2, 4)]:
        contracts[cn] = Contract(cn, cn, 'Works', 'Non-live (Others)', priority,
                                 PUBLIC.week_end(due), PUBLIC.week_end(due), 1, 'PM', 1)
    activities = {
        'parent': Activity('parent', 'P', 'Works', LOCATION, LOCATION, 1,
                           PUBLIC.week_start(1), None, 3),
        'successor': Activity('successor', 'S', 'Works', 'SEC:ALP:S07_S08:EB',
                              'SEC:ALP:S07_S08:EB', 2, PUBLIC.week_start(1), 'parent', 3),
        'competitor': Activity('competitor', 'C', 'Works', LOCATION, LOCATION, 3,
                               PUBLIC.week_start(1), None, 3),
    }
    return replace(PUBLIC, contracts=contracts, activities=activities, _line_sectors={})


class HeuristicOptimizationTests(unittest.TestCase):
    def test_successor_deadline_and_weight_reach_its_predecessor(self):
        inst = fixture()
        baseline, _ = construct(inst, ordering='slack')
        improved, _ = construct(inst, ordering='chain')
        self.assertGreater(objective(inst, baseline, 'A'), 0)
        self.assertEqual(objective(inst, improved, 'A'), 0)
        self.assertEqual(improved['finish_week'], {'parent': 1, 'successor': 3, 'competitor': 4})

    def test_release_delay_preserves_precedence_and_cached_geometry(self):
        inst = fixture()
        delayed, _ = construct(inst, ordering='chain', not_before={'parent': 4})
        cached, _ = construct(inst, ordering='chain', not_before={'parent': 4},
                              _geometry=Geometry(inst))
        self.assertEqual(delayed, cached)
        self.assertEqual(delayed['finish_week']['parent'], 4)
        self.assertEqual(delayed['finish_week']['successor'], 6)
        self.assertEqual(len(delayed['placements']), 6)

    def test_direct_week_construction_rejects_cycles(self):
        inst = fixture()
        inst.activities['parent'] = replace(inst.activities['parent'],
                                            predecessor_activity_id='successor')
        with self.assertRaisesRegex(ValueError, 'cycle'):
            Week(inst, 1)

    def test_seeded_search_keeps_an_unavoidable_lateness_deterministic(self):
        inst = fixture()
        inst.activities = {'competitor': replace(inst.activities['competitor'], total_accesses=5)}
        inst.contracts = {'C': inst.contracts['C']}
        first = solve(inst, 'A')
        self.assertEqual(first, solve(inst, 'A'))
        self.assertEqual(first['finish_week'], {'competitor': 5})


if __name__ == '__main__':
    unittest.main()
