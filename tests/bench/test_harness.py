"""The benchmark harness, its report and its command line."""

import contextlib
import csv
import io
import json
import os
import tempfile
import unittest
from unittest import mock

from dbmsi.bench import BenchmarkConfig, BenchmarkResult, run_benchmark, spec_of, summarize
from dbmsi.bench import estimate_check, harness, run_sweep, set_param
from dbmsi.bench.harness import io_cost_field, percentile
from dbmsi.bench.cli import main
from dbmsi.bench.methods import SequentialScan
from dbmsi.bench.report import load_result, markdown, write_results
from dbmsi.workload import PointQuery, RangeQuery, TermQuery, generate, write_queries, write_records
from dbmsi.workload import QueryGenerator

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def small_config(**overrides):
    base = dict(
        name="test", num_files=600, specs=["point:extension", "range:size", "term", "and"],
        selectivities=[0.01, 0.1], per_level=2, repeat=1, pool_size=16, num_updates=20,
        methods=["seqscan", "btree", "bitmap-wah", "inverted"],
    )
    base.update(overrides)
    return BenchmarkConfig(**base)


class BrokenScan(SequentialScan):
    """Drops one row from every answer, to prove mismatches are caught."""

    name = "broken"

    def query(self, query):
        rids = super().query(query)
        if rids:
            rids.pop()
        return rids


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(list(argv))
    return code, out.getvalue(), err.getvalue()


class TestConfig(unittest.TestCase):
    def test_unknown_keys_and_methods_are_rejected(self):
        with self.assertRaises(ValueError):
            BenchmarkConfig.from_dict({"num_file": 10})
        with self.assertRaises(ValueError):
            BenchmarkConfig(methods=["seqscan", "hash"]).validate()
        with self.assertRaises(ValueError):
            BenchmarkConfig(methods=["btree"]).validate()
        with self.assertRaises(ValueError):  # replaced by random_io_weights
            BenchmarkConfig.from_dict({"random_io_weight": 4})
        with self.assertRaises(ValueError):
            BenchmarkConfig(random_io_weights=[]).validate()

    def test_percentile(self):
        self.assertEqual(percentile([3.0], 95), 3.0)
        self.assertEqual(percentile([4, 1, 3, 2, 5], 50), 3)
        self.assertEqual(percentile(range(101), 95), 95)
        self.assertAlmostEqual(percentile([0, 10], 95), 9.5)

    def test_set_param(self):
        data = {"name": "x", "generator": {"vocab_size": 5}}
        out = set_param(data, "generator.num_users", 10)
        self.assertEqual(out["generator"], {"vocab_size": 5, "num_users": 10})
        self.assertNotIn("num_users", data["generator"])  # the input is left alone
        self.assertEqual(set_param(data, "num_files", 7)["num_files"], 7)

    def test_checked_in_configs_load(self):
        for name in ("smoke.json", "default.json", "scale.json", "cardinality.json"):
            BenchmarkConfig.load(os.path.join(ROOT, "benchmarks", name)).validate()

    def test_spec_of(self):
        self.assertEqual(spec_of(PointQuery(attribute="uid", value=1)), "point:uid")
        self.assertEqual(spec_of(RangeQuery(attribute="size", low=0, high=1)), "range:size")
        self.assertEqual(spec_of(TermQuery(terms=("a",))), "term")
        self.assertEqual(spec_of(TermQuery(terms=("a", "b"), op="or")), "or")
        self.assertEqual(spec_of(TermQuery(terms=("a", "b", "c"))), "and:3")


