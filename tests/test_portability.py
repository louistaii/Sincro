"""The tool must not be tuned to the public instance.

Hidden instances are free to rename lines, bounds, stations and interchange
hubs, and to carry different buffer depths or a third line. Each test relabels
the public CSVs and asserts the network behaves identically, because a
hardcoded identifier does not always crash -- renaming the hubs used to drop
the Live cross-line closure silently, which reads as a feasible schedule.
"""
import csv
import re
import tempfile
import unittest
from pathlib import Path

from sincro.instance import load_instance

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / '01_data'

# A Live activity sitting on the interchange tunnel: the one whose closure is
# supposed to reach the neighbouring line.
LIVE_AT_HUB = 'A074'

RENAME_HUBS = [(r'H01_H02', 'X01_X02'), (r'\bH01\b', 'X01'), (r'\bH02\b', 'X02')]
RENAME_BOUNDS = [(r'\bEB\b', 'NB'), (r'\bWB\b', 'SB')]
RENAME_LINES = [(r'\bALP\b', 'NOR'), (r'\bBET\b', 'STH')]


def relabel(dest: Path, subs=(), edits=None) -> Path:
    """Copy the public instance, applying regex renames and per-file edits."""
    dest.mkdir(parents=True, exist_ok=True)
    for f in sorted(DATA.glob('*.csv')):
        text = f.read_text()
        if edits and f.name in edits:
            text = edits[f.name](text)
        for pattern, repl in subs:
            text = re.sub(pattern, repl, text)
        (dest / f.name).write_text(text)
    return dest


def apply(subs, value: str) -> str:
    for pattern, repl in subs:
        value = re.sub(pattern, repl, value)
    return value


