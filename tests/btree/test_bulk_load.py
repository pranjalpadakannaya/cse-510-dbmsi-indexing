import random
import unittest

from dbmsi.btree.keys import IntKey
from dbmsi.btree.tree import BPlusTree, DuplicateEntryError, _chunks
from dbmsi.storage import BufferPoolManager, DiskManager, RID, measure


def empty_tree(leaf_capacity=4, internal_capacity=3, pool_size=64):
    bpm = BufferPoolManager(DiskManager(), pool_size=pool_size)
    tree = BPlusTree(
        bpm, IntKey(), leaf_capacity=leaf_capacity, internal_capacity=internal_capacity
    )
    return bpm, tree


class TestChunks(unittest.TestCase):
    def test_groups_respect_bounds(self):
        for cap in range(3, 30):
            lo = cap // 2
            for target in range(lo, cap + 1):
                for n in range(1, 200):
                    groups = _chunks(list(range(n)), target, lo, cap)
                    self.assertEqual([x for g in groups for x in g], list(range(n)))
                    for g in groups:
                        self.assertLessEqual(len(g), cap)
                        if len(groups) > 1:
                            self.assertGreaterEqual(len(g), lo)


class TestBulkLoad(unittest.TestCase):
    def test_sizes_and_capacities(self):
        for leaf_cap, int_cap in [(3, 3), (4, 3), (7, 5), (16, 12)]:
            for n in [1, 2, 3, 5, 17, 64, 333, 1000]:
                for ff in (0.5, 0.75, 1.0):
                    with self.subTest(leaf_cap=leaf_cap, int_cap=int_cap, n=n, ff=ff):
                        _, tree = empty_tree(leaf_cap, int_cap)
                        pairs = [(k, RID(k, 0)) for k in range(n)]
                        tree.bulk_load(pairs, fill_factor=ff)
                        tree.validate()
                        self.assertEqual(list(tree.items()), pairs)

    def test_unsorted_input_with_duplicate_keys(self):
        _, tree = empty_tree()
        pairs = [(k % 50, RID(k, 0)) for k in range(400)]
        random.Random(4).shuffle(pairs)
        tree.bulk_load(pairs)
        tree.validate()
        self.assertEqual(list(tree.items()), sorted(pairs))
        self.assertEqual(tree.search(7), [RID(k, 0) for k in range(7, 400, 50)])

    def test_fill_factor_controls_density(self):
        pairs = [(k, RID(k, 0)) for k in range(2000)]
        _, full = empty_tree(leaf_capacity=20, internal_capacity=10)
        full.bulk_load(pairs, fill_factor=1.0)
        _, half = empty_tree(leaf_capacity=20, internal_capacity=10)
        half.bulk_load(pairs, fill_factor=0.5)
        self.assertEqual(full.stats().leaf_pages, 100)
        self.assertEqual(half.stats().leaf_pages, 200)

    def test_tree_is_fully_usable_afterwards(self):
        _, tree = empty_tree()
        tree.bulk_load((k, RID(k, 0)) for k in range(0, 600, 2))
        rng = random.Random(8)
        for k in rng.sample(range(1, 600, 2), 150):
            tree.insert(k, RID(k, 0))
        for k in rng.sample(range(0, 600, 2), 200):
            self.assertTrue(tree.delete(k, RID(k, 0)))
        tree.validate()

    def test_full_scan_after_bulk_load_is_mostly_sequential(self):
        bpm, tree = empty_tree(leaf_capacity=50, internal_capacity=20, pool_size=16)
        tree.bulk_load((k, RID(k, 0)) for k in range(5000))
        bpm.clear()
        with measure(bpm.stats) as m:
            self.assertEqual(sum(1 for _ in tree.range_scan()), 5000)
        leaves = tree.stats().leaf_pages
        self.assertGreaterEqual(m.delta.sequential_reads, leaves - 1)

    def test_rejects_bad_input(self):
        _, tree = empty_tree()
        with self.assertRaises(ValueError):
            tree.bulk_load([], fill_factor=0.3)
        with self.assertRaises(DuplicateEntryError):
            tree.bulk_load([(1, RID(1, 1)), (1, RID(1, 1))])
        tree.insert(1, RID(0, 0))
        with self.assertRaises(ValueError):
            tree.bulk_load([(2, RID(0, 0))])

    def test_empty_input_keeps_empty_tree(self):
        _, tree = empty_tree()
        tree.bulk_load([])
        tree.validate()
        tree.insert(3, RID(0, 0))
        self.assertEqual(tree.search(3), [RID(0, 0)])


if __name__ == "__main__":
    unittest.main()
