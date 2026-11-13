import unittest

from dbmsi.storage.lru_replacer import LRUReplacer


class LRUReplacerTest(unittest.TestCase):
    def test_victim_order(self):
        r = LRUReplacer()
        for f in [1, 2, 3]:
            r.unpin(f)
        r.pin(2)
        r.unpin(1)  # 1 becomes most recent
        self.assertEqual(len(r), 2)
        self.assertEqual(r.victim(), 3)
        self.assertEqual(r.victim(), 1)
        self.assertIsNone(r.victim())


if __name__ == "__main__":
    unittest.main()
