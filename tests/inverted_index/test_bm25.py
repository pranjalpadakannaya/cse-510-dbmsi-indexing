import unittest

from dbmsi.inverted_index.bm25 import BM25Index
from dbmsi.storage import RID


class BM25IndexTest(unittest.TestCase):

    def test_top_k_ranking(self):
        index = BM25Index()

        database_report = RID(1, 1)
        database_notes = RID(1, 2)
        unrelated = RID(1, 3)

        index.add(database_report, "database_report.pdf")
        index.add(database_notes, "database_notes.txt")
        index.add(unrelated, "vacation_photo.jpg")

        results = index.search("database report", k=2)

        self.assertEqual(results[0][0], database_report)
        self.assertEqual(results[1][0], database_notes)

    def test_missing_query(self):
        index = BM25Index()
        index.add(RID(1, 1), "database_report.pdf")

        self.assertEqual(index.search("vacation", k=10), [])

    def test_k_limit(self):
        index = BM25Index()

        index.add(RID(1, 1), "database_report.pdf")
        index.add(RID(1, 2), "database_notes.pdf")
        index.add(RID(1, 3), "database_project.pdf")

        results = index.search("database", k=2)

        self.assertEqual(len(results), 2)


if __name__ == "__main__":
    unittest.main()