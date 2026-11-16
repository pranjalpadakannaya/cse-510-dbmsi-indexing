"""Latch-crabbing B+ tree: the whole single-threaded B+ tree suite, re-run
against :class:`CrabbingBPlusTree`, plus concurrent stress tests."""

import importlib.util
import random
import threading
import unittest

from dbmsi.btree import CrabbingBPlusTree, IntKey, RWLatch
from dbmsi.btree.tree import DuplicateEntryError
from dbmsi.storage import BufferPoolManager, DiskManager, RID

_SUITES = ["test_tree", "test_bulk_load", "test_delete", "test_range_scan"]


def _crabbing_copies():
    """Re-import each B+ tree test module with ``BPlusTree`` replaced, and
    expose its test cases here under a ``Crabbing`` prefix."""
    copies = {}
    for name in _SUITES:
        full = f"tests.btree.{name}"
        spec = importlib.util.find_spec(full)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.BPlusTree = CrabbingBPlusTree
        for attr, value in vars(module).items():
            if isinstance(value, type) and issubclass(value, unittest.TestCase) \
                    and value.__module__ == module.__name__:
                copies[f"Crabbing_{name}_{attr}"] = type(
                    f"Crabbing_{name}_{attr}", (value,), {"__module__": __name__}
                )
    return copies


globals().update(_crabbing_copies())


def _tree(pool_size=256, leaf_capacity=4, internal_capacity=3, read_latency=0.0):
    bpm = BufferPoolManager(DiskManager(read_latency=read_latency), pool_size=pool_size)
    tree = CrabbingBPlusTree(bpm, IntKey(), leaf_capacity=leaf_capacity,
                             internal_capacity=internal_capacity)
    return bpm, tree


def _run(threads):
    errors = []

    def wrap(fn):
        def target():
            try:
                fn()
            except BaseException as e:  # surfaced in the main thread
                errors.append(e)
        return target

    workers = [threading.Thread(target=wrap(fn)) for fn in threads]
    for w in workers:
        w.start()
    for w in workers:
        w.join(timeout=60)
        if w.is_alive():
            raise AssertionError("worker did not finish: deadlock?")
    if errors:
        raise errors[0]


class TestRWLatch(unittest.TestCase):
    def test_shared_holders_coexist(self):
        latch = RWLatch()
        latch.acquire_shared()
        latch.acquire_shared()
        latch.release_shared()
        latch.release_shared()
        self.assertTrue(latch.is_free)

    def test_exclusive_waits_for_readers(self):
        latch = RWLatch()
        latch.acquire_shared()
        got = threading.Event()

        def writer():
            with latch.exclusive():
                got.set()

        t = threading.Thread(target=writer)
        t.start()
        self.assertFalse(got.wait(0.1))
        latch.release_shared()
        self.assertTrue(got.wait(5))
        t.join()
        self.assertTrue(latch.is_free)

    def test_waiting_writer_blocks_new_readers(self):
        latch = RWLatch()
        latch.acquire_shared()
        order = []
        writer_waiting = threading.Event()

        def writer():
            writer_waiting.set()
            with latch.exclusive():
                order.append("writer")

        def reader():
            with latch.shared():
                order.append("reader")

        w = threading.Thread(target=writer)
        w.start()
        writer_waiting.wait()
        while latch._waiting_writers == 0:
            pass
        r = threading.Thread(target=reader)
        r.start()
        latch.release_shared()
        w.join()
        r.join()
        self.assertEqual(order, ["writer", "reader"])

    def test_release_without_hold_raises(self):
        with self.assertRaises(RuntimeError):
            RWLatch().release_shared()
        with self.assertRaises(RuntimeError):
            RWLatch().release_exclusive()


