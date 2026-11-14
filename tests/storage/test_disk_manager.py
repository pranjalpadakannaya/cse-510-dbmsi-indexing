import os
import tempfile
import unittest

from dbmsi.storage.constants import PAGE_SIZE
from dbmsi.storage.disk_manager import DiskManager


def page_of(byte: int) -> bytes:
    return bytes([byte]) * PAGE_SIZE


class DiskManagerContract:
    """Tests run against both the in-memory and file-backed stores."""

    def make(self) -> DiskManager:
        raise NotImplementedError

    def test_allocate_is_dense_and_reads_zero(self):
        dm = self.make()
        ids = [dm.allocate_page() for _ in range(3)]
        self.assertEqual(ids, [0, 1, 2])
        buf = bytearray(PAGE_SIZE)
        dm.read_page(2, buf)
        self.assertEqual(buf, bytearray(PAGE_SIZE))

    def test_write_then_read_roundtrip(self):
        dm = self.make()
        for i in range(4):
            dm.write_page(dm.allocate_page(), page_of(i + 1))
        buf = bytearray(PAGE_SIZE)
        for i in range(4):
            dm.read_page(i, buf)
            self.assertEqual(bytes(buf), page_of(i + 1))
        self.assertEqual(dm.stats.page_writes, 4)
        self.assertEqual(dm.stats.page_reads, 4)

    def test_sequential_vs_random_classification(self):
        dm = self.make()
        for _ in range(5):
            dm.allocate_page()
        buf = bytearray(PAGE_SIZE)
        for pid in [0, 1, 2, 4, 3, 4]:
            dm.read_page(pid, buf)
        # 0 random (first), 1 seq, 2 seq, 4 random, 3 random, 4 seq
        self.assertEqual(dm.stats.sequential_reads, 3)
        self.assertEqual(dm.stats.random_reads, 3)

    def test_deallocate_and_reuse(self):
        dm = self.make()
        a, b = dm.allocate_page(), dm.allocate_page()
        dm.deallocate_page(a)
        with self.assertRaises(ValueError):
            dm.read_page(a, bytearray(PAGE_SIZE))
        self.assertEqual(dm.allocate_page(), a)
        self.assertEqual(dm.num_pages, 2)
        self.assertNotEqual(a, b)

    def test_bounds_and_size_checks(self):
        dm = self.make()
        dm.allocate_page()
        with self.assertRaises(IndexError):
            dm.read_page(1, bytearray(PAGE_SIZE))
        with self.assertRaises(ValueError):
            dm.read_page(0, bytearray(10))
        with self.assertRaises(ValueError):
            dm.write_page(0, b"short")


class InMemoryDiskManagerTest(DiskManagerContract, unittest.TestCase):
    def make(self):
        return DiskManager()


class FileDiskManagerTest(DiskManagerContract, unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "test.db")
        self.dms = []

    def tearDown(self):
        for dm in self.dms:
            dm.close()
        self.tmp.cleanup()

    def make(self):
        dm = DiskManager(self.path)
        self.dms.append(dm)
        return dm

    def test_reopen_preserves_pages(self):
        with DiskManager(self.path) as dm:
            dm.allocate_page()
            dm.write_page(dm.allocate_page(), page_of(7))
        with DiskManager(self.path) as dm:
            self.assertEqual(dm.num_pages, 2)
            buf = bytearray(PAGE_SIZE)
            dm.read_page(1, buf)
            self.assertEqual(bytes(buf), page_of(7))


if __name__ == "__main__":
    unittest.main()
