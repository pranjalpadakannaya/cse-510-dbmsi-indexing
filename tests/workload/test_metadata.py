import unittest

from dbmsi.storage import BufferPoolManager, DiskManager, HeapFile
from dbmsi.workload.metadata import (
    FileMetadata,
    category_of,
    split_extension,
    tokenize,
)


def sample(**overrides):
    fields = dict(
        file_id=7, path="projects/cse510/Final_Report-v2.PDF", size=123456,
        mtime=1.7e9 + 0.25, ctime=1.6e9, atime=1.71e9, uid=501, mode=0o644,
    )
    fields.update(overrides)
    return FileMetadata(**fields)


class TestDerivedAttributes(unittest.TestCase):
    def test_path_parts(self):
        m = sample()
        self.assertEqual(m.name, "Final_Report-v2.PDF")
        self.assertEqual(m.directory, "projects/cse510")
        self.assertEqual(m.extension, "pdf")
        self.assertEqual(m.category, "document")
        self.assertEqual(m.depth, 2)
        self.assertEqual(m.tokens, ["final", "report", "v2", "pdf"])
        self.assertEqual(sample(path="top.txt").depth, 0)

    def test_split_extension(self):
        self.assertEqual(split_extension("a.tar.gz"), ("a.tar", "gz"))
        self.assertEqual(split_extension(".bashrc"), (".bashrc", ""))
        self.assertEqual(split_extension("Makefile"), ("Makefile", ""))
        self.assertEqual(split_extension("trailing."), ("trailing.", ""))

    def test_category_and_tokens(self):
        self.assertEqual(category_of("py"), "code")
        self.assertEqual(category_of("weird"), "other")
        self.assertEqual(tokenize("IMG_2041 (copy).JPG"), ["img", "2041", "copy", "jpg"])


class TestSerialization(unittest.TestCase):
    def test_pack_round_trip(self):
        for m in [sample(), sample(path="日本/ファイル.txt", size=0, uid=0, mode=0)]:
            self.assertEqual(FileMetadata.unpack(m.pack()), m)

    def test_dict_round_trip(self):
        m = sample()
        self.assertEqual(FileMetadata.from_dict(m.to_dict()), m)

    def test_rejects_huge_path(self):
        with self.assertRaises(ValueError):
            sample(path="x" * 70000).pack()

    def test_stores_in_heap_file(self):
        heap = HeapFile(BufferPoolManager(DiskManager(), pool_size=8))
        records = [sample(file_id=i, path=f"d{i % 5}/file_{i}.txt") for i in range(300)]
        rids = [heap.insert(r.pack()) for r in records]
        self.assertEqual([FileMetadata.unpack(heap.get(rid)) for rid in rids], records)


if __name__ == "__main__":
    unittest.main()
