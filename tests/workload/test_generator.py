import random
import statistics
import unittest
from collections import Counter

from dbmsi.workload.generator import (
    GeneratorConfig,
    QuantileSampler,
    SyntheticGenerator,
    WeightedSampler,
    generate,
    synthetic_words,
    zipf_weights,
)
from dbmsi.workload.metadata import tokenize
from dbmsi.workload.profile import MetadataProfile


class TestSamplers(unittest.TestCase):
    def test_weighted_sampler_frequencies(self):
        rng = random.Random(1)
        s = WeightedSampler(["a", "b", "c"], [1, 2, 7])
        counts = Counter(s.sample(rng) for _ in range(20000))
        self.assertAlmostEqual(counts["c"] / 20000, 0.7, delta=0.02)
        self.assertAlmostEqual(counts["a"] / 20000, 0.1, delta=0.02)
        with self.assertRaises(ValueError):
            WeightedSampler(["a"], [0])

    def test_zipf_weights(self):
        self.assertEqual(zipf_weights(3, 0), [1.0, 1.0, 1.0])
        self.assertEqual(zipf_weights(3, 1), [1.0, 0.5, 1 / 3])

    def test_quantile_sampler_reproduces_distribution(self):
        rng = random.Random(2)
        qs = [float(i) for i in range(101)]  # uniform on [0, 100]
        s = QuantileSampler(qs)
        xs = [s.sample(rng) for _ in range(20000)]
        self.assertTrue(all(0 <= x <= 100 for x in xs))
        self.assertAlmostEqual(statistics.median(xs), 50, delta=2)
        log = QuantileSampler([0, 10, 1e6], log_scale=True)
        self.assertTrue(all(0 <= log.sample(rng) <= 1e6 for _ in range(1000)))

    def test_synthetic_words_are_distinct_tokens(self):
        words = synthetic_words(2000, random.Random(3), exclude={"bada"})
        self.assertEqual(len(set(words)), 2000)
        self.assertNotIn("bada", words)
        self.assertTrue(all(tokenize(w) == [w] for w in words))


class TestGenerator(unittest.TestCase):
    def test_deterministic_and_seed_sensitive(self):
        a = generate(num_files=500, seed=7)
        self.assertEqual(a, generate(num_files=500, seed=7))
        self.assertNotEqual(a, generate(num_files=500, seed=8))

    def test_smaller_dataset_is_prefix_of_larger(self):
        small = generate(num_files=300, seed=4)
        large = generate(num_files=3000, seed=4)
        self.assertEqual(large[:300], small)

    def test_records_are_well_formed(self):
        config = GeneratorConfig(num_files=3000, seed=1)
        records = generate(config)
        self.assertEqual([r.file_id for r in records], list(range(3000)))
        self.assertEqual(len({r.path for r in records}), 3000)
        for r in records:
            self.assertGreaterEqual(r.size, 0)
            self.assertTrue(config.time_start <= r.mtime <= config.time_end)
            self.assertGreaterEqual(r.ctime, r.mtime)
            self.assertGreaterEqual(r.atime, r.mtime)
            self.assertIn(r.extension, config.extensions)
            self.assertIn(r.depth, config.depths)
            self.assertTrue(r.tokens)
        self.assertLessEqual({r.uid for r in records}, set(range(501, 505)))

    def test_name_collisions_get_copy_suffix(self):
        records = generate(num_files=2000, vocab_size=3, terms_per_name={1: 1},
                           numeric_term_prob=0, depths={0: 1}, extensions={"txt": 1})
        paths = [r.path for r in records]
        self.assertEqual(len(set(paths)), 2000)
        self.assertTrue(any(p.endswith(" (2).txt") for p in paths))

    def test_term_skew_is_controlled(self):
        fitted = []
        for s in (0.0, 0.6, 1.0, 1.4):
            records = generate(num_files=20000, seed=2, vocab_size=2000, term_skew=s,
                               numeric_term_prob=0.0)
            profile = MetadataProfile.from_records(records)
            fitted.append(profile.term_skew)
            self.assertAlmostEqual(profile.term_skew, s, delta=0.25, msg=f"skew {s}")
        self.assertEqual(fitted, sorted(fitted))

    def test_extension_skew_reshapes_frequencies(self):
        flat = Counter(r.extension for r in generate(num_files=5000, extension_skew=0.0))
        steep = Counter(r.extension for r in generate(num_files=5000, extension_skew=2.0))
        self.assertLess(max(flat.values()) / 5000, 0.08)
        self.assertGreater(max(steep.values()) / 5000, 0.5)

    def test_size_and_recency_controls(self):
        records = generate(num_files=5000, size_median=1000, size_sigma=0.5, zero_size_prob=0)
        self.assertAlmostEqual(statistics.median(r.size for r in records), 1000, delta=80)
        recent = generate(num_files=5000, recency_skew=4.0)
        c = GeneratorConfig()
        midpoint = (c.time_start + c.time_end) / 2
        share_recent = sum(r.mtime > midpoint for r in recent) / 5000
        self.assertGreater(share_recent, 0.8)

    def test_dir_skew_concentrates_files(self):
        def top_dir_share(s):
            records = generate(num_files=5000, depths={2: 1}, dir_skew=s)
            return Counter(r.directory for r in records).most_common(1)[0][1] / 5000

        self.assertLess(top_dir_share(0.0), 0.03)
        self.assertGreater(top_dir_share(1.5), 0.2)

    def test_invalid_configs(self):
        for bad in [dict(term_skew=-1), dict(numeric_term_prob=2), dict(vocab_size=0),
                    dict(time_start=10, time_end=5), dict(depths={})]:
            with self.subTest(**{k: str(v) for k, v in bad.items()}):
                with self.assertRaises(ValueError):
                    SyntheticGenerator(GeneratorConfig(**bad))

    def test_empty_dataset(self):
        self.assertEqual(generate(num_files=0), [])


if __name__ == "__main__":
    unittest.main()
