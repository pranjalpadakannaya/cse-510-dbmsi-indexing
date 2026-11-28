"""Measured I/O cost: the calibration and how a config uses it."""

import json
import os
import tempfile
import unittest

from dbmsi.bench import BenchmarkConfig
from dbmsi.bench.calibrate import calibrate, load_calibration, write_calibration


def _fake(k=3.0, random_ms=0.09):
    return {"k": k, "raw_k": k, "random": {"mean_ms": random_ms},
            "sequential": {"mean_ms": random_ms / k}}


class CalibrateTest(unittest.TestCase):
    def test_measures_both_patterns(self):
        data = calibrate(file_mb=2, reads=200)
        self.assertGreater(data["sequential"]["mean_ms"], 0)
        self.assertGreater(data["random"]["mean_ms"], 0)
        self.assertGreaterEqual(data["k"], 1.0)
        self.assertEqual(data["sequential"]["reads"], 200)

    def test_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "cal.json")
            write_calibration(_fake(), path)
            self.assertEqual(load_calibration(path)["k"], 3.0)

    def test_rejects_other_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "x.json")
            with open(path, "w") as f:
                json.dump({"name": "not a calibration"}, f)
            with self.assertRaises(ValueError):
                load_calibration(path)


class ConfigCalibrationTest(unittest.TestCase):
    def _config(self, **extra):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        path = os.path.join(self.tmp.name, "cal.json")
        write_calibration(_fake(k=3.08, random_ms=0.0898), path)
        return BenchmarkConfig.from_dict({"io_calibration": path, **extra})

    def test_calibration_sets_weights_and_latency(self):
        c = self._config()
        self.assertEqual(c.random_io_weights, [3.08])
        self.assertEqual(c.throughput_read_latency_ms, [0.0, 0.0898])
        self.assertEqual(c.io_calibration_data["k"], 3.08)

    def test_explicit_values_win(self):
        c = self._config(random_io_weights=[1, 4, 10], throughput_read_latency_ms=[0, 1.0])
        self.assertEqual(c.random_io_weights, [1, 4, 10])
        self.assertEqual(c.throughput_read_latency_ms, [0, 1.0])

    def test_without_calibration_keeps_assumed_weights(self):
        c = BenchmarkConfig.from_dict({})
        self.assertEqual(c.random_io_weights, [1.0, 4.0, 10.0])
        self.assertIsNone(c.io_calibration_data)


if __name__ == "__main__":
    unittest.main()