class TestCrabbingSingleThread(unittest.TestCase):
    def test_optimistic_and_pessimistic_paths_both_used(self):
        _, tree = _tree()
        for k in range(200):
            tree.insert(k, RID(k, 0))
        tree.validate()
        self.assertGreater(tree.optimistic_inserts, 0)
        self.assertGreater(tree.pessimistic_inserts, 0)
        self.assertEqual(tree.optimistic_inserts + tree.pessimistic_inserts, 200)
        self.assertTrue(tree.latches_free())

    def test_duplicate_releases_latches(self):
        _, tree = _tree()
        for k in range(50):
            tree.insert(k, RID(k, 0))
        with self.assertRaises(DuplicateEntryError):
            tree.insert(10, RID(10, 0))
        # A full leaf takes the pessimistic path before finding the duplicate.
        for k in range(50):
            with self.assertRaises(DuplicateEntryError):
                tree.insert(k, RID(k, 0))
        self.assertTrue(tree.latches_free())
        self.assertEqual(len(tree), 50)
        tree.validate()

    def test_pins_released(self):
        bpm, tree = _tree()
        for k in range(300):
            tree.insert(k, RID(k, 0))
        for k in range(300):
            tree.search(k)
        self.assertEqual(sum(bpm.pin_count(p) for p in range(bpm.disk.num_pages)), 0)


class TestCrabbingConcurrent(unittest.TestCase):
    def _concurrent_inserts(self, threads, per_thread, pool_size=256, **kw):
        _, tree = _tree(pool_size=pool_size, **kw)
        keys = list(range(threads * per_thread))
        random.Random(1).shuffle(keys)
        shares = [keys[i::threads] for i in range(threads)]
        _run([lambda s=s: [tree.insert(k, RID(k, 1)) for k in s] for s in shares])
        tree.validate()
        self.assertEqual(len(tree), len(keys))
        for k in keys:
            self.assertEqual(tree.search(k), [RID(k, 1)])
        self.assertTrue(tree.latches_free())
        return tree

    def test_concurrent_inserts_tiny_fanout(self):
        tree = self._concurrent_inserts(8, 400)
        self.assertGreaterEqual(tree.height, 5)

    def test_concurrent_inserts_small_pool_forces_evictions(self):
        self._concurrent_inserts(8, 300, pool_size=24)

    def test_concurrent_inserts_with_read_latency(self):
        self._concurrent_inserts(6, 60, pool_size=16, read_latency=0.0002)

    def test_concurrent_inserts_real_fanout(self):
        self._concurrent_inserts(4, 3000, pool_size=64, leaf_capacity=None,
                                 internal_capacity=None)

    def test_searches_and_scans_during_inserts(self):
        _, tree = _tree(pool_size=64)
        base = list(range(0, 4000, 2))  # even keys exist before the run
        tree.bulk_load((k, RID(k, 0)) for k in base)
        new = list(range(1, 4000, 2))
        random.Random(2).shuffle(new)
        stop = threading.Event()
        problems = []

        def writer(share):
            for k in share:
                tree.insert(k, RID(k, 0))

        def reader(seed):
            rng = random.Random(seed)
            while not stop.is_set():
                k = rng.choice(base)
                if tree.search(k) != [RID(k, 0)]:
                    problems.append(("search", k))
                lo = rng.randrange(0, 3900)
                got = [key for key, _ in tree.range_scan(lo, lo + 60)]
                if got != sorted(got) or any(not lo <= g <= lo + 60 for g in got):
                    problems.append(("order", lo))
                if not set(range(lo + lo % 2, lo + 61, 2)) <= set(got):
                    problems.append(("missing", lo))

        readers = [threading.Thread(target=reader, args=(i,)) for i in range(4)]
        for r in readers:
            r.start()
        try:
            _run([lambda s=new[i::4]: writer(s) for i in range(4)])
        finally:
            stop.set()
            for r in readers:
                r.join()
        self.assertEqual(problems, [])
        tree.validate()
        self.assertEqual(len(tree), 4000)
        self.assertEqual([k for k, _ in tree.items()], list(range(4000)))
        self.assertTrue(tree.latches_free())

    def test_deletes_interleave_with_inserts(self):
        _, tree = _tree(pool_size=64)
        tree.bulk_load((k, RID(k, 0)) for k in range(0, 2000, 2))
        evens = list(range(0, 2000, 2))
        odds = list(range(1, 2000, 2))

        def deleter():
            for k in evens[::2]:
                self.assertTrue(tree.delete(k, RID(k, 0)))

        def inserter(share):
            for k in share:
                tree.insert(k, RID(k, 0))

        _run([deleter, lambda: inserter(odds[::2]), lambda: inserter(odds[1::2])])
        tree.validate()
        expected = sorted(set(evens[1::2]) | set(odds))
        self.assertEqual([k for k, _ in tree.items()], expected)
        self.assertTrue(tree.latches_free())


if __name__ == "__main__":
    unittest.main()