class Relabelled(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.public = load_instance(DATA)

    def footprint(self, inst, aid=LIVE_AT_HUB):
        return inst.closure_footprint(inst.activities[aid])

    def cross_line(self, inst, aid=LIVE_AT_HUB):
        own = inst.line_of(inst.activities[aid].start_location_id)
        return {loc for loc in self.footprint(inst, aid) if inst.line_of(loc) != own}

    # ---------- the silent failure ----------

    def test_public_live_activity_closes_the_other_line(self):
        """Baseline: the closure crosses lines at all."""
        self.assertTrue(self.cross_line(self.public),
                        'Live work at the interchange must close the other line')

    def test_renamed_hubs_keep_the_cross_line_closure(self):
        inst = load_instance(relabel(self.dir / 'hubs', RENAME_HUBS))
        expected = {apply(RENAME_HUBS, loc) for loc in self.cross_line(self.public)}
        self.assertEqual(self.cross_line(inst), expected)

    def test_renamed_bounds_keep_the_cross_line_closure(self):
        inst = load_instance(relabel(self.dir / 'bounds', RENAME_BOUNDS))
        expected = {apply(RENAME_BOUNDS, loc) for loc in self.cross_line(self.public)}
        self.assertEqual(self.cross_line(inst), expected)

    def test_renamed_lines_keep_the_cross_line_closure(self):
        inst = load_instance(relabel(self.dir / 'lines', RENAME_LINES))
        expected = {apply(RENAME_LINES, loc) for loc in self.cross_line(self.public)}
        self.assertEqual(self.cross_line(inst), expected)

    # ---------- every identifier at once ----------

    def test_full_relabel_is_isomorphic(self):
        subs = RENAME_HUBS + RENAME_BOUNDS + RENAME_LINES
        inst = load_instance(relabel(self.dir / 'all', subs))
        self.assertEqual(sorted(inst.activities), sorted(self.public.activities))
        for aid in self.public.activities:
            self.assertEqual(
                inst.closure_footprint(inst.activities[aid]),
                {apply(subs, loc) for loc in self.footprint(self.public, aid)},
                f'{aid}: closure footprint changed under relabelling')
            self.assertEqual(
                inst.span_locations(inst.activities[aid]),
                [apply(subs, loc) for loc in self.public.span_locations(self.public.activities[aid])],
                f'{aid}: span changed under relabelling')

    # ---------- buffer depths are data, not code ----------

    def test_buffer_depth_is_read_from_the_csv(self):
        deeper = relabel(self.dir / 'deep', edits={
            '05_BUFFER_LOCATION.csv': lambda t: t.replace('Live,2,1', 'Live,3,1')})
        inst = load_instance(deeper)
        self.assertEqual(inst.buffer_rules['Live'], (3, True))
        self.assertGreater(len(self.footprint(inst)), len(self.footprint(self.public)),
                           'a deeper buffer must widen the closure')

    def test_buffer_depth_zero_leaves_only_the_span_and_its_mirror(self):
        flat = relabel(self.dir / 'flat', edits={
            '05_BUFFER_LOCATION.csv': lambda t: t.replace('Live,2,1', 'Live,0,1')})
        inst = load_instance(flat)
        act = inst.activities[LIVE_AT_HUB]
        span = set(inst.span_locations(act))
        self.assertTrue(span <= self.footprint(inst))
        self.assertLess(len(self.footprint(inst)), len(self.footprint(self.public)))

    def test_unknown_nature_is_rejected_by_name_not_assumed(self):
        renamed = relabel(self.dir / 'nature', edits={
            '05_BUFFER_LOCATION.csv': lambda t: t.replace('Live,2,1', 'Third rail,2,1')})
        with self.assertRaises(ValueError) as cm:
            load_instance(renamed)
        self.assertIn('unknown nature of activity', str(cm.exception))

    def test_renamed_nature_carries_its_rule_through(self):
        """A nature spelled differently still mirrors and crosses lines."""
        subs = [(r'Live', 'Third rail')]
        inst = load_instance(relabel(self.dir / 'nature_ok', subs))
        self.assertEqual(inst.buffer_rules['Third rail'], (2, True))
        self.assertEqual(self.cross_line(inst), self.cross_line(self.public))


class ThirdLine(unittest.TestCase):
    """A third line sharing the same interchange hubs."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.public = load_instance(DATA)
        self.dir = relabel(Path(self.tmp.name) / 'three', edits={
            '01_LINES.csv': self._lines,
            '02_STATIONS.csv': lambda t: self._copy_line(t, 'line_code'),
            '03_SECTORS.csv': lambda t: self._copy_line(t, 'line_code'),
            '04_LOCATION_SUPPLY.csv': lambda t: self._copy_line(t, 'line_code'),
        })

    @staticmethod
    def _lines(text: str) -> str:
        return text.rstrip('\n') + '\nGAM,Line Gamma\n'

    @staticmethod
    def _copy_line(text: str, col: str) -> str:
        """Duplicate every BET row as an identically shaped GAM row."""
        rows = list(csv.DictReader(text.splitlines()))
        header = list(rows[0])
        extra = []
        for r in rows:
            if r[col] != 'BET':
                continue
            copy = {k: (v.replace('BET', 'GAM') if k != col else 'GAM') for k, v in r.items()}
            extra.append(copy)
        out = [','.join(header)]
        out += [','.join(r[k] for k in header) for r in rows + extra]
        return '\n'.join(out) + '\n'

    def test_third_line_loads(self):
        inst = load_instance(self.dir)
        self.assertEqual(inst.lines, ['ALP', 'BET', 'GAM'])

    def test_live_closure_reaches_every_other_line(self):
        inst = load_instance(self.dir)
        act = inst.activities[LIVE_AT_HUB]
        reached = inst.affected_lines(act)
        self.assertEqual(reached, {'ALP', 'BET', 'GAM'},
                         'cutting power at the interchange must close every line there')

    def test_non_live_work_stays_on_its_own_line(self):
        inst = load_instance(self.dir)
        for aid, act in inst.activities.items():
            nature = inst.contracts[act.contract_number].nature_of_activity
            if inst.buffer_rules[nature][1]:
                continue
            own = inst.line_of(act.start_location_id)
            self.assertEqual(inst.affected_lines(act), {own},
                             f'{aid} ({nature}) must not cross lines')


if __name__ == '__main__':
    unittest.main()
