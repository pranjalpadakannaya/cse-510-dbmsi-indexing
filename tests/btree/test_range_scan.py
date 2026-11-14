import random
import unittest
from datetime import datetime, timedelta, timezone

from dbmsi.btree.keys import FloatKey, IntKey, StringKey, TimestampKey
from dbmsi.btree.tree import BPlusTree
from dbmsi.storage import BufferPoolManager, DiskManager, RID, measure


def build(keys, codec=None, pool_size=128):
    bpm = BufferPoolManager(DiskManager(), pool_size=pool_size)
    tree = BPlusTree(bpm, codec or IntKey(), leaf_capacity=4, internal_capacity=3)
    pairs = [(k, RID(i, 0)) for i, k in enumerate(keys)]
    shuffled = pairs[:]
    random.Random(5).shuffle(shuffled)
    for k, rid in shuffled:
        tree.insert(k, rid)
    return bpm, tree, sorted(pairs)


class TestRangeScan(unittest.TestCase):
    def setUp(self):
        # Even keys 0..398, each appearing twice, so bounds hit duplicates.
        keys = [k for k in range(0, 400, 2) for _ in range(2)]
        self.bpm, self.tree, self.pairs = build(keys)

    def expected(self, lo, hi, inc_lo=True, inc_hi=True):
        def ok(k):
            if lo is not None and (k < lo or (k == lo and not inc_lo)):
                return False
            if hi is not None and (k > hi or (k == hi and not inc_hi)):
                return False
            return True

        return [p for p in self.pairs if ok(p[0])]

    def test_full_scan_is_ordered(self):
        self.assertEqual(list(self.tree.range_scan()), self.pairs)
        self.assertEqual(list(self.tree.items()), self.pairs)

    def test_bounds_and_inclusivity(self):
        cases = [
            (10, 20), (11, 21), (0, 0), (398, 398), (-50, 5), (390, 999),
            (None, 30), (370, None), (200, 100), (7, 7),
        ]
        for lo, hi in cases:
            for inc_lo in (True, False):
                for inc_hi in (True, False):
                    with self.subTest(lo=lo, hi=hi, inc_lo=inc_lo, inc_hi=inc_hi):
                        got = list(self.tree.range_scan(lo, hi, inc_lo, inc_hi))
                        self.assertEqual(got, self.expected(lo, hi, inc_lo, inc_hi))

    def test_random_ranges(self):
        rng = random.Random(9)
        for _ in range(200):
            lo, hi = sorted(rng.randrange(-10, 410) for _ in range(2))
            self.assertEqual(list(self.tree.range_scan(lo, hi)), self.expected(lo, hi))

    def test_empty_tree(self):
        _, tree, _ = build([])
        self.assertEqual(list(tree.range_scan()), [])
        self.assertEqual(list(tree.range_scan(1, 5)), [])

    def test_abandoned_iterator_leaves_nothing_pinned(self):
        it = self.tree.range_scan(10, 300)
        next(it)
        next(it)
        del it
        for pid in range(self.bpm.disk.num_pages):
            self.assertEqual(self.bpm.pin_count(pid), 0)

    def test_scan_reads_only_overlapping_leaves(self):
        self.bpm.clear()
        with measure(self.bpm.stats) as m:
            got = list(self.tree.range_scan(100, 140))
        leaf_cap = self.tree.layout.leaf_capacity
        min_leaves = -(-len(got) // leaf_cap)
        # Root-to-leaf descent, then roughly one read per covered leaf.
        self.assertLessEqual(
            m.delta.page_reads, self.tree.height - 1 + 2 * min_leaves + 1
        )
        self.assertLess(m.delta.page_reads, self.tree.stats().leaf_pages)

    def test_float_range(self):
        values = [round(-50 + i * 0.37, 2) for i in range(300)]
        _, tree, _ = build(values, codec=FloatKey())
        got = [k for k, _ in tree.range_scan(-1.5, 2.25, include_high=False)]
        self.assertEqual(got, sorted(v for v in values if -1.5 <= v < 2.25))

    def test_time_window(self):
        base = datetime(2025, 6, 1, tzinfo=timezone.utc)
        mtimes = [base + timedelta(hours=7 * i) for i in range(300)]
        _, tree, _ = build(mtimes, codec=TimestampKey())
        lo, hi = datetime(2025, 7, 1, tzinfo=timezone.utc), datetime(2025, 8, 1, tzinfo=timezone.utc)
        got = [k for k, _ in tree.range_scan(lo, hi, include_high=False)]
        self.assertEqual(got, [t for t in mtimes if lo <= t < hi])
        self.assertEqual(len(tree.search(mtimes[42])), 1)

    def test_string_prefix_style_range(self):
        names = [f"img_{i:04d}.jpg" for i in range(50)] + [f"doc_{i:04d}.pdf" for i in range(50)]
        _, tree, _ = build(names, codec=StringKey(16))
        got = [k for k, _ in tree.range_scan("doc_", "doc_\U0010ffff")]
        self.assertEqual(got, sorted(n for n in names if n.startswith("doc_")))


if __name__ == "__main__":
    unittest.main()
