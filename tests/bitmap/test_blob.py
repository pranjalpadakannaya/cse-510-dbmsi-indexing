import unittest

from dbmsi.bitmap.blob import PAYLOAD_SIZE, blob_pages, free_blob, read_blob, write_blob
from dbmsi.storage import BufferPoolManager, DiskManager, measure


def pool(size=8):
    return BufferPoolManager(DiskManager(), pool_size=size)


def payload(n):
    return bytes(i * 31 % 251 for i in range(n))


class TestBlob(unittest.TestCase):
    def test_round_trip_at_page_boundaries(self):
        bpm = pool()
        for n in (0, 1, PAYLOAD_SIZE - 1, PAYLOAD_SIZE, PAYLOAD_SIZE + 1, 5 * PAYLOAD_SIZE + 17):
            with self.subTest(n=n):
                first = write_blob(bpm, payload(n))
                self.assertEqual(read_blob(bpm, first), payload(n))
        bpm.clear()  # evicts every unpinned page, so nothing may be left pinned
        self.assertEqual(bpm.num_resident, 0)

    def test_page_count(self):
        self.assertEqual(blob_pages(0), 1)
        self.assertEqual(blob_pages(PAYLOAD_SIZE), 1)
        self.assertEqual(blob_pages(PAYLOAD_SIZE + 1), 2)
        bpm = pool()
        write_blob(bpm, payload(3 * PAYLOAD_SIZE + 1))
        self.assertEqual(bpm.stats.pages_allocated, 4)

    def test_new_blob_is_read_sequentially(self):
        bpm = pool()
        first = write_blob(bpm, payload(6 * PAYLOAD_SIZE))
        bpm.clear()
        with measure(bpm.stats) as m:
            read_blob(bpm, first)
        self.assertEqual(m.delta.page_reads, 6)
        self.assertEqual(m.delta.sequential_reads, 5)  # all but the initial seek

    def test_overwrite_with_longer_data_keeps_first_page(self):
        bpm = pool()
        first = write_blob(bpm, payload(PAYLOAD_SIZE + 10))
        again = write_blob(bpm, payload(4 * PAYLOAD_SIZE), first)
        self.assertEqual(again, first)
        self.assertEqual(read_blob(bpm, first), payload(4 * PAYLOAD_SIZE))
        self.assertEqual(bpm.stats.pages_allocated, 4)

    def test_overwrite_with_shorter_data_frees_the_extra_pages(self):
        bpm = pool()
        first = write_blob(bpm, payload(5 * PAYLOAD_SIZE))
        write_blob(bpm, payload(PAYLOAD_SIZE + 1), first)
        self.assertEqual(read_blob(bpm, first), payload(PAYLOAD_SIZE + 1))
        self.assertEqual(bpm.stats.pages_deallocated, 3)

    def test_free_blob_releases_every_page(self):
        bpm = pool()
        first = write_blob(bpm, payload(3 * PAYLOAD_SIZE))
        free_blob(bpm, first)
        self.assertEqual(bpm.stats.pages_deallocated, 3)

    def test_works_with_a_tiny_buffer_pool(self):
        bpm = pool(size=2)
        first = write_blob(bpm, payload(10 * PAYLOAD_SIZE))
        self.assertEqual(read_blob(bpm, first), payload(10 * PAYLOAD_SIZE))
        write_blob(bpm, payload(20 * PAYLOAD_SIZE), first)
        self.assertEqual(read_blob(bpm, first), payload(20 * PAYLOAD_SIZE))


if __name__ == "__main__":
    unittest.main()
