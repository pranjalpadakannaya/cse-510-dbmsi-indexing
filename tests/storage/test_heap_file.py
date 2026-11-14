import random
import unittest

from dbmsi.storage.buffer_pool import BufferPoolManager
from dbmsi.storage.disk_manager import DiskManager
from dbmsi.storage.heap_file import HeapFile
from dbmsi.storage.rid import RID
from dbmsi.storage.slotted_page import MAX_RECORD_SIZE, PageFullError


def make_bpm(pool_size: int = 8) -> BufferPoolManager:
    return BufferPoolManager(DiskManager(), pool_size)


class HeapFileTest(unittest.TestCase):
    def test_insert_get_spans_pages(self):
        hf = HeapFile(make_bpm())
        rids = [hf.insert(f"record-{i:05d}".encode() * 10) for i in range(1000)]
        self.assertGreater(hf.num_pages, 1)
        self.assertEqual(len(set(rids)), 1000)
        for i, rid in enumerate(rids):
            self.assertEqual(hf.get(rid), f"record-{i:05d}".encode() * 10)
        self.assertEqual(hf.bpm.num_resident, min(8, hf.num_pages))

    def test_scan_returns_all_live_records_in_rid_order(self):
        hf = HeapFile(make_bpm())
        rids = [hf.insert(bytes([i % 256]) * 50) for i in range(500)]
        for rid in rids[::3]:
            hf.delete(rid)
        expected = {r: bytes([i % 256]) * 50 for i, r in enumerate(rids) if i % 3}
        scanned = list(hf.scan())
        self.assertEqual(dict(scanned), expected)
        self.assertEqual([r for r, _ in scanned], sorted(expected))

    def test_cold_scan_is_sequential(self):
        bpm = make_bpm(4)
        hf = HeapFile(bpm)
        for i in range(2000):
            hf.insert(b"x" * 100)
        bpm.clear()
        bpm.stats.reset()
        n = sum(1 for _ in hf.scan())
        self.assertEqual(n, 2000)
        s = bpm.stats
        self.assertEqual(s.page_reads, hf.num_pages)
        self.assertEqual(s.random_reads, 1)
        self.assertEqual(s.sequential_reads, hf.num_pages - 1)

    def test_scan_leaves_nothing_pinned(self):
        bpm = make_bpm(2)
        hf = HeapFile(bpm)
        for _ in range(300):
            hf.insert(b"y" * 100)
        it = hf.scan()
        next(it)
        del it
        for pid in hf.page_ids():
            self.assertEqual(bpm.pin_count(pid), 0)

    def test_update_keeps_rid(self):
        hf = HeapFile(make_bpm())
        rid = hf.insert(b"short")
        hf.update(rid, b"a much longer version of the record")
        self.assertEqual(hf.get(rid), b"a much longer version of the record")
        with self.assertRaises(PageFullError):
            hf.update(rid, b"z" * (MAX_RECORD_SIZE + 1))

    def test_oversized_record_rejected(self):
        hf = HeapFile(make_bpm())
        with self.assertRaises(PageFullError):
            hf.insert(b"z" * (MAX_RECORD_SIZE + 1))
        hf.insert(b"z" * MAX_RECORD_SIZE)

    def test_reopen_by_first_page_id(self):
        bpm = make_bpm()
        hf = HeapFile(bpm)
        for i in range(400):
            hf.insert(str(i).encode() * 20)
        reopened = HeapFile(bpm, hf.first_page_id)
        self.assertEqual(reopened.num_pages, hf.num_pages)
        self.assertEqual(reopened.last_page_id, hf.last_page_id)
        rid = reopened.insert(b"after reopen")
        self.assertEqual(hf.get(rid), b"after reopen")

    def test_two_heap_files_share_a_pool(self):
        bpm = make_bpm(4)
        a, b = HeapFile(bpm), HeapFile(bpm)
        for i in range(200):
            a.insert(b"A%d" % i)
            b.insert(b"B%d" % i)
        self.assertTrue(all(r.startswith(b"A") for _, r in a.scan()))
        self.assertTrue(all(r.startswith(b"B") for _, r in b.scan()))

    def test_missing_record(self):
        hf = HeapFile(make_bpm())
        rid = hf.insert(b"x")
        hf.delete(rid)
        with self.assertRaises(KeyError):
            hf.get(rid)
        with self.assertRaises(KeyError):
            hf.get(RID(rid.page_id, 99))

    def test_randomized_against_dict_model(self):
        rng = random.Random(42)
        hf = HeapFile(make_bpm(3))
        model: dict[RID, bytes] = {}
        for _ in range(4000):
            op = rng.random()
            if op < 0.6 or not model:
                rec = rng.randbytes(rng.randint(1, 300))
                model[hf.insert(rec)] = rec
            elif op < 0.8:
                rid = rng.choice(list(model))
                hf.delete(rid)
                del model[rid]
            else:
                rid = rng.choice(list(model))
                rec = rng.randbytes(rng.randint(1, 300))
                try:
                    hf.update(rid, rec)
                    model[rid] = rec
                except PageFullError:
                    pass
        self.assertEqual(dict(hf.scan()), model)


if __name__ == "__main__":
    unittest.main()
