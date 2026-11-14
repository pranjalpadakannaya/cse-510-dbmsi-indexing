import random
import unittest

from dbmsi.workload.metadata import FileMetadata
from dbmsi.workload.profile import (
    MetadataProfile,
    fit_zipf_exponent,
    quantiles,
    stem_tokens,
)


def record(i, path, size=100, mtime=1e9, uid=501):
    return FileMetadata(i, path, size, mtime, mtime + 5, mtime + 60, uid, 0o644)


class TestHelpers(unittest.TestCase):
    def test_quantiles(self):
        self.assertEqual(quantiles([]), [])
        self.assertEqual(quantiles([5.0]), [5.0] * 101)
        qs = quantiles(list(range(101)))
        self.assertEqual(qs, [float(i) for i in range(101)])
        self.assertEqual(quantiles([0, 10], n=3), [0, 5, 10])

    def test_fit_zipf_recovers_exponent(self):
        for s in (0.0, 0.7, 1.0, 1.5):
            freqs = [round(1e6 * r ** -s) for r in range(1, 500)]
            self.assertAlmostEqual(fit_zipf_exponent(freqs), s, delta=0.02)
        self.assertEqual(fit_zipf_exponent([3]), 0.0)

    def test_stem_tokens_drop_extension(self):
        self.assertEqual(stem_tokens("Final_Report-v2.pdf"), ["final", "report", "v2"])
        self.assertEqual(stem_tokens("Makefile"), ["makefile"])


class TestProfile(unittest.TestCase):
    def setUp(self):
        rng = random.Random(0)
        paths = [
            "docs/report_final.pdf", "docs/report_draft.pdf", "src/main.py",
            "src/util/report.py", "img/IMG_001.jpg", "img/IMG_002.jpg", "README",
        ]
        self.records = [
            record(i, p, size=10 ** (i % 5), mtime=1e9 + i * 100, uid=501 if i % 3 else 0)
            for i, p in enumerate(paths)
        ]
        self.records += [record(100 + i, f"misc/f{i}.txt", size=rng.randrange(1000)) for i in range(20)]
        self.profile = MetadataProfile.from_records(self.records)

    def test_counts(self):
        p = self.profile
        self.assertEqual(p.num_files, 27)
        self.assertEqual(p.extensions["txt"], 20)
        self.assertEqual(p.extensions["pdf"], 2)
        self.assertEqual(p.extensions[""], 1)
        self.assertEqual(list(p.extensions)[0], "txt")  # most common first
        self.assertEqual(p.categories["document"], 22)
        self.assertEqual(p.term_doc_freqs["report"], 3)
        self.assertEqual(p.term_doc_freqs["img"], 2)
        self.assertNotIn("pdf", p.term_doc_freqs)
        self.assertEqual(p.depths, {0: 1, 1: 25, 2: 1})
        self.assertEqual(p.terms_per_name, {1: 3 + 20, 2: 4})
        self.assertEqual(sum(p.uids.values()), 27)
        self.assertEqual(p.ctime_offset_quantiles, [5.0] * 101)
        self.assertEqual(len(p.size_quantiles), 101)
        self.assertEqual(p.size_quantiles[-1], max(r.size for r in self.records))

    def test_top_terms_limit(self):
        p = MetadataProfile.from_records(self.records, top_terms=2)
        self.assertEqual(len(p.term_doc_freqs), 2)
        self.assertEqual(p.vocabulary_size, self.profile.vocabulary_size)

    def test_json_round_trip(self):
        again = MetadataProfile.from_json(self.profile.to_json())
        self.assertEqual(again, self.profile)

    def test_summary_and_empty(self):
        self.assertIn("files:            27", self.profile.summary())
        empty = MetadataProfile.from_records([])
        self.assertEqual(empty.num_files, 0)
        self.assertIn("files:", empty.summary())


if __name__ == "__main__":
    unittest.main()
