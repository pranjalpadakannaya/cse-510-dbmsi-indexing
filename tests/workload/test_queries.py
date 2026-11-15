import math
import unittest
from collections import Counter

from dbmsi.workload.generator import generate
from dbmsi.workload.queries import (
    PointQuery,
    Query,
    QueryGenerator,
    RangeQuery,
    TermQuery,
    evaluate,
)

SELECTIVITIES = (0.0005, 0.001, 0.01, 0.05, 0.1, 0.5)


class TestQueries(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.records = generate(num_files=10000, seed=3, vocab_size=1500, term_skew=1.0)
        cls.n = len(cls.records)

    def setUp(self):
        self.gen = QueryGenerator(self.records, seed=1)

    def assertExact(self, q: Query):
        self.assertEqual(q.expected_count, len(evaluate(q, self.records)), q)

    def test_range_selectivity_is_close_and_exact(self):
        for attr in ("size", "mtime", "ctime", "atime"):
            for s in SELECTIVITIES:
                with self.subTest(attr=attr, s=s):
                    q = self.gen.range(attr, s)
                    self.assertExact(q)
                    self.assertLessEqual(q.low, q.high)
                    # Continuous attributes have few ties, so this is tight.
                    self.assertAlmostEqual(q.selectivity(self.n), s, delta=max(0.002, s * 0.05))

    def test_range_on_discrete_attribute_is_still_exact(self):
        q = self.gen.range("depth", 0.1)
        self.assertExact(q)
        self.assertGreaterEqual(q.expected_count, 1000)

    def test_point_queries(self):
        for attr in ("extension", "category", "uid", "mode", "depth"):
            with self.subTest(attr=attr):
                self.assertExact(self.gen.point(attr))
                self.assertExact(self.gen.point(attr, 0.05))
        counts = Counter(r.extension for r in self.records)
        q = self.gen.point("extension", 0.05)
        best = min(abs(math.log(c / self.n / 0.05)) for c in counts.values())
        self.assertLessEqual(abs(math.log(q.selectivity(self.n) / 0.05)), best + math.log(1.25) + 1e-9)

    def test_term_selectivity_tracks_target(self):
        for s in (0.001, 0.005, 0.02, 0.05):
            with self.subTest(s=s):
                q = self.gen.term(s)
                self.assertExact(q)
                ratio = q.selectivity(self.n) / s
                self.assertTrue(0.5 < ratio < 2.0, ratio)
        self.assertExact(self.gen.term())

    def test_conjunction_and_disjunction(self):
        for s in (0.0005, 0.002):
            q = self.gen.conjunction(s)
            self.assertExact(q)
            self.assertEqual((q.op, len(q.terms)), ("and", 2))
            self.assertGreater(q.expected_count, 0)
        q = self.gen.disjunction(0.04)
        self.assertExact(q)
        self.assertEqual((q.op, len(set(q.terms))), ("or", 2))
        self.assertAlmostEqual(q.selectivity(self.n), 0.04, delta=0.02)

    def test_selectivity_sweep_is_monotone(self):
        qs = self.gen.sweep("range:size", SELECTIVITIES, per_level=3)
        self.assertEqual(len(qs), 18)
        means = [sum(q.expected_count for q in qs[i:i + 3]) / 3 for i in range(0, 18, 3)]
        self.assertEqual(means, sorted(means))

    def test_mixed_workload(self):
        mix = {"range:size": 2, "range:mtime": 2, "point:extension": 1, "term": 1, "and": 1, "or": 1}
        qs = self.gen.workload(200, mix, (0.001, 0.01, 0.1))
        kinds = Counter(q.kind for q in qs)
        self.assertEqual(set(kinds), {"range", "point", "term"})
        for q in qs[:50]:
            self.assertExact(q)

    def test_deterministic(self):
        a = QueryGenerator(self.records, seed=9).sweep("term", (0.01,), 5)
        b = QueryGenerator(self.records, seed=9).sweep("term", (0.01,), 5)
        self.assertEqual(a, b)

    def test_serialization_round_trip(self):
        qs = [self.gen.range("mtime", 0.01), self.gen.point("uid"), self.gen.conjunction(0.001)]
        for q in qs:
            self.assertEqual(Query.from_dict(q.to_dict()), q)

    def test_rejects_bad_input(self):
        with self.assertRaises(ValueError):
            QueryGenerator([])
        with self.assertRaises(ValueError):
            self.gen.range("extension", 0.1)
        with self.assertRaises(ValueError):
            self.gen.range("size", 0)
        with self.assertRaises(ValueError):
            self.gen.point("path")
        with self.assertRaises(ValueError):
            self.gen.make("bogus")
        with self.assertRaises(ValueError):
            TermQuery(terms=("a",), op="xor")

    def test_matches_predicates(self):
        r = self.records[0]
        self.assertTrue(PointQuery(attribute="extension", value=r.extension).matches(r))
        self.assertTrue(RangeQuery(attribute="size", low=r.size, high=r.size).matches(r))
        self.assertTrue(TermQuery(terms=tuple(r.tokens[:1])).matches(r))
        self.assertFalse(TermQuery(terms=("zzzznotaterm",), op="or").matches(r))


if __name__ == "__main__":
    unittest.main()
