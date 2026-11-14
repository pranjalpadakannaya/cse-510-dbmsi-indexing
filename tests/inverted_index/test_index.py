import unittest

from dbmsi.inverted_index.compression import delta_encode, delta_decode, vb_encode, vb_decode, compress_postings, decompress_postings
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

    def test_delta_round_trip(self):
        original = [10, 15, 18, 30]
        encoded = delta_encode(original)

        self.assertEqual(encoded, [10, 5, 3, 12])
        self.assertEqual(delta_decode(encoded), original)
        self.assertEqual(delta_encode([]), [])
        self.assertEqual(delta_decode([]), [])

    def test_variable_byte_round_trip(self):
        original = [10, 15, 18, 30]

        gaps = delta_encode(original)
        compressed = vb_encode(gaps)
        decoded_gaps = vb_decode(compressed)
        restored = delta_decode(decoded_gaps)

        self.assertEqual(restored, original)
        
    def test_rid_posting_compression_round_trip(self):
        original = [RID(1, 2), RID(3, 1)]

        compressed = compress_postings(original)
        restored = decompress_postings(compressed)

        self.assertEqual(restored, original)


if __name__ == "__main__":
    unittest.main()