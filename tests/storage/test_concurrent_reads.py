"""Buffer pool behavior when several threads miss at once."""

import threading
import time
import unittest

from dbmsi.storage import BufferPoolManager, DiskManager


class TestBufferPoolConcurrentMisses(unittest.TestCase):
    def test_parallel_misses_overlap_and_agree(self):
        disk = DiskManager(read_latency=0.02)
        bpm = BufferPoolManager(disk, pool_size=16)
        ids = []
        for i in range(8):
            with bpm.create() as page:
                page.data[0] = i
                page.mark_dirty()
                ids.append(page.page_id)
        bpm.clear()
        seen = {}

        def reader(pid):
            with bpm.fetch(pid) as page:
                seen.setdefault(pid, []).append(page.data[0])

        t = time.perf_counter()
        workers = [threading.Thread(target=reader, args=(p,)) for p in ids for _ in range(2)]
        for w in workers:
            w.start()
        for w in workers:
            w.join()
        elapsed = time.perf_counter() - t
        self.assertEqual({p: v for p, v in seen.items()}, {p: [i, i] for i, p in enumerate(ids)})
        self.assertEqual(bpm.stats.page_reads, 8)  # the second reader of a page waits
        self.assertLess(elapsed, 8 * 0.02)  # the reads overlapped

    def test_failed_read_frees_frame(self):
        bpm = BufferPoolManager(DiskManager(), pool_size=2)
        with self.assertRaises(IndexError):
            bpm.fetch_page(99)
        self.assertFalse(bpm.is_resident(99))
        for _ in range(2):
            with bpm.create():
                pass


if __name__ == "__main__":
    unittest.main()
