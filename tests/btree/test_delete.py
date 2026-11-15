import random
import unittest

from dbmsi.btree.keys import IntKey
from dbmsi.btree.tree import BPlusTree
from dbmsi.storage import BufferPoolManager, DiskManager, RID


def small_tree(leaf_capacity=4, internal_capacity=3, pool_size=64):
    bpm = BufferPoolManager(DiskManager(), pool_size=pool_size)
    tree = BPlusTree(
        bpm, IntKey(), leaf_capacity=leaf_capacity, internal_capacity=internal_capacity
    )
    return bpm, tree


class TestDelete(unittest.TestCase):
    def test_delete_missing_returns_false(self):
        _, tree = small_tree()
        self.assertFalse(tree.delete(1, RID(0, 0)))
        tree.insert(1, RID(0, 0))
        self.assertFalse(tree.delete(1, RID(0, 1)))
        self.assertFalse(tree.delete(2, RID(0, 0)))
        self.assertTrue(tree.delete(1, RID(0, 0)))
        self.assertFalse(tree.delete(1, RID(0, 0)))
        self.assertEqual(len(tree), 0)
        tree.validate()

    def test_random_deletes_keep_tree_valid(self):
        for leaf_cap, int_cap in [(3, 3), (4, 3), (5, 4), (8, 6)]:
            with self.subTest(leaf_cap=leaf_cap, int_cap=int_cap):
                rng = random.Random(leaf_cap * 31 + int_cap)
                _, tree = small_tree(leaf_cap, int_cap)
                live = {}
                for k in rng.sample(range(2000), 600):
                    live[k] = RID(k, 0)
                    tree.insert(k, live[k])
                order = list(live)
                rng.shuffle(order)
                for i, k in enumerate(order):
                    self.assertTrue(tree.delete(k, live.pop(k)))
                    if i % 25 == 0:
                        tree.validate()
                        self.assertEqual([k for k, _ in tree.items()], sorted(live))
                tree.validate()
                self.assertEqual(tree.height, 1)
                self.assertEqual(list(tree.items()), [])

    def test_mixed_workload_matches_reference(self):
        rng = random.Random(77)
        _, tree = small_tree()
        ref = set()
        for step in range(3000):
            k, rid = rng.randrange(150), RID(rng.randrange(4), 0)
            if (k, rid) in ref and rng.random() < 0.6:
                self.assertTrue(tree.delete(k, rid))
                ref.discard((k, rid))
            elif (k, rid) not in ref:
                tree.insert(k, rid)
                ref.add((k, rid))
            if step % 200 == 0:
                tree.validate()
        tree.validate()
        self.assertEqual(list(tree.items()), sorted(ref))

    def test_delete_key_removes_all_duplicates(self):
        _, tree = small_tree()
        for i in range(40):
            tree.insert(5, RID(i, 0))
            tree.insert(i + 10, RID(i, 1))
        self.assertEqual(tree.delete_key(5), 40)
        self.assertEqual(tree.search(5), [])
        self.assertEqual(len(tree), 40)
        tree.validate()

    def test_tree_shrinks_and_frees_pages(self):
        bpm, tree = small_tree()
        for k in range(300):
            tree.insert(k, RID(k, 0))
        tall = tree.height
        pages_before = tree.stats().total_pages
        for k in range(290):
            tree.delete(k, RID(k, 0))
        tree.validate()
        self.assertLess(tree.height, tall)
        self.assertLess(tree.stats().total_pages, pages_before)
        self.assertEqual(bpm.stats.pages_deallocated, pages_before - tree.stats().total_pages)

    def test_reinsert_after_delete_reuses_pages(self):
        bpm, tree = small_tree()
        for k in range(200):
            tree.insert(k, RID(k, 0))
        for k in range(200):
            tree.delete(k, RID(k, 0))
        high_water = bpm.disk.num_pages
        for k in range(200):
            tree.insert(k, RID(k, 0))
        tree.validate()
        self.assertLessEqual(bpm.disk.num_pages, high_water + 2)

    def test_works_with_tiny_buffer_pool(self):
        _, tree = small_tree(pool_size=3)
        for k in range(200):
            tree.insert(k, RID(k, 0))
        for k in range(0, 200, 2):
            tree.delete(k, RID(k, 0))
        tree.validate()
        self.assertEqual([k for k, _ in tree.items()], list(range(1, 200, 2)))


if __name__ == "__main__":
    unittest.main()
