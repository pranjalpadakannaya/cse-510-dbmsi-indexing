import contextlib
import io
import os
import tempfile
import unittest

from dbmsi.workload.cli import main
from dbmsi.workload.dataset import read_queries, read_records, write_records
from dbmsi.workload.generator import generate
from dbmsi.workload.profile import MetadataProfile
from dbmsi.workload.queries import evaluate


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(list(argv))
    return code, out.getvalue(), err.getvalue()


class TestDatasetFiles(unittest.TestCase):
    def test_records_round_trip(self):
        records = generate(num_files=200, seed=1)
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "r.jsonl")
            self.assertEqual(write_records(path, records), 200)
            self.assertEqual(read_records(path), records)


class TestCli(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def path(self, name):
        return os.path.join(self.dir, name)

    def test_scan_profile_generate_queries_pipeline(self):
        src = self.path("tree")
        for i in range(30):
            os.makedirs(os.path.join(src, f"dir{i % 3}"), exist_ok=True)
            with open(os.path.join(src, f"dir{i % 3}", f"notes_{i}.txt"), "w") as f:
                f.write("x" * i)

        code, out, _ = run("scan", src, "-o", self.path("real.jsonl"), "-p", self.path("prof.json"))
        self.assertEqual(code, 0)
        self.assertIn("scanned 30 files", out)
        self.assertEqual(len(read_records(self.path("real.jsonl"))), 30)

        code, out, _ = run("profile", self.path("real.jsonl"), "-o", self.path("prof2.json"))
        self.assertEqual(code, 0)
        self.assertIn("files:            30", out)
        self.assertEqual(MetadataProfile.load(self.path("prof2.json")).extensions, {"txt": 30})

        code, out, _ = run("generate", "-n", "500", "--profile", self.path("prof.json"),
                           "--term-skew", "0.8", "-o", self.path("synth.jsonl"))
        self.assertEqual(code, 0)
        self.assertIn("term skew 0.80", out)
        synth = read_records(self.path("synth.jsonl"))
        self.assertEqual(len(synth), 500)
        self.assertTrue(all(r.extension == "txt" for r in synth))

        code, out, _ = run("queries", self.path("synth.jsonl"), "-o", self.path("q.jsonl"),
                           "--spec", "range:size", "term", "point:extension",
                           "--selectivity", "0.01", "0.1", "--per-level", "3", "--show", "2")
        self.assertEqual(code, 0)
        queries = read_queries(self.path("q.jsonl"))
        self.assertEqual(len(queries), 3 * 2 * 3)
        for q in queries:
            self.assertEqual(q.expected_count, len(evaluate(q, synth)))

    def test_generate_without_profile_is_reproducible(self):
        for name in ("a.jsonl", "b.jsonl"):
            code, _, _ = run("generate", "-n", "300", "--seed", "4", "--extension-skew", "1.5",
                             "-o", self.path(name))
            self.assertEqual(code, 0)
        with open(self.path("a.jsonl")) as a, open(self.path("b.jsonl")) as b:
            self.assertEqual(a.read(), b.read())

    def test_errors_are_reported(self):
        code, _, err = run("scan", self.path("missing"), "-o", self.path("x.jsonl"))
        self.assertEqual(code, 1)
        self.assertIn("error:", err)
        write_records(self.path("one.jsonl"), generate(num_files=50))
        code, _, err = run("queries", self.path("one.jsonl"), "-o", self.path("q.jsonl"),
                           "--spec", "range:extension", "--selectivity", "0.1")
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
