import os
import tempfile
import threading
import unittest

from dbmsi.storage.buffer_pool import BufferPoolFullError, BufferPoolManager
from dbmsi.storage.disk_manager import DiskManager


def make_pool(size: int, num_pages: int = 0):
    """Pool over an in-memory disk with ``num_pages`` tagged pages written."""
    disk = DiskManager()
    for i in range(num_pages):
        pid = disk.allocate_page()
        disk.write_page(pid, bytes([i % 256]) * 4096)
    disk.stats.reset()
    return BufferPoolManager(disk, size)


class BufferPoolTest(unittest.TestCase):
    def test_hit_and_miss_accounting(self):
        bpm = make_pool(2, 3)
        for pid in [0, 0, 1, 0]:
            with bpm.fetch(pid) as p:
                self.assertEqual(p.data[0], pid)
        s = bpm.stats
        self.assertEqual((s.page_requests, s.buffer_hits, s.buffer_misses), (4, 2, 2))
        self.assertEqual(s.page_reads, 2)
        self.assertEqual(s.evictions, 0)

    def test_lru_eviction_order(self):
        bpm = make_pool(2, 3)
        for pid in [0, 1, 0, 2]:  # 1 is LRU when 2 arrives
            with bpm.fetch(pid):
                pass
        self.assertTrue(bpm.is_resident(0))
        self.assertFalse(bpm.is_resident(1))
        self.assertTrue(bpm.is_resident(2))
        self.assertEqual(bpm.stats.evictions, 1)

    def test_pinned_pages_are_not_evicted(self):
        bpm = make_pool(2, 3)
        p0 = bpm.fetch_page(0)
        p1 = bpm.fetch_page(1)
        with self.assertRaises(BufferPoolFullError):
            bpm.fetch_page(2)
        bpm.unpin_page(p1.page_id)
        with bpm.fetch(2):
            pass
        self.assertTrue(bpm.is_resident(0))
        self.assertFalse(bpm.is_resident(1))
        self.assertEqual(p0.pin_count, 1)
        bpm.unpin_page(0)

    def test_multiple_pins(self):
        bpm = make_pool(1, 2)
        bpm.fetch_page(0)
        bpm.fetch_page(0)
        self.assertEqual(bpm.pin_count(0), 2)
        bpm.unpin_page(0)
        with self.assertRaises(BufferPoolFullError):
            bpm.fetch_page(1)
        bpm.unpin_page(0)
        with self.assertRaises(RuntimeError):
            bpm.unpin_page(0)
        with self.assertRaises(KeyError):
            bpm.unpin_page(1)

    def test_dirty_writeback_on_eviction(self):
        bpm = make_pool(1, 2)
        with bpm.fetch(0) as p:
            p.data[0] = 0xAB
            p.mark_dirty()
        with bpm.fetch(1):
            pass  # evicts dirty page 0
        self.assertEqual(bpm.stats.dirty_writebacks, 1)
        self.assertEqual(bpm.stats.page_writes, 1)
        with bpm.fetch(0) as p:
            self.assertEqual(p.data[0], 0xAB)

    def test_clean_eviction_does_not_write(self):
        bpm = make_pool(1, 2)
        for pid in [0, 1, 0]:
            with bpm.fetch(pid):
                pass
        self.assertEqual(bpm.stats.page_writes, 0)
        self.assertEqual(bpm.stats.evictions, 2)

    def test_new_page_is_zeroed_and_flushed(self):
        bpm = make_pool(1)
        with bpm.create() as p:
            pid = p.page_id
            self.assertEqual(p.data, bytearray(4096))
            p.data[10] = 7
        bpm.flush_page(pid)
        self.assertEqual(bpm.stats.page_writes, 1)
        bpm.flush_page(pid)  # clean now: no extra write
        self.assertEqual(bpm.stats.page_writes, 1)
        bpm.clear()
        with bpm.fetch(pid) as p:
            self.assertEqual(p.data[10], 7)

    def test_unpin_dirty_flag(self):
        bpm = make_pool(1, 1)
        p = bpm.fetch_page(0)
        p.data[0] = 99
        bpm.unpin_page(0, is_dirty=True)
        bpm.flush_all()
        self.assertEqual(bpm.stats.page_writes, 1)

    def test_clear_gives_cold_cache(self):
        bpm = make_pool(4, 4)
        for pid in range(4):
            with bpm.fetch(pid):
                pass
        bpm.clear()
        self.assertEqual(bpm.num_resident, 0)
        bpm.stats.reset()
        for pid in range(4):
            with bpm.fetch(pid):
                pass
        self.assertEqual(bpm.stats.buffer_misses, 4)
        self.assertEqual(bpm.stats.sequential_reads, 3)

    def test_delete_page(self):
        bpm = make_pool(2)
        with bpm.create() as p:
            pid = p.page_id
        p = bpm.fetch_page(pid)
        with self.assertRaises(RuntimeError):
            bpm.delete_page(pid)
        bpm.unpin_page(pid)
        bpm.delete_page(pid)
        self.assertFalse(bpm.is_resident(pid))
        with bpm.create() as p2:
            self.assertEqual(p2.page_id, pid)  # id reused

    def test_failed_read_does_not_leak_frame(self):
        bpm = make_pool(1)
        with self.assertRaises(IndexError):
            bpm.fetch_page(5)
        with bpm.create():
            pass

    def test_scan_larger_than_pool(self):
        bpm = make_pool(8, 100)
        for pid in range(100):
            with bpm.fetch(pid) as p:
                self.assertEqual(p.data[0], pid)
        s = bpm.stats
        self.assertEqual(s.page_reads, 100)
        self.assertEqual(s.evictions, 92)
        self.assertEqual(s.sequential_reads, 99)

    def test_concurrent_access(self):
        bpm = make_pool(8, 32)
        errors = []

        def worker(seed):
            try:
                for i in range(500):
                    pid = (seed * 7 + i) % 32
                    with bpm.fetch(pid) as p:
                        assert p.page_id == pid and p.data[0] == pid
            except Exception as e:  # pragma: no cover
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        self.assertEqual(bpm.stats.page_requests, 3000)
        for pid in range(32):
            self.assertEqual(bpm.pin_count(pid), 0)

    def test_persistence_through_file(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "bp.db")
            with DiskManager(path) as disk:
                bpm = BufferPoolManager(disk, 2)
                ids = []
                for i in range(5):
                    with bpm.create() as p:
                        p.data[0] = i + 1
                        ids.append(p.page_id)
                bpm.flush_all()
            with DiskManager(path) as disk:
                bpm = BufferPoolManager(disk, 2)
                for i, pid in enumerate(ids):
                    with bpm.fetch(pid) as p:
                        self.assertEqual(p.data[0], i + 1)


if __name__ == "__main__":
    unittest.main()
