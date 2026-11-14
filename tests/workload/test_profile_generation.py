"""Generating from a measured profile should reproduce that profile."""

import os
import statistics
import tempfile
import unittest

from dbmsi.workload.generator import GeneratorConfig, generate
from dbmsi.workload.profile import MetadataProfile
from dbmsi.workload.scanner import scan_directory


def share(d: dict, key) -> float:
    return d.get(key, 0) / sum(d.values())


class TestProfileDrivenGeneration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # A "real" source with distinctive shape: few big extensions, shallow
        # tree, small files, one dominant owner, steep term skew.
        cls.source = generate(
            num_files=8000, seed=11, term_skew=1.3, vocab_size=800,
            extensions={"log": 50, "csv": 30, "py": 15, "": 5},
            depths={1: 3, 2: 6, 3: 1}, size_median=300, size_sigma=1.0,
            uids={0: 1, 1000: 9}, modes={0o640: 1},
        )
        cls.profile = MetadataProfile.from_records(cls.source)
        cls.config = GeneratorConfig.from_profile(cls.profile, num_files=8000, seed=5)
        cls.synthetic = generate(cls.config)
        cls.synthetic_profile = MetadataProfile.from_records(cls.synthetic)

    def test_categorical_distributions_match(self):
        p, q = self.profile, self.synthetic_profile
        for attr in ("extensions", "depths", "uids"):
            src, syn = getattr(p, attr), getattr(q, attr)
            self.assertEqual(set(syn), set(src), attr)
            for key in src:
                self.assertAlmostEqual(share(syn, key), share(src, key), delta=0.03,
                                       msg=f"{attr}[{key}]")
        self.assertEqual(q.modes, {0o640: 8000})

    def test_numeric_distributions_match(self):
        for attr in ("size", "mtime", "ctime", "atime"):
            src = statistics.median(getattr(r, attr) for r in self.source)
            syn = statistics.median(getattr(r, attr) for r in self.synthetic)
            self.assertAlmostEqual(syn / src, 1.0, delta=0.08, msg=attr)
        self.assertLessEqual(max(r.size for r in self.synthetic), self.profile.size_quantiles[-1] + 1)

    def test_terms_and_skew_match(self):
        p, q = self.profile, self.synthetic_profile
        self.assertAlmostEqual(q.term_skew, p.term_skew, delta=0.15)
        top_src = list(p.term_doc_freqs)[:20]
        top_syn = list(q.term_doc_freqs)[:20]
        self.assertGreaterEqual(len(set(top_src) & set(top_syn)), 15)
        self.assertEqual(self.config.vocab_size, p.vocabulary_size)

    def test_overrides_sweep_skew_on_realistic_base(self):
        flat = generate(GeneratorConfig.from_profile(self.profile, num_files=8000, term_skew=0.3))
        flat_profile = MetadataProfile.from_records(flat)
        self.assertLess(flat_profile.term_skew, self.synthetic_profile.term_skew - 0.5)
        self.assertAlmostEqual(
            share(flat_profile.extensions, "log"), share(self.profile.extensions, "log"), delta=0.03
        )

    def test_profile_from_real_scan(self):
        with tempfile.TemporaryDirectory() as root:
            for i in range(40):
                d = os.path.join(root, f"proj{i % 4}", "src")
                os.makedirs(d, exist_ok=True)
                with open(os.path.join(d, f"module_{i % 7}_v{i}.py"), "w") as f:
                    f.write("x" * i)
            profile = MetadataProfile.from_records(scan_directory(root))
        self.assertEqual(profile.extensions, {"py": 40})
        records = generate(GeneratorConfig.from_profile(profile, num_files=200))
        self.assertTrue(all(r.extension == "py" and r.depth == 2 for r in records))
        self.assertTrue(all(0 <= r.size <= 39 for r in records))

    def test_empty_profile_rejected(self):
        with self.assertRaises(ValueError):
            GeneratorConfig.from_profile(MetadataProfile())


if __name__ == "__main__":
    unittest.main()
