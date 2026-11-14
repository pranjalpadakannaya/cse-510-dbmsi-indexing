import random
import unittest

from dbmsi.btree.keys import (
    IntKey,
    StringKey,
    codec_from_meta,
    decode_rid,
    encode_rid,
)
from dbmsi.storage import RID


class TestRidEncoding(unittest.TestCase):
    def test_round_trip(self):
        for rid in [RID(0, 0), RID(7, 65535), RID(2**31 - 1, 3), RID(-1, 0)]:
            self.assertEqual(decode_rid(encode_rid(rid)), rid)

    def test_byte_order_matches_rid_order(self):
        rng = random.Random(1)
        rids = [RID(rng.randrange(-5, 1000), rng.randrange(0, 600)) for _ in range(500)]
        self.assertEqual(sorted(rids, key=encode_rid), sorted(rids))


class TestIntKey(unittest.TestCase):
    def setUp(self):
        self.codec = IntKey()

    def test_round_trip_and_order(self):
        rng = random.Random(2)
        keys = [rng.randrange(-(2**63), 2**63) for _ in range(500)] + [0, -1, 1]
        for k in keys:
            self.assertEqual(self.codec.decode(self.codec.encode(k)), k)
        self.assertEqual(sorted(keys, key=self.codec.encode), sorted(keys))

    def test_rejects_bad_keys(self):
        with self.assertRaises(OverflowError):
            self.codec.encode(2**63)
        with self.assertRaises(TypeError):
            self.codec.encode("5")
        with self.assertRaises(TypeError):
            self.codec.encode(True)

    def test_entry_order_breaks_ties_by_rid(self):
        a = self.codec.encode_entry(5, RID(1, 2))
        b = self.codec.encode_entry(5, RID(1, 3))
        c = self.codec.encode_entry(6, RID(0, 0))
        self.assertLess(a, b)
        self.assertLess(b, c)
        self.assertEqual(self.codec.decode_entry(b), (5, RID(1, 3)))
        self.assertEqual(len(a), self.codec.entry_size)


class TestStringKey(unittest.TestCase):
    def test_round_trip_and_order(self):
        codec = StringKey(16)
        keys = ["", "a", "ab", "b", "pdf", "txt", "Z", "résumé", "日本"]
        for k in keys:
            self.assertEqual(codec.decode(codec.encode(k)), k)
        self.assertEqual(sorted(keys, key=codec.encode), sorted(keys))

    def test_rejects_bad_keys(self):
        codec = StringKey(4)
        with self.assertRaises(ValueError):
            codec.encode("toolong")
        with self.assertRaises(ValueError):
            codec.encode("a\x00b")
        with self.assertRaises(TypeError):
            codec.encode(5)


class TestCodecFromMeta(unittest.TestCase):
    def test_rebuilds_codecs(self):
        for codec in [IntKey(), StringKey(32)]:
            self.assertEqual(codec_from_meta(codec.type_id, codec.param), codec)
        with self.assertRaises(ValueError):
            codec_from_meta(99, 0)


if __name__ == "__main__":
    unittest.main()
