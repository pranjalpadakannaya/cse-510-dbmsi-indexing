import unittest

from dbmsi.inverted_index.index import InvertedIndex, tokenize
from dbmsi.storage import RID


class InvertedIndexTest(unittest.TestCase):

    def test_tokenize(self):
        self.assertEqual(
            tokenize("Database_Project.pdf"),
            ["database", "project", "pdf"],
        )

    def test_add_and_search(self):
        index = InvertedIndex()

        first = RID(1, 2)
        second = RID(3, 1)

        index.add(first, "database_project.pdf")
        index.add(second, "database_notes.txt")

        self.assertEqual(index.search("database"), [first, second])
        self.assertEqual(index.search("project"), [first])
        self.assertEqual(index.search("missing"), [])
        self.assertEqual(index.search_and("database", "project"), [first])
        self.assertEqual(index.search_or("database", "project"), [first, second])

if __name__ == "__main__":
    unittest.main()