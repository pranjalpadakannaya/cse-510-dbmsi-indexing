import os
import random
import tempfile
import unittest

from dbmsi.btree.keys import IntKey, StringKey
from dbmsi.btree.tree import BPlusTree, DuplicateEntryError
from dbmsi.storage import BufferPoolManager, DiskManager, RID, measure


def small_tree(pool_size=64, codec=None):
    """A tree with tiny fan-out so a few hundred keys build several levels."""
    bpm = BufferPoolManager(DiskManager(), pool_size=pool_size)
    tree = BPlusTree(bpm, codec or IntKey(), leaf_capacity=4, internal_capacity=3)
    return bpm, tree


class TestInsertAndSearch(unittest.TestCase):
    def test_empty_tree(self):
        _, tree = small_tree()
        self.assertEqual(tree.search(1), [])
        self.assertEqual(len(tree), 0)
        self.assertEqual(tree.height, 1)
        tree.validate()

    def test_random_inserts_build_valid_multilevel_tree(self):
        _, tree = small_tree()
        rng = random.Random(10)
        keys = list(range(500))
        rng.shuffle(keys)
        for k in keys:
            tree.insert(k, RID(k, k % 7))
        tree.validate()
        self.assertGreaterEqual(tree.height, 4)
        self.assertEqual(len(tree), 500)
        for k in range(500):
            self.assertEqual(tree.search(k), [RID(k, k % 7)])
        self.assertEqual(tree.search(500), [])
        self.assertEqual(tree.search(-1), [])

    def test_sequential_inserts(self):
        _, tree = small_tree()
        for k in range(300):
            tree.insert(k, RID(0, k))
        tree.validate()
        for k in range(0, 300, 17):
            self.assertEqual(tree.search(k), [RID(0, k)])

    def test_duplicate_keys_span_leaves(self):
        _, tree = small_tree()
        rids = [RID(p, s) for p in range(5) for s in range(6)]
        shuffled = rids[:]
        random.Random(3).shuffle(shuffled)
        for rid in shuffled:
            tree.insert(7, rid)
            tree.insert(rid.page_id * 100 + 1000, rid)
        tree.validate()
        self.assertEqual(tree.search(7), sorted(rids))

    def test_rejects_exact_duplicate(self):
        _, tree = small_tree()
        tree.insert(1, RID(1, 1))
        with self.assertRaises(DuplicateEntryError):
            tree.insert(1, RID(1, 1))
        self.assertEqual(len(tree), 1)
        tree.insert(1, RID(1, 2))
        self.assertEqual(len(tree), 2)

    def test_string_keys(self):
        _, tree = small_tree(codec=StringKey(8))
        exts = ["pdf", "txt", "py", "jpg", "md", "c", "h", "zip"]
        for i in range(200):
            tree.insert(exts[i % len(exts)], RID(i, 0))
        tree.validate()
        self.assertEqual(tree.search("py"), [RID(i, 0) for i in range(2, 200, 8)])

    def test_works_with_tiny_buffer_pool(self):
        # Never more than a couple of pages pinned at once.
        _, tree = small_tree(pool_size=3)
        for k in range(200):
            tree.insert(k, RID(k, 0))
        tree.validate()

    def test_default_capacity_fills_a_page(self):
        bpm = BufferPoolManager(DiskManager(), pool_size=16)
        tree = BPlusTree(bpm, IntKey())
        for k in range(tree.layout.leaf_capacity):
            tree.insert(k, RID(k, 0))
        self.assertEqual(tree.height, 1)
        tree.insert(-1, RID(0, 0))
        self.assertEqual(tree.height, 2)
        tree.validate()


class TestIOAccounting(unittest.TestCase):
    def test_point_lookup_reads_one_page_per_level(self):
        bpm, tree = small_tree(pool_size=256)
        for k in range(400):
            tree.insert(k, RID(k, 0))
        bpm.clear()
        with measure(bpm.stats) as m:
            tree.search(123)
        self.assertEqual(m.delta.page_reads, tree.height)

    def test_stats_counts_pages(self):
        bpm, tree = small_tree()
        for k in range(100):
            tree.insert(k, RID(k, 0))
        s = tree.stats()
        self.assertEqual(s.height, tree.height)
        self.assertEqual(s.num_entries, 100)
        self.assertGreaterEqual(s.leaf_pages, 100 // 4)
        self.assertEqual(s.total_pages, bpm.disk.num_pages)


class TestPersistence(unittest.TestCase):
    def test_reopen_from_disk(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "idx.db")
            bpm = BufferPoolManager(DiskManager(path), pool_size=8)
            tree = BPlusTree(bpm, StringKey(12), leaf_capacity=5, internal_capacity=4)
            for i in range(150):
                tree.insert(f"file{i:03d}", RID(i, 1))
            tree.flush()
            bpm.flush_all()
            meta = tree.meta_page_id
            bpm.disk.close()

            bpm2 = BufferPoolManager(DiskManager(path), pool_size=8)
            reopened = BPlusTree(bpm2, meta_page_id=meta)
            self.assertEqual(reopened.codec, StringKey(12))
            self.assertEqual(len(reopened), 150)
            self.assertEqual(reopened.height, tree.height)
            self.assertEqual(reopened.search("file042"), [RID(42, 1)])
            reopened.validate()
            with self.assertRaises(ValueError):
                BPlusTree(bpm2, IntKey(), meta_page_id=meta)
            bpm2.disk.close()

    def test_rejects_non_meta_page(self):
        bpm, tree = small_tree()
        with self.assertRaises(ValueError):
            BPlusTree(bpm, meta_page_id=tree.root_page_id)

    def test_new_tree_requires_codec(self):
        bpm = BufferPoolManager(DiskManager(), pool_size=4)
        with self.assertRaises(ValueError):
            BPlusTree(bpm)


if __name__ == "__main__":
    unittest.main()
