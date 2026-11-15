import random
import unittest

from dbmsi.storage.constants import INVALID_PAGE_ID, PAGE_SIZE
from dbmsi.storage.rid import RID
from dbmsi.storage.slotted_page import (
    HEADER_SIZE,
    MAX_RECORD_SIZE,
    SLOT_SIZE,
    PageFullError,
    SlottedPage,
)


def fresh() -> SlottedPage:
    return SlottedPage.init(bytearray(PAGE_SIZE))


class RIDTest(unittest.TestCase):
    def test_pack_roundtrip_and_ordering(self):
        r = RID(123456, 42)
        self.assertEqual(RID.unpack(r.pack()), r)
        self.assertEqual(len(r.pack()), RID.SIZE)
        self.assertLess(RID(1, 9), RID(2, 0))


class SlottedPageTest(unittest.TestCase):
    def test_empty_page(self):
        p = fresh()
        self.assertEqual(p.num_slots, 0)
        self.assertEqual(p.next_page_id, INVALID_PAGE_ID)
        self.assertEqual(p.free_space(), PAGE_SIZE - HEADER_SIZE)

    def test_insert_get(self):
        p = fresh()
        a = p.insert(b"hello")
        b = p.insert(b"")
        c = p.insert(b"world!")
        self.assertEqual((a, b, c), (0, 1, 2))
        self.assertEqual(p.get(a), b"hello")
        self.assertEqual(p.get(b), b"")
        self.assertEqual(p.get(c), b"world!")
        self.assertEqual(len(p), 3)

    def test_next_page_id_persists_across_ops(self):
        p = fresh()
        p.next_page_id = 99
        p.insert(b"x" * 10)
        p.delete(0)
        p.insert(b"y")
        self.assertEqual(p.next_page_id, 99)

    def test_delete_and_slot_reuse(self):
        p = fresh()
        for i in range(3):
            p.insert(bytes([i]) * 4)
        p.delete(1)
        with self.assertRaises(KeyError):
            p.get(1)
        self.assertEqual(p.insert(b"new"), 1)
        self.assertEqual(p.get(0), b"\x00" * 4)
        self.assertEqual(p.get(2), b"\x02" * 4)

    def test_trailing_delete_shrinks_directory(self):
        p = fresh()
        p.insert(b"a")
        p.insert(b"b")
        p.delete(1)
        self.assertEqual(p.num_slots, 1)
        p.delete(0)
        self.assertEqual(p.num_slots, 0)
        self.assertEqual(p.contiguous_free_space(), PAGE_SIZE - HEADER_SIZE)

    def test_fill_page_exactly(self):
        p = fresh()
        rec = b"r" * 96
        n = 0
        while p.can_insert(len(rec)):
            p.insert(rec)
            n += 1
        self.assertEqual(n, (PAGE_SIZE - HEADER_SIZE) // (len(rec) + SLOT_SIZE))
        with self.assertRaises(PageFullError):
            p.insert(rec)

    def test_max_record(self):
        p = fresh()
        p.insert(b"m" * MAX_RECORD_SIZE)
        self.assertEqual(p.free_space(), 0)
        with self.assertRaises(PageFullError):
            fresh().insert(b"m" * (MAX_RECORD_SIZE + 1))

    def test_insert_compacts_fragmented_space(self):
        p = fresh()
        rec = b"z" * 400
        ids = [p.insert(rec) for _ in range(9)]
        for sid in ids[::2]:
            p.delete(sid)
        big = b"B" * 1500  # fits only after compaction
        self.assertLess(p.contiguous_free_space(), len(big))
        sid = p.insert(big)
        self.assertEqual(p.get(sid), big)
        for s in ids[1::2]:
            self.assertEqual(p.get(s), rec)

    def test_update_shrink_grow_and_overflow(self):
        p = fresh()
        a = p.insert(b"aaaa")
        b = p.insert(b"bbbb")
        p.update(a, b"A")
        self.assertEqual(p.get(a), b"A")
        p.update(a, b"A" * 1000)
        self.assertEqual(p.get(a), b"A" * 1000)
        self.assertEqual(p.get(b), b"bbbb")
        with self.assertRaises(PageFullError):
            p.update(b, b"x" * PAGE_SIZE)
        self.assertEqual(p.get(b), b"bbbb")

    def test_view_writes_through(self):
        buf = bytearray(PAGE_SIZE)
        SlottedPage.init(buf).insert(b"persist")
        self.assertEqual(SlottedPage(buf).get(0), b"persist")

    def test_randomized_against_dict_model(self):
        rng = random.Random(510)
        p = fresh()
        model: dict[int, bytes] = {}
        for _ in range(3000):
            op = rng.random()
            if op < 0.5:
                rec = rng.randbytes(rng.randint(0, 200))
                if p.can_insert(len(rec)):
                    model[p.insert(rec)] = rec
                else:
                    with self.assertRaises(PageFullError):
                        p.insert(rec)
            elif op < 0.8 and model:
                sid = rng.choice(list(model))
                p.delete(sid)
                del model[sid]
            elif model:
                sid = rng.choice(list(model))
                rec = rng.randbytes(rng.randint(0, 300))
                try:
                    p.update(sid, rec)
                    model[sid] = rec
                except PageFullError:
                    pass
            self.assertEqual(dict(p.records()), model)


if __name__ == "__main__":
    unittest.main()
