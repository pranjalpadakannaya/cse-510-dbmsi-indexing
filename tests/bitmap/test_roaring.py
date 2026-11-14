"""Roaring-specific behaviour: chunks, container forms and sizes.

The operations shared by every encoding are covered in ``test_bitmaps``;
these tests use bitmaps longer than one 65,536-bit chunk.
"""

import random
import unittest

from dbmsi.bitmap import Bitmap, BitmapIndex, RoaringBitmap, WAHBitmap
from dbmsi.bitmap.roaring import (
    ARRAY,
    ARRAY_MAX,
    BITSET,
    BITSET_BYTES,
    CHUNK_SIZE,
    RUN,
    _choose,
    _from_int,
    _from_range,
    _from_sorted,
)
from dbmsi.storage import RID, BufferPoolManager, DiskManager


def kinds(bitmap):
    return {k: n for k, n in bitmap.container_kinds().items() if n}


def mixed_positions(rng, num_bits):
    """Sparse, dense and run-shaped regions in different chunks."""
    out = set()
    for start in range(0, num_bits, CHUNK_SIZE):
        end = min(start + CHUNK_SIZE, num_bits)
        shape = rng.choice(("empty", "sparse", "dense", "runs", "full"))
        if shape == "sparse":
            out.update(rng.randrange(start, end) for _ in range(rng.randint(1, 300)))
        elif shape == "dense":
            out.update(p for p in range(start, end) if rng.random() < 0.6)
        elif shape == "runs":
            pos = start
            while pos < end:
                length = rng.randint(200, 4000)
                out.update(range(pos, min(pos + length, end)))
                pos += length + rng.randint(200, 4000)
        elif shape == "full":
            out.update(range(start, end))
    return sorted(out)


class TestContainerForms(unittest.TestCase):
    def test_form_is_the_smallest_serialization(self):
        self.assertEqual(_choose(10, 10), ARRAY)  # 20 B array vs 42 B of runs
        self.assertEqual(_choose(ARRAY_MAX, ARRAY_MAX), ARRAY)
        self.assertEqual(_choose(ARRAY_MAX + 1, ARRAY_MAX + 1), BITSET)
        self.assertEqual(_choose(1000, 3), RUN)  # 14 B of runs vs 2000 B array
        self.assertEqual(_choose(60_000, 5), RUN)
        self.assertEqual(_choose(60_000, 3000), BITSET)  # 12 KB of runs vs 8 KB
        self.assertEqual(_choose(2, 1), ARRAY)  # a run is not smaller here

    def test_builders_agree_on_form_and_contents(self):
        rng = random.Random(1)
        shapes = [
            [5],
            [0, 1],
            list(range(100, 5000)),
            sorted(rng.sample(range(CHUNK_SIZE), 50)),
            sorted(rng.sample(range(CHUNK_SIZE), ARRAY_MAX)),
            sorted(rng.sample(range(CHUNK_SIZE), ARRAY_MAX + 1)),
            sorted(rng.sample(range(CHUNK_SIZE), 40_000)),
            list(range(CHUNK_SIZE)),
            [0, CHUNK_SIZE - 1],
        ]
        for values in shapes:
            from_list = _from_sorted(values)
            from_bits = _from_int(from_list.as_int())
            with self.subTest(n=len(values), kind=from_list.kind):
                self.assertEqual(from_list, from_bits)
                self.assertEqual(list(from_list.values()), values)
                self.assertEqual(from_list.cardinality, len(values))
                self.assertEqual(len(from_list.payload()), from_list.payload_bytes)
                for v in (values[0], values[-1], values[len(values) // 2]):
                    self.assertTrue(from_list.contains(v))
        self.assertIsNone(_from_sorted([]))
        self.assertIsNone(_from_int(0))
        self.assertIsNone(_from_range(0))
        self.assertEqual(_from_range(CHUNK_SIZE).kind, RUN)
        self.assertEqual(_from_range(2).kind, ARRAY)

    def test_each_region_gets_its_own_form(self):
        rng = random.Random(2)
        positions = (
            sorted(rng.sample(range(0, CHUNK_SIZE), 100))  # chunk 0: sparse
            + [CHUNK_SIZE + p for p in range(CHUNK_SIZE) if p % 3]  # chunk 1: dense
            + list(range(3 * CHUNK_SIZE + 10, 3 * CHUNK_SIZE + 50_000))  # chunk 3: one run
        )
        bm = RoaringBitmap.from_positions(positions, 5 * CHUNK_SIZE)
        self.assertEqual(bm.container_kinds(), {ARRAY: 1, BITSET: 1, RUN: 1})
        self.assertEqual(bm.num_containers, 3)  # chunks 2 and 4 take no space
        self.assertEqual(bm.size_bytes, 8 + 3 * 5 + 200 + BITSET_BYTES + 6)
        self.assertEqual(len(bm.to_bytes()), bm.size_bytes)


class TestAcrossChunks(unittest.TestCase):
    def test_matches_uncompressed_on_mixed_bitmaps(self):
        rng = random.Random(3)
        for num_bits in (CHUNK_SIZE - 1, CHUNK_SIZE, CHUNK_SIZE + 1, 6 * CHUNK_SIZE + 12345):
            a, b = mixed_positions(rng, num_bits), mixed_positions(rng, num_bits)
            ra, rb = (RoaringBitmap.from_positions(p, num_bits) for p in (a, b))
            pa, pb = (Bitmap.from_positions(p, num_bits) for p in (a, b))
            with self.subTest(bits=num_bits):
                self.assertEqual(list(ra.positions()), a)
                self.assertEqual(ra.count(), len(a))
                for name, got, want in (
                    ("and", ra & rb, pa & pb),
                    ("or", ra | rb, pa | pb),
                    ("xor", ra ^ rb, pa ^ pb),
                    ("not", ~ra, ~pa),
                    ("andnot", ra & ~rb, pa & ~pb),
                ):
                    expected = list(want.positions())
                    self.assertEqual(list(got.positions()), expected, name)
                    self.assertEqual(got.count(), len(expected), name)
                    # Canonical: same bits, same containers.
                    self.assertEqual(got, RoaringBitmap.from_positions(expected, num_bits), name)
                    self.assertEqual(RoaringBitmap.from_bytes(got.to_bytes()), got, name)
                for p in rng.sample(range(num_bits), 200):
                    self.assertEqual(ra.get(p), pa.get(p))

    def test_and_ignores_chunks_missing_on_either_side(self):
        left = RoaringBitmap.from_positions([5, 3 * CHUNK_SIZE + 1], 10 * CHUNK_SIZE)
        right = RoaringBitmap.from_positions(
            [CHUNK_SIZE + 2, 3 * CHUNK_SIZE + 1, 7 * CHUNK_SIZE], 10 * CHUNK_SIZE
        )
        both = left & right
        self.assertEqual(list(both.positions()), [3 * CHUNK_SIZE + 1])
        self.assertEqual(both.num_containers, 1)
        self.assertEqual((left & RoaringBitmap.zeros(10 * CHUNK_SIZE)).num_containers, 0)

    def test_xor_with_itself_leaves_no_containers(self):
        rng = random.Random(4)
        bm = RoaringBitmap.from_positions(mixed_positions(rng, 4 * CHUNK_SIZE), 4 * CHUNK_SIZE)
        self.assertEqual(bm ^ bm, RoaringBitmap.zeros(4 * CHUNK_SIZE))

    def test_not_of_empty_and_full(self):
        num_bits = 3 * CHUNK_SIZE + 100
        full = ~RoaringBitmap.zeros(num_bits)
        self.assertEqual(full, RoaringBitmap.ones(num_bits))
        self.assertEqual(full.count(), num_bits)
        self.assertEqual(kinds(full), {RUN: 4})  # one run per chunk
        self.assertLess(full.size_bytes, 60)
        self.assertEqual(~full, RoaringBitmap.zeros(num_bits))
        self.assertEqual((~full).num_containers, 0)

    def test_or_shares_but_never_changes_operand_containers(self):
        a = RoaringBitmap.from_positions([1, 2, 3], 2 * CHUNK_SIZE)
        b = RoaringBitmap.from_positions([CHUNK_SIZE + 9], 2 * CHUNK_SIZE)
        union = a | b
        union.append(True)
        union.append(True)
        self.assertEqual(list(a.positions()), [1, 2, 3])
        self.assertEqual(list(b.positions()), [CHUNK_SIZE + 9])
        self.assertEqual(len(a), 2 * CHUNK_SIZE)

    def test_append_across_a_chunk_boundary(self):
        bm = RoaringBitmap.zeros(CHUNK_SIZE - 2)
        expected = []
        for i in range(6):
            if i != 3:
                expected.append(len(bm))
            bm.append(i != 3)
        self.assertEqual(len(bm), CHUNK_SIZE + 4)
        self.assertEqual(list(bm.positions()), expected)
        self.assertEqual(bm.num_containers, 2)
        self.assertEqual(bm, RoaringBitmap.from_positions(expected, CHUNK_SIZE + 4))

    def test_corrupt_data_is_rejected(self):
        data = RoaringBitmap.from_positions([1, 70_000, 200_000], 300_000).to_bytes()
        with self.assertRaises(ValueError):
            RoaringBitmap.from_bytes(data[:-1])
        with self.assertRaises(ValueError):
            RoaringBitmap.from_bytes(data[:10] + b"\x09" + data[11:])  # unknown kind


class TestSizes(unittest.TestCase):
    def test_sparse_bitmap_costs_two_bytes_per_set_bit(self):
        num_bits = 1_000_000
        positions = list(range(0, num_bits, 1000))
        roaring = RoaringBitmap.from_positions(positions, num_bits)
        self.assertEqual(kinds(roaring), {ARRAY: 16})
        self.assertEqual(roaring.size_bytes, 8 + 16 * 5 + 2 * len(positions))
        # WAH needs a literal and a fill word (8 bytes) per isolated bit.
        self.assertLess(roaring.size_bytes, WAHBitmap.from_positions(positions, num_bits).size_bytes)

    def test_dense_random_bitmap_never_exceeds_uncompressed_by_much(self):
        rng = random.Random(6)
        num_bits = 4 * CHUNK_SIZE
        positions = [p for p in range(num_bits) if rng.random() < 0.5]
        roaring = RoaringBitmap.from_positions(positions, num_bits)
        self.assertEqual(kinds(roaring), {BITSET: 4})
        self.assertEqual(roaring.size_bytes, 8 + 4 * (5 + BITSET_BYTES))
        self.assertLess(
            roaring.size_bytes, WAHBitmap.from_positions(positions, num_bits).size_bytes
        )

    def test_index_stats_show_roaring_smaller_than_plain_at_high_cardinality(self):
        """Many distinct values means sparse bitmaps, where arrays win."""
        rng = random.Random(8)
        n = 60_000
        rows = [(RID(i // 50, i % 50), (f"v{rng.randrange(500)}",)) for i in range(n)]
        sizes = {}
        for encoding in ("plain", "wah", "roaring"):
            index = BitmapIndex(
                BufferPoolManager(DiskManager(), pool_size=128), ["value"], encoding=encoding
            )
            index.bulk_load(rows)
            sizes[encoding] = index.stats().total_bitmap_bytes
        self.assertEqual(sizes["plain"], 500 * (4 + n // 8))
        self.assertLess(sizes["roaring"], sizes["wah"])
        self.assertLess(sizes["wah"], sizes["plain"])
        self.assertEqual(sizes["roaring"], 500 * (8 + 5) + 2 * n)  # one array per value


if __name__ == "__main__":
    unittest.main()
