"""Each access method, checked against brute-force evaluation of the workload."""

import unittest

from dbmsi.bench import BitmapMethod, BTreeMethod, InvertedMethod, SequentialScan
from dbmsi.bench.methods import codec_for
from dbmsi.btree import FloatKey, IntKey, StringKey
from dbmsi.storage import BufferPoolManager, DiskManager, HeapFile, measure
from dbmsi.workload import PointQuery, QueryGenerator, RangeQuery, TermQuery, evaluate, generate

SPECS = ["point:extension", "point:category", "point:uid", "range:size", "range:mtime",
         "range:depth", "term", "and", "or", "and:3", "or:3"]
SELECTIVITIES = [0.001, 0.01, 0.1, 0.5]


class Fixture:
    def __init__(self, n=1500, extra=0, pool_size=32):
        records = generate(num_files=n + extra, seed=3)
        self.base, self.extra = records[:n], records[n:]
        self.bpm = BufferPoolManager(DiskManager(), pool_size)
        self.heap = HeapFile(self.bpm)
        self.rows = [(self.heap.insert(r.pack()), r) for r in self.base]
        self.queries = QueryGenerator(self.base, seed=5).workload(
            60, {s: 1 for s in SPECS}, SELECTIVITIES
        )
        self.methods = [
            SequentialScan(self.bpm, self.heap),
            BTreeMethod(self.bpm, self.heap, ["size", "mtime", "depth", "extension", "category",
                                              "uid"]),
            *(BitmapMethod(self.bpm, self.heap, ["extension", "category", "uid", "depth"], e)
              for e in ("plain", "wah", "roaring")),
            InvertedMethod(self.bpm, self.heap),
        ]
        for m in self.methods:
            m.build(self.rows)

    def file_ids(self, rids):
        by_rid = {rid: r.file_id for rid, r in self.rows}
        return sorted(by_rid[rid] for rid in rids)


class TestAgainstBruteForce(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.f = Fixture()

    def test_every_supported_query_matches_brute_force(self):
        checked = 0
        for q in self.f.queries:
            expected = evaluate(q, self.f.base)
            self.assertEqual(len(expected), q.expected_count)
            for m in self.f.methods:
                if m.supports(q):
                    with self.subTest(method=m.name, query=q):
                        self.assertEqual(self.f.file_ids(m.query(q)), expected)
                    checked += 1
        self.assertGreater(checked, len(self.f.queries) * 2)

    def test_supported_query_types(self):
        point = PointQuery(attribute="extension", value="pdf")
        size = RangeQuery(attribute="size", low=0, high=100)
        depth = RangeQuery(attribute="depth", low=1, high=3)
        term = TermQuery(terms=("report",))
        scan, btree, bitmap, inverted = (self.f.methods[i] for i in (0, 1, 4, 5))
        for q in (point, size, depth, term):
            self.assertTrue(scan.supports(q))
        self.assertEqual([btree.supports(q) for q in (point, size, depth, term)],
                         [True, True, True, False])
        self.assertEqual([bitmap.supports(q) for q in (point, size, depth, term)],
                         [True, False, True, False])
        self.assertEqual([inverted.supports(q) for q in (point, size, depth, term)],
                         [False, False, False, True])

    def test_missing_values_and_terms_return_nothing(self):
        btree, bitmap, inverted = self.f.methods[1], self.f.methods[4], self.f.methods[5]
        self.assertEqual(btree.query(PointQuery(attribute="extension", value="nope")), set())
        self.assertEqual(bitmap.query(PointQuery(attribute="uid", value=-7)), set())
        self.assertEqual(inverted.query(TermQuery(terms=("zzzqqq",))), set())
        self.assertEqual(inverted.query(TermQuery(terms=("zzzqqq", "a"), op="and")), set())

    def test_queries_read_pages_through_the_buffer_pool(self):
        q = TermQuery(terms=(self.f.base[0].tokens[0],))
        for m in self.f.methods:
            if m.supports(q):
                self.f.bpm.clear()
                with measure(self.f.bpm.stats) as io:
                    m.query(q)
                self.assertGreater(io.delta.page_reads, 0, m.name)

    def test_sizes(self):
        scan, btree, plain, wah, roaring, inverted = self.f.methods
        self.assertEqual(scan.size_pages(), self.f.heap.num_pages)
        for m in self.f.methods:
            self.assertGreater(m.size_pages(), 0, m.name)
        # Inverted lists are packed back to back, not one page per term.
        self.assertLess(inverted.size_pages(), len(inverted.terms))
        self.assertLessEqual(inverted.size_bytes(), inverted.size_pages() * 4096)
        self.assertEqual(set(btree.describe()), set(btree.attributes))


class TestInserts(unittest.TestCase):
    def test_methods_stay_correct_after_inserts(self):
        f = Fixture(n=800, extra=120)
        rids = [f.heap.insert(r.pack()) for r in f.extra]
        for m in f.methods:
            for rid, r in zip(rids, f.extra):
                m.insert(rid, r)
        f.rows += list(zip(rids, f.extra))
        everything = f.base + f.extra
        for q in f.queries:
            expected = evaluate(q, everything)
            for m in f.methods:
                if m.supports(q):
                    with self.subTest(method=m.name, query=q):
                        self.assertEqual(f.file_ids(m.query(q)), expected)


class TestCodecs(unittest.TestCase):
    def test_codec_per_attribute(self):
        rows = [(None, r) for r in generate(num_files=50, seed=1)]
        self.assertEqual(codec_for("size"), IntKey())
        self.assertEqual(codec_for("mtime"), FloatKey())
        self.assertIsInstance(codec_for("extension", rows), StringKey)
        with self.assertRaises(ValueError):
            codec_for("path")


if __name__ == "__main__":
    unittest.main()