class TestHarness(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result = run_benchmark(small_config())

    def test_all_results_correct(self):
        self.assertTrue(self.result.all_correct)
        self.assertEqual(self.result.verification["mismatches"], [])
        self.assertGreater(self.result.verification["checked"], 0)

    def test_every_query_runs_on_scan_and_supporting_methods(self):
        by_query = {}
        for row in self.result.queries:
            by_query.setdefault(row["query_id"], []).append(row["method"])
        self.assertEqual(len(by_query), self.result.dataset["num_queries"])
        for methods in by_query.values():
            self.assertEqual(methods[0], "seqscan")
        specs = {}
        for row in self.result.queries:
            specs.setdefault(row["spec"], set()).add(row["method"])
        self.assertEqual(specs, {
            "point:extension": {"seqscan", "btree", "bitmap-wah"},
            "range:size": {"seqscan", "btree"},
            "term": {"seqscan", "inverted"},
            "and": {"seqscan", "inverted"},
        })

    def test_metrics_are_recorded(self):
        heap_pages = self.result.dataset["heap_pages"]
        for row in self.result.queries:
            self.assertEqual(row["sequential_reads"] + row["random_reads"], row["page_reads"])
            self.assertGreaterEqual(row["latency_ms"], 0)
            self.assertGreaterEqual(row["latency_p95_ms"], row["latency_ms"])
            self.assertGreaterEqual(row["runs"], 1)
            self.assertEqual(row["index_page_reads"] + row["fetch_page_reads"], row["page_reads"])
            self.assertEqual(row["fetch_page_reads"], 0)  # fetch_records is off
            self.assertEqual(row["warm_page_reads"], 0)  # the small indexes fit in the pool
            for k in (1, 4, 10):
                self.assertEqual(row[io_cost_field(k)],
                                 row["sequential_reads"] + k * row["random_reads"])
            if row["method"] == "seqscan":
                self.assertEqual(row["page_reads"], heap_pages)  # cold cache: every page
            else:
                self.assertLess(row["page_reads"], heap_pages)
        build = {b["method"]: b for b in self.result.build}
        self.assertEqual(build["seqscan"]["size_pages"], heap_pages)
        for b in self.result.build:
            self.assertGreater(b["size_pages"], 0)
            self.assertGreater(b["build_page_writes"], 0)
        self.assertEqual({u["method"] for u in self.result.updates}, set(build))
        for u in self.result.updates:
            self.assertEqual(u["inserts"], 20)

    def test_runs_are_reproducible(self):
        again = run_benchmark(small_config())
        strip = lambda rows: [{k: v for k, v in r.items() if "seconds" not in k and "ms" not in k}
                              for r in rows]
        self.assertEqual(strip(again.queries), strip(self.result.queries))
        self.assertEqual(strip(again.build), strip(self.result.build))
        self.assertEqual(strip(again.updates), strip(self.result.updates))

    def test_different_seed_gives_different_workload(self):
        other = run_benchmark(small_config(query_seed=9, num_updates=0))
        self.assertNotEqual([r["query"] for r in other.queries],
                            [r["query"] for r in self.result.queries])
        self.assertEqual(other.updates, [])

    def test_mismatches_are_reported(self):
        real = harness.make_methods

        def with_broken(config, bpm, heap, queries):
            return real(config, bpm, heap, queries) + [BrokenScan(bpm, heap)]

        with mock.patch.object(harness, "make_methods", with_broken):
            result = run_benchmark(small_config(num_updates=5))
        broken = [r for r in result.queries if r["method"] == "broken"]
        self.assertTrue(broken)
        self.assertFalse(any(r["correct"] for r in broken if r["expected_count"] > 0))
        self.assertFalse(result.all_correct)
        self.assertTrue(result.verification["mismatches"])
        self.assertIn("broken", markdown(result))

    def test_fetch_records_adds_heap_reads(self):
        fetched = run_benchmark(small_config(fetch_records=True, num_updates=0))
        key = lambda r: (r["query_id"], r["method"])
        plain = {key(r): r for r in self.result.queries}
        for row in fetched.queries:
            if row["method"] != "seqscan" and row["result_count"]:
                self.assertGreater(row["page_reads"], plain[key(row)]["page_reads"])
                self.assertEqual(row["index_page_reads"], plain[key(row)]["page_reads"])
                self.assertGreater(row["fetch_page_reads"], 0)
        # Fetching in RID order reads each heap page at most once.
        heap_pages = fetched.dataset["heap_pages"]
        self.assertTrue(all(r["fetch_page_reads"] <= heap_pages for r in fetched.queries))
        # The estimate adds the expected heap pages holding the matches; per
        # spec the 600-row queries are a few pages each, so check the total.
        checks = estimate_check(fetched)
        measured = sum(e["measured"] for e in checks)
        estimated = sum(e["estimated"] for e in checks)
        self.assertAlmostEqual(measured / estimated, 1, delta=0.05)

    def test_estimates_are_recorded(self):
        checks = estimate_check(self.result)
        self.assertEqual({e["method"] for e in checks},
                         {"seqscan", "btree", "bitmap-wah", "inverted"})
        for e in checks:
            self.assertGreaterEqual(e["within_tolerance"], 0)
            self.assertLessEqual(e["within_tolerance"], 1)
            if e["method"] == "seqscan":
                self.assertEqual(e["error"], 0)

    def test_repeats_stop_at_the_time_budget(self):
        result = run_benchmark(small_config(repeat=50, min_repeat=2, max_repeat_seconds=0,
                                            num_updates=0))
        self.assertTrue(all(r["runs"] == 2 for r in result.queries))
        result = run_benchmark(small_config(repeat=4, min_repeat=1, num_updates=0,
                                            specs=["term"]))
        self.assertTrue(all(r["runs"] == 4 for r in result.queries))

    def test_raw_postings_answer_the_same(self):
        result = run_benchmark(small_config(methods=["seqscan", "inverted", "inverted-raw"],
                                            specs=["term", "or"], num_updates=10))
        self.assertTrue(result.all_correct)
        size = {b["method"]: b["size_bytes"] for b in result.build}
        self.assertLess(size["inverted"], size["inverted-raw"])

    def test_throughput(self):
        result = run_benchmark(small_config(
            num_updates=0, specs=["range:size"], methods=["seqscan", "btree"],
            throughput_threads=[1, 3], throughput_ops=600, throughput_insert_fraction=0.5,
            throughput_read_latency_ms=[0.0, 0.05], throughput_pool_size=16,
        ))
        self.assertEqual(
            [(t["read_latency_ms"], t["latching"], t["threads"]) for t in result.throughput],
            [(lat, scheme, n) for lat in (0.0, 0.05) for scheme in ("global", "crabbing")
             for n in (1, 3)],
        )
        for t in result.throughput:
            self.assertEqual(t["ops"], 600)
            self.assertGreater(t["inserts"], 0)
            self.assertEqual(t["lost_inserts"], 0)
            self.assertTrue(t["entries_ok"])
            self.assertGreater(t["ops_per_second"], 0)
            self.assertEqual(t["pool_size"], 16)
        crabbing = [t for t in result.throughput if t["latching"] == "crabbing"]
        self.assertTrue(all(t["optimistic_inserts"] > 0 for t in crabbing))
        text = markdown(result)
        self.assertIn("Concurrent searches and inserts", text)
        self.assertIn("crabbing / global", text)

    def test_dataset_and_queries_from_files(self):
        records = generate(num_files=400, seed=2)
        with tempfile.TemporaryDirectory() as d:
            rpath, qpath = os.path.join(d, "r.jsonl"), os.path.join(d, "q.jsonl")
            write_records(rpath, records)
            # Queries over the rows the harness builds from (all but the last 10).
            write_queries(qpath, QueryGenerator(records[:-10], seed=1).sweep("term", [0.01], 3))
            result = run_benchmark(small_config(records=rpath, queries=qpath, num_updates=10))
        self.assertEqual(result.dataset["num_rows"], 390)
        self.assertEqual(result.dataset["num_queries"], 3)
        self.assertTrue(result.all_correct)


class TestReport(unittest.TestCase):
    def test_outputs_round_trip(self):
        result = run_benchmark(small_config(num_updates=5))
        with tempfile.TemporaryDirectory() as d:
            write_results(result, d, plots=False)
            for name in ("results.json", "queries.csv", "summary.csv", "build.csv",
                         "updates.csv", "estimates.csv", "report.md"):
                self.assertTrue(os.path.exists(os.path.join(d, name)), name)
            with open(os.path.join(d, "queries.csv")) as f:
                self.assertEqual(len(list(csv.DictReader(f))), len(result.queries))
            self.assertEqual(load_result(d), BenchmarkResult.from_dict(
                json.loads(json.dumps(result.to_dict()))))

    def test_summary_groups(self):
        result = run_benchmark(small_config(num_updates=0))
        summary = summarize(result)
        groups = {(r["spec"], r["target_selectivity"], r["method"]) for r in result.queries}
        self.assertEqual(len(summary), len(groups))
        for row in summary:
            self.assertEqual(row["queries"], 2)
            self.assertEqual(row["correct"], 2)
        text = markdown(result)
        for heading in ("## Correctness", "## Build and storage", "### `range:size`"):
            self.assertIn(heading, text)


class TestSweep(unittest.TestCase):
    def test_sweep_writes_one_run_per_value(self):
        data = small_config(num_updates=0, specs=["point:uid", "range:size"],
                            methods=["seqscan", "btree", "bitmap-roaring"]).to_dict()
        with tempfile.TemporaryDirectory() as d:
            runs = run_sweep(data, "generator.num_users", [2, 20], d, plots=False)
            self.assertEqual([v for v, _ in runs], [2, 20])
            for v, run in runs:
                self.assertTrue(run["all_correct"])
                with open(os.path.join(d, f"generator.num_users={v}", "results.json")) as f:
                    config = json.load(f)["config"]
                self.assertEqual(config["generator"], {"num_users": v})
            card = [b["details"]["cardinality"]["uid"] for _, run in runs
                    for b in run["build"] if b["method"] == "bitmap-roaring"]
            self.assertEqual(card, [2, 20])
            for name in ("sweep.csv", "sweep_build.csv", "sweep_estimates.csv", "sweep.json",
                         "sweep.md"):
                self.assertTrue(os.path.exists(os.path.join(d, name)), name)
            with open(os.path.join(d, "sweep.csv")) as f:
                rows = list(csv.DictReader(f))
            self.assertEqual({r["generator.num_users"] for r in rows}, {"2", "20"})


class TestCli(unittest.TestCase):
    def test_run_and_report(self):
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "out")
            code, stdout, err = run(
                "run", os.path.join(ROOT, "benchmarks", "smoke.json"), "-o", out, "--no-plots",
                "-q", "--set", "num_files=400", "--set", "per_level=1",
                "--set", 'specs=["range:size", "term"]', "--set", "name=cli",
            )
            self.assertEqual(code, 0, err)
            self.assertIn("# Benchmark: cli", stdout)
            with open(os.path.join(out, "results.json")) as f:
                config = json.load(f)["config"]
            self.assertEqual(config["num_files"], 400)
            self.assertEqual(config["specs"], ["range:size", "term"])

            os.remove(os.path.join(out, "report.md"))
            code, stdout, _ = run("report", out, "--no-plots")
            self.assertEqual(code, 0)
            self.assertTrue(os.path.exists(os.path.join(out, "report.md")))

    def test_sweep(self):
        with tempfile.TemporaryDirectory() as d:
            code, stdout, err = run(
                "sweep", os.path.join(ROOT, "benchmarks", "smoke.json"), "-o", d, "--no-plots",
                "-q", "--param", "num_files", "--values", "300", "500", "--jobs", "2",
                "--set", "per_level=1", "--set", 'specs=["range:size"]',
                "--set", "throughput_threads=[]",
            )
            self.assertEqual(code, 0, err)
            self.assertIn("# Sweep over `num_files`", stdout)
            self.assertTrue(os.path.isdir(os.path.join(d, "num_files=300")))
            self.assertTrue(os.path.isdir(os.path.join(d, "num_files=500")))

    def test_errors(self):
        code, _, err = run("run", "--set", "bogus=1", "--no-plots", "-q")
        self.assertEqual(code, 1)
        self.assertIn("bogus", err)
        code, _, err = run("run", "--set", "nofile", "-q")
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
