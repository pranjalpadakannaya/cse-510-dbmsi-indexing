import unittest

from dbmsi.storage.stats import IOStats, measure


class IOStatsTest(unittest.TestCase):
    def test_incr_reset_snapshot(self):
        s = IOStats()
        s.incr("page_reads", 3)
        snap = s.snapshot()
        s.incr("page_reads")
        self.assertEqual(snap.page_reads, 3)
        self.assertEqual(s.page_reads, 4)
        s.reset()
        self.assertEqual(s.as_dict(), IOStats().as_dict())

    def test_measure_delta(self):
        s = IOStats()
        s.incr("page_writes", 5)
        with measure(s) as m:
            s.incr("page_writes", 2)
            s.incr("buffer_hits")
        self.assertEqual(m.delta.page_writes, 2)
        self.assertEqual(m.delta.buffer_hits, 1)

    def test_hit_rate_and_io_cost(self):
        s = IOStats(page_requests=4, buffer_hits=3, sequential_reads=10, random_reads=2)
        self.assertAlmostEqual(s.hit_rate, 0.75)
        self.assertEqual(s.io_cost(k=4), 18)
        self.assertEqual(IOStats().hit_rate, 0.0)


if __name__ == "__main__":
    unittest.main()
