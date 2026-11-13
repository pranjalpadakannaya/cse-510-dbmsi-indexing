import unittest

from dbmsi.inverted_index.compression import delta_encode, delta_decode
from dbmsi.inverted_index.index import InvertedIndex, tokenize
from dbmsi.storage import RID

class InvertedIndexTest(unittest.TestCase):

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

    def test_delta_round_trip(self):
        original = [10, 15, 18, 30]
        encoded = delta_encode(original)

        self.assertEqual(encoded, [10, 5, 3, 12])
        self.assertEqual(delta_decode(encoded), original)
        self.assertEqual(delta_encode([]), [])
        self.assertEqual(delta_decode([]), [])