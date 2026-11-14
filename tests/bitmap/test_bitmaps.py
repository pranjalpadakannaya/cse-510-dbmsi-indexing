"""Uncompressed and WAH bitmaps, checked against plain Python sets."""

import random
import unittest

from dbmsi.bitmap import Bitmap, WAHBitmap
from dbmsi.bitmap.wah import (
    ALL_ONES,
    FILL_BIT,
    FILL_FLAG,
    MAX_RUN,
    _append_fill,
    _append_group,
    _pop_group,
)

ENCODINGS = (Bitmap, WAHBitmap)


def random_positions(rng, num_bits, density):
    return [i for i in range(num_bits) if rng.random() < density]


def clustered_positions(rng, num_bits, runs):
    """Long runs of ones and zeros, the case fills are made for."""
    out, pos, on = [], 0, False
    while pos < num_bits:
        length = rng.randint(1, max(1, num_bits // runs))
        if on:
            out.extend(range(pos, min(pos + length, num_bits)))
        pos += length
        on = not on
    return out


class TestBothEncodings(unittest.TestCase):
    def cases(self):
        rng = random.Random(7)
        for num_bits in (0, 1, 30, 31, 32, 61, 62, 63, 500, 4000):
            for density in (0.0, 0.002, 0.05, 0.5, 0.95, 1.0):
                yield num_bits, random_positions(rng, num_bits, density)
            yield num_bits, clustered_positions(rng, num_bits, 6)

    def test_round_trip_of_positions_count_and_get(self):
        for cls in ENCODINGS:
            for num_bits, positions in self.cases():
                with self.subTest(cls=cls.__name__, bits=num_bits, set=len(positions)):
                    bm = cls.from_positions(positions, num_bits)
                    self.assertEqual(len(bm), num_bits)
                    self.assertEqual(list(bm.positions()), positions)
                    self.assertEqual(bm.count(), len(positions))
                    wanted = set(positions)
                    for i in range(0, num_bits, max(1, num_bits // 50)):
                        self.assertEqual(bm.get(i), i in wanted)

    def test_serialization_round_trip(self):
        for cls in ENCODINGS:
            for num_bits, positions in self.cases():
                with self.subTest(cls=cls.__name__, bits=num_bits, set=len(positions)):
                    bm = cls.from_positions(positions, num_bits)
                    data = bm.to_bytes()
                    self.assertEqual(len(data), bm.size_bytes)
                    self.assertEqual(cls.from_bytes(data), bm)

    def test_logical_operations_match_set_algebra(self):
        rng = random.Random(11)
        for cls in ENCODINGS:
            for num_bits in (1, 31, 62, 93, 700, 3001):
                for da, db in ((0.01, 0.01), (0.5, 0.02), (0.5, 0.5), (0.0, 0.3), (1.0, 0.3)):
                    a = set(random_positions(rng, num_bits, da))
                    b = set(clustered_positions(rng, num_bits, 8)) if db == 0.3 else set(
                        random_positions(rng, num_bits, db)
                    )
                    ba = cls.from_positions(sorted(a), num_bits)
                    bb = cls.from_positions(sorted(b), num_bits)
                    everything = set(range(num_bits))
                    with self.subTest(cls=cls.__name__, bits=num_bits, da=da, db=db):
                        self.assertEqual(list((ba & bb).positions()), sorted(a & b))
                        self.assertEqual(list((ba | bb).positions()), sorted(a | b))
                        self.assertEqual(list((ba ^ bb).positions()), sorted(a ^ b))
                        self.assertEqual(list((~ba).positions()), sorted(everything - a))
                        self.assertEqual(~~ba, ba)
                        self.assertEqual(
                            list((ba & ~bb).positions()), sorted(a - b)
                        )

    def test_results_are_canonical(self):
        """The same set of bits compares equal however it was produced."""
        rng = random.Random(3)
        for cls in ENCODINGS:
            for num_bits in (62, 1000):
                a = set(clustered_positions(rng, num_bits, 5))
                b = set(random_positions(rng, num_bits, 0.3))
                ba = cls.from_positions(sorted(a), num_bits)
                bb = cls.from_positions(sorted(b), num_bits)
                self.assertEqual(ba & bb, cls.from_positions(sorted(a & b), num_bits))
                self.assertEqual(ba | bb, cls.from_positions(sorted(a | b), num_bits))
                self.assertEqual(~ba, cls.from_positions(sorted(set(range(num_bits)) - a), num_bits))

    def test_zeros_and_ones(self):
        for cls in ENCODINGS:
            for num_bits in (0, 1, 31, 40, 62, 1000):
                self.assertEqual(cls.zeros(num_bits).count(), 0)
                self.assertEqual(list(cls.ones(num_bits).positions()), list(range(num_bits)))
                self.assertEqual(~cls.zeros(num_bits), cls.ones(num_bits))
                self.assertEqual(~cls.ones(num_bits), cls.zeros(num_bits))

    def test_append_matches_building_from_positions(self):
        rng = random.Random(5)
        for cls in ENCODINGS:
            bm = cls.zeros(0)
            positions = []
            for i in range(700):
                # Runs of equal bits, so fills are created and reopened.
                bit = (i // 40) % 3 == 0 or rng.random() < 0.05
                bm.append(bit)
                if bit:
                    positions.append(i)
                if i % 97 == 0:
                    self.assertEqual(bm, cls.from_positions(positions, i + 1))
            self.assertEqual(bm, cls.from_positions(positions, 700))

    def test_resized_pads_with_zeros(self):
        for cls in ENCODINGS:
            bm = cls.from_positions([0, 5, 40], 41)
            longer = bm.resized(5000)
            self.assertEqual(len(longer), 5000)
            self.assertEqual(list(longer.positions()), [0, 5, 40])
            self.assertEqual(longer, cls.from_positions([0, 5, 40], 5000))
            self.assertEqual(len(bm), 41)  # the original is unchanged
            with self.assertRaises(ValueError):
                bm.resized(10)

    def test_operands_of_different_lengths(self):
        for cls in ENCODINGS:
            short = cls.from_positions([1, 30], 31)
            long = cls.from_positions([1, 100, 200], 300)
            self.assertEqual(list((short & long).positions()), [1])
            self.assertEqual(list((short | long).positions()), [1, 30, 100, 200])
            self.assertEqual(len(short | long), 300)

    def test_invalid_positions_are_rejected(self):
        for cls in ENCODINGS:
            with self.assertRaises(ValueError):
                cls.from_positions([10], 10)
            with self.assertRaises(ValueError):
                cls.from_positions([-1], 10)
            with self.assertRaises(IndexError):
                cls.zeros(10).get(10)
        with self.assertRaises(ValueError):
            WAHBitmap.from_positions([5, 3], 10)
        with self.assertRaises(ValueError):
            WAHBitmap.from_positions([3, 3], 10)


class TestWAHAgainstUncompressed(unittest.TestCase):
    def test_same_answers_as_uncompressed_on_random_expressions(self):
        rng = random.Random(21)
        num_bits = 2500
        sets = [
            random_positions(rng, num_bits, 0.001),
            random_positions(rng, num_bits, 0.2),
            clustered_positions(rng, num_bits, 4),
            clustered_positions(rng, num_bits, 40),
            list(range(num_bits)),
            [],
        ]
        plain = [Bitmap.from_positions(s, num_bits) for s in sets]
        wah = [WAHBitmap.from_positions(s, num_bits) for s in sets]
        for _ in range(300):
            i, j, k = (rng.randrange(len(sets)) for _ in range(3))
            p = (plain[i] & ~plain[j]) | (plain[k] ^ plain[j])
            w = (wah[i] & ~wah[j]) | (wah[k] ^ wah[j])
            self.assertEqual(list(w.positions()), list(p.positions()))
            self.assertEqual(w.count(), p.count())


class TestWAHEncoding(unittest.TestCase):
    def test_sparse_bitmap_is_much_smaller_than_uncompressed(self):
        num_bits = 1_000_000
        positions = list(range(0, num_bits, 10_000))  # 100 set bits
        wah = WAHBitmap.from_positions(positions, num_bits)
        plain = Bitmap.from_positions(positions, num_bits)
        self.assertEqual(plain.size_bytes, 4 + 125_000)
        # One literal and one fill per set bit.
        self.assertLessEqual(wah.num_words, 2 * len(positions) + 1)
        self.assertLess(wah.size_bytes * 100, plain.size_bytes)

    def test_incompressible_bitmap_costs_32_bits_per_31(self):
        num_bits = 31 * 1000
        positions = list(range(0, num_bits, 2))
        wah = WAHBitmap.from_positions(positions, num_bits)
        self.assertEqual(wah.num_words, 1000)

    def test_runs_become_single_fill_words(self):
        self.assertEqual(WAHBitmap.zeros(31 * 5000).num_words, 1)
        self.assertEqual(WAHBitmap.ones(31 * 5000).num_words, 1)
        self.assertEqual(WAHBitmap.ones(31 * 5000 + 3).num_words, 2)
        mixed = WAHBitmap.from_positions(range(31 * 10, 31 * 20), 31 * 30)
        self.assertEqual(mixed.num_words, 3)

    def test_partial_last_group_is_never_part_of_a_one_fill(self):
        bm = ~WAHBitmap.zeros(40)
        self.assertEqual(bm.count(), 40)
        self.assertEqual(list(bm.positions()), list(range(40)))
        self.assertEqual(bm, WAHBitmap.ones(40))

    def test_and_skips_over_zero_fills(self):
        """An AND with an almost empty bitmap does not walk the dense one."""
        num_bits = 31 * 100_000
        dense = WAHBitmap.from_positions(range(0, num_bits, 2), num_bits)
        sparse = WAHBitmap.from_positions([5, num_bits - 4], num_bits)
        self.assertEqual(dense.num_words, 100_000)
        result = dense & sparse
        self.assertEqual(list(result.positions()), [num_bits - 4])  # 5 is odd
        self.assertLessEqual(result.num_words, 3)

    def test_fill_runs_longer_than_one_word_can_count(self):
        words = []
        _append_fill(words, 0, MAX_RUN + 5)
        self.assertEqual(words, [FILL_FLAG | MAX_RUN, FILL_FLAG | 5])
        _append_fill(words, 0, 2)
        self.assertEqual(words[-1], FILL_FLAG | 7)
        _append_fill(words, 1, 1)
        self.assertEqual(words[-1], FILL_FLAG | FILL_BIT | 1)
        self.assertEqual(_pop_group(words), ALL_ONES)
        self.assertEqual(_pop_group(words), 0)
        self.assertEqual(words[-1], FILL_FLAG | 6)

    def test_group_helpers_keep_literals_and_fills_apart(self):
        words = []
        _append_group(words, 0b101)
        _append_group(words, ALL_ONES)
        _append_group(words, ALL_ONES)
        _append_group(words, 0)
        self.assertEqual(words, [0b101, FILL_FLAG | FILL_BIT | 2, FILL_FLAG | 1])
        # A literal with its top data bit set is not mistaken for a fill.
        _append_group(words, 1 << 30)
        _append_fill(words, 1, 1)
        self.assertEqual(words[-2:], [1 << 30, FILL_FLAG | FILL_BIT | 1])

    def test_truncated_data_is_rejected(self):
        data = WAHBitmap.from_positions([1, 500], 1000).to_bytes()
        with self.assertRaises(ValueError):
            WAHBitmap.from_bytes(data[:-4])
        with self.assertRaises(ValueError):
            Bitmap.from_bytes(Bitmap.ones(1000).to_bytes()[:-1])


if __name__ == "__main__":
    unittest.main()
