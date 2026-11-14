"""The page-backed bitmap index, checked against a sequential scan."""

import os
import random
import tempfile
import threading
import unittest

from dbmsi.bitmap import RIDS_PER_PAGE, Bitmap, BitmapIndex, RoaringBitmap, WAHBitmap
from dbmsi.bitmap.blob import blob_pages
from dbmsi.storage import RID, BufferPoolManager, DiskManager, HeapFile, measure
from dbmsi.workload import FileMetadata, GeneratorConfig, QueryGenerator, SyntheticGenerator

ENCODINGS = ("plain", "wah", "roaring")
ATTRIBUTES = ("extension", "semester")


def make_rows(n, seed=1, clustered=False):
    """``(rid, {attribute: value})`` rows with skewed categorical values."""
    rng = random.Random(seed)
    extensions = ["pdf", "txt", "py", "docx", "csv", "png", "zip", ""]
    weights = [40, 25, 15, 8, 5, 4, 2, 1]
    semesters = [f"{year}{term}" for year in range(2020, 2026) for term in ("spring", "fall")]
    rows = []
    for i in range(n):
        semester = semesters[i * len(semesters) // n] if clustered else rng.choice(semesters)
        rows.append(
            (
                RID(i // 50, i % 50),
                {"extension": rng.choices(extensions, weights)[0], "semester": semester},
            )
        )
    return rows


def scan(rows, predicate):
    """The sequential-scan answer: RIDs of the matching rows, in row order."""
    return [rid for rid, values in rows if predicate(values)]


def build(rows, encoding, pool_size=64):
    bpm = BufferPoolManager(DiskManager(), pool_size=pool_size)
    index = BitmapIndex(bpm, ATTRIBUTES, encoding=encoding)
    index.bulk_load(rows)
    return bpm, index


class TestQueries(unittest.TestCase):
    def setUp(self):
        self.rows = make_rows(3000)

    def test_empty_index(self):
        for encoding in ENCODINGS:
            bpm = BufferPoolManager(DiskManager(), pool_size=8)
            index = BitmapIndex(bpm, ATTRIBUTES, encoding=encoding)
            self.assertEqual(len(index), 0)
            self.assertEqual(index.search("extension", "pdf"), [])
            self.assertEqual(index.values("extension"), [])
            index.bulk_load([])
            self.assertEqual(index.search("extension", "pdf"), [])

    def test_equality_matches_scan_for_every_value(self):
        for encoding in ENCODINGS:
            _, index = build(self.rows, encoding)
            self.assertEqual(len(index), len(self.rows))
            for attribute in ATTRIBUTES:
                expected_values = sorted({v[attribute] for _, v in self.rows})
                self.assertEqual(index.values(attribute), expected_values)
                self.assertEqual(index.cardinality(attribute), len(expected_values))
                for value in expected_values:
                    with self.subTest(encoding=encoding, attribute=attribute, value=value):
                        expected = scan(self.rows, lambda v: v[attribute] == value)
                        self.assertEqual(index.search(attribute, value), expected)
                        self.assertEqual(index.count(attribute, value), len(expected))

    def test_missing_value_gives_no_rows_and_no_io(self):
        for encoding in ENCODINGS:
            bpm, index = build(self.rows, encoding)
            bpm.clear()
            with measure(bpm.stats) as m:
                self.assertEqual(index.search("extension", "exe"), [])
                self.assertEqual(index.count("extension", "exe"), 0)
            self.assertEqual(m.delta.page_reads, 0)

    def test_and_or_across_attributes_match_scan(self):
        rng = random.Random(4)
        for encoding in ENCODINGS:
            _, index = build(self.rows, encoding)
            for _ in range(25):
                ext = rng.choice(index.values("extension"))
                sem = rng.choice(index.values("semester"))
                with self.subTest(encoding=encoding, ext=ext, sem=sem):
                    self.assertEqual(
                        index.search_and(("extension", ext), ("semester", sem)),
                        scan(self.rows, lambda v: v["extension"] == ext and v["semester"] == sem),
                    )
                    self.assertEqual(
                        index.search_or(("extension", ext), ("semester", sem)),
                        scan(self.rows, lambda v: v["extension"] == ext or v["semester"] == sem),
                    )

    def test_and_or_on_the_same_attribute(self):
        for encoding in ENCODINGS:
            _, index = build(self.rows, encoding)
            self.assertEqual(index.search_and(("extension", "pdf"), ("extension", "txt")), [])
            self.assertEqual(
                index.search_or(("extension", "pdf"), ("extension", "txt")),
                scan(self.rows, lambda v: v["extension"] in ("pdf", "txt")),
            )
            self.assertEqual(
                index.rids(index.any_of("extension", ["pdf", "txt", "nope"])),
                scan(self.rows, lambda v: v["extension"] in ("pdf", "txt")),
            )

    def test_not_and_composed_expressions_match_scan(self):
        for encoding in ENCODINGS:
            _, index = build(self.rows, encoding)
            pdf = index.bitmap("extension", "pdf")
            py = index.bitmap("extension", "py")
            fall = index.bitmap("semester", "2023fall")
            self.assertEqual(
                index.rids(~pdf), scan(self.rows, lambda v: v["extension"] != "pdf")
            )
            self.assertEqual(
                index.rids((pdf | py) & ~fall),
                scan(
                    self.rows,
                    lambda v: v["extension"] in ("pdf", "py") and v["semester"] != "2023fall",
                ),
            )
            self.assertEqual(
                (pdf & fall).count(),
                len(scan(self.rows, lambda v: v["extension"] == "pdf" and v["semester"] == "2023fall")),
            )

    def test_every_row_has_exactly_one_value_per_attribute(self):
        for encoding in ENCODINGS:
            _, index = build(self.rows, encoding)
            for attribute in ATTRIBUTES:
                union = index.any_of(attribute, index.values(attribute))
                self.assertEqual(union.count(), len(self.rows))
                total = sum(index.count(attribute, v) for v in index.values(attribute))
                self.assertEqual(total, len(self.rows))

    def test_encodings_return_identical_results(self):
        _, plain = build(self.rows, "plain")
        _, wah = build(self.rows, "wah")
        _, roaring = build(self.rows, "roaring")
        self.assertIsInstance(plain.bitmap("extension", "pdf"), Bitmap)
        self.assertIsInstance(wah.bitmap("extension", "pdf"), WAHBitmap)
        self.assertIsInstance(roaring.bitmap("extension", "pdf"), RoaringBitmap)
        for attribute in ATTRIBUTES:
            for value in plain.values(attribute):
                expected = list(plain.bitmap(attribute, value).positions())
                self.assertEqual(list(wah.bitmap(attribute, value).positions()), expected)
                self.assertEqual(list(roaring.bitmap(attribute, value).positions()), expected)

    def test_integer_values_and_row_formats(self):
        bpm = BufferPoolManager(DiskManager(), pool_size=16)
        index = BitmapIndex(bpm, ["uid", "extension"])
        index.bulk_load(
            [
                (RID(0, 0), {"uid": 501, "extension": "pdf"}),  # mapping
                (RID(0, 1), (502, "pdf")),  # sequence
                (RID(0, 2), [501, "txt"]),
            ]
        )
        self.assertEqual(index.search("uid", 501), [RID(0, 0), RID(0, 2)])
        self.assertEqual(index.search("uid", "501"), [])  # "501" is a different value
        self.assertEqual(index.values("uid"), [501, 502])
        with self.assertRaises(TypeError):
            index.insert(RID(0, 3), (1.5, "pdf"))
        with self.assertRaises(TypeError):
            index.insert(RID(0, 3), (True, "pdf"))
        with self.assertRaises(ValueError):
            index.insert(RID(0, 3), (501,))
        with self.assertRaises(KeyError):
            index.search("category", "document")
        self.assertEqual(len(index), 3)

    def test_invalid_construction(self):
        bpm = BufferPoolManager(DiskManager(), pool_size=8)
        with self.assertRaises(ValueError):
            BitmapIndex(bpm, [])
        with self.assertRaises(ValueError):
            BitmapIndex(bpm, ["a", "a"])
        with self.assertRaises(ValueError):
            BitmapIndex(bpm, ["a"], encoding="bogus")
        index = BitmapIndex(bpm, ["a"])
        index.bulk_load([(RID(0, 0), ("x",))])
        with self.assertRaises(RuntimeError):
            index.bulk_load([(RID(0, 1), ("y",))])
        with self.assertRaises(ValueError):
            index.search_and()


class TestInsert(unittest.TestCase):
    def test_inserts_match_bulk_load_and_scan(self):
        rows = make_rows(RIDS_PER_PAGE * 2 + 37, seed=9)
        for encoding in ENCODINGS:
            bpm = BufferPoolManager(DiskManager(), pool_size=32)
            index = BitmapIndex(bpm, ATTRIBUTES, encoding=encoding)
            for number, (rid, values) in enumerate(rows):
                self.assertEqual(index.insert(rid, values), number)
            _, bulk = build(rows, encoding)
            for attribute in ATTRIBUTES:
                self.assertEqual(index.values(attribute), bulk.values(attribute))
                for value in bulk.values(attribute):
                    with self.subTest(encoding=encoding, attribute=attribute, value=value):
                        self.assertEqual(
                            index.search(attribute, value),
                            scan(rows, lambda v: v[attribute] == value),
                        )
                        self.assertEqual(
                            index.bitmap(attribute, value), bulk.bitmap(attribute, value)
                        )

    def test_insert_after_bulk_load_including_new_values(self):
        rows = make_rows(1500, seed=2)
        extra = [
            (RID(900, 0), {"extension": "pdf", "semester": "2023fall"}),
            (RID(900, 1), {"extension": "ipynb", "semester": "2030spring"}),  # both new
            (RID(900, 2), {"extension": "ipynb", "semester": "2023fall"}),
        ]
        for encoding in ENCODINGS:
            _, index = build(rows, encoding)
            for rid, values in extra:
                index.insert(rid, values)
            everything = rows + extra
            self.assertEqual(len(index), len(everything))
            for attribute, value in (
                ("extension", "pdf"), ("extension", "ipynb"), ("extension", "txt"),
                ("semester", "2030spring"), ("semester", "2023fall"),
            ):
                with self.subTest(encoding=encoding, attribute=attribute, value=value):
                    self.assertEqual(
                        index.search(attribute, value),
                        scan(everything, lambda v: v[attribute] == value),
                    )
            # A value that only older rows have is still combined correctly.
            self.assertEqual(
                index.rids(~index.bitmap("extension", "zip")),
                scan(everything, lambda v: v["extension"] != "zip"),
            )

    def test_concurrent_inserts_lose_nothing(self):
        bpm = BufferPoolManager(DiskManager(), pool_size=64)
        index = BitmapIndex(bpm, ["extension"])
        threads, per_thread = 6, 150

        def work(t):
            for i in range(per_thread):
                index.insert(RID(t, i), ("pdf" if i % 3 else "txt",))

        workers = [threading.Thread(target=work, args=(t,)) for t in range(threads)]
        for w in workers:
            w.start()
        for w in workers:
            w.join()
        self.assertEqual(len(index), threads * per_thread)
        pdf, txt = index.search("extension", "pdf"), index.search("extension", "txt")
        self.assertEqual(len(pdf), threads * 100)
        self.assertEqual(len(txt), threads * 50)
        self.assertEqual(
            sorted(pdf + txt), [RID(t, i) for t in range(threads) for i in range(per_thread)]
        )


class TestPersistence(unittest.TestCase):
    def test_reopen_from_a_file(self):
        rows = make_rows(2000, seed=6)
        for encoding in ENCODINGS:
            with tempfile.TemporaryDirectory() as tmp:
                path = os.path.join(tmp, "index.db")
                disk = DiskManager(path)
                bpm = BufferPoolManager(disk, pool_size=32)
                index = BitmapIndex(bpm, ATTRIBUTES, encoding=encoding)
                index.bulk_load(rows)
                index.insert(RID(999, 0), {"extension": "pdf", "semester": "2031fall"})
                index.flush()
                meta = index.meta_page_id
                bpm.flush_all()
                disk.close()

                disk = DiskManager(path)
                bpm = BufferPoolManager(disk, pool_size=32)
                reopened = BitmapIndex(bpm, meta_page_id=meta)
                self.assertEqual(reopened.encoding, encoding)
                self.assertEqual(reopened.attributes, ATTRIBUTES)
                self.assertEqual(len(reopened), len(rows) + 1)
                self.assertEqual(
                    reopened.search("extension", "pdf"),
                    scan(rows, lambda v: v["extension"] == "pdf") + [RID(999, 0)],
                )
                self.assertEqual(reopened.search("semester", "2031fall"), [RID(999, 0)])
                disk.close()

    def test_wrong_meta_page_is_rejected(self):
        bpm = BufferPoolManager(DiskManager(), pool_size=8)
        with bpm.create() as page:
            other = page.page_id
        with self.assertRaises(ValueError):
            BitmapIndex(bpm, meta_page_id=other)


class TestPageIO(unittest.TestCase):
    def test_equality_reads_the_bitmap_and_the_matching_rid_pages(self):
        rows = make_rows(20_000, seed=3)
        for encoding in ENCODINGS:
            bpm, index = build(rows, encoding, pool_size=256)
            matching = [i for i, (_, v) in enumerate(rows) if v["extension"] == "zip"]
            bitmap_pages = blob_pages(index.bitmap("extension", "zip").size_bytes)
            rid_pages = len({i // RIDS_PER_PAGE for i in matching})
            bpm.clear()
            with measure(bpm.stats) as m:
                result = index.search("extension", "zip")
            self.assertEqual(len(result), len(matching))
            with self.subTest(encoding=encoding):
                self.assertEqual(m.delta.page_reads, bitmap_pages + rid_pages)

    def test_counting_does_not_read_the_rid_directory(self):
        rows = make_rows(20_000, seed=3)
        bpm, index = build(rows, "wah", pool_size=256)
        pages = sum(
            blob_pages(index.bitmap(a, v).size_bytes)
            for a, v in (("extension", "pdf"), ("semester", "2022fall"))
        )
        bpm.clear()
        with measure(bpm.stats) as m:
            both = index.bitmap("extension", "pdf") & index.bitmap("semester", "2022fall")
            count = both.count()
        self.assertEqual(
            count,
            len(scan(rows, lambda v: v["extension"] == "pdf" and v["semester"] == "2022fall")),
        )
        self.assertEqual(m.delta.page_reads, pages)

    def test_bulk_loaded_bitmap_is_read_sequentially(self):
        rows = make_rows(100_000, seed=8)
        bpm, index = build(rows, "plain", pool_size=256)
        bpm.clear()
        with measure(bpm.stats) as m:
            index.bitmap("extension", "pdf")
        self.assertEqual(m.delta.page_reads, blob_pages(4 + 100_000 // 8))
        self.assertEqual(m.delta.random_reads, 1)  # the initial seek only

    def test_wah_is_smaller_on_clustered_data_and_stats_add_up(self):
        rows = make_rows(50_000, seed=5, clustered=True)
        _, plain = build(rows, "plain", pool_size=256)
        _, wah = build(rows, "wah", pool_size=256)
        ps, ws = plain.stats(), wah.stats()
        self.assertEqual(ps.cardinality, {"extension": 8, "semester": 12})
        self.assertEqual(ws.cardinality, ps.cardinality)
        # Uncompressed: one bit per row for every value.
        self.assertEqual(ps.bitmap_bytes["semester"], 12 * (4 + 50_000 // 8))
        # Each semester is one run of ones: a handful of words per value.
        self.assertLess(ws.bitmap_bytes["semester"], 12 * 40)
        self.assertEqual(ws.bitmap_pages["semester"], 12)
        self.assertEqual(ps.rid_pages, -(-50_000 // RIDS_PER_PAGE))
        self.assertEqual(
            ps.total_pages, ps.total_bitmap_pages + ps.rid_pages + ps.catalog_pages + 1
        )
        self.assertLess(ws.total_bitmap_bytes, ps.total_bitmap_bytes)

    def test_pages_allocated_match_stats(self):
        rows = make_rows(30_000, seed=12)
        for encoding in ENCODINGS:
            bpm, index = build(rows, encoding, pool_size=256)
            self.assertEqual(bpm.stats.pages_allocated - bpm.stats.pages_deallocated,
                             index.stats().total_pages)


class TestOnSyntheticMetadata(unittest.TestCase):
    """End to end: generated records in a heap file, queries from the generator."""

    @classmethod
    def setUpClass(cls):
        cls.records = SyntheticGenerator(GeneratorConfig(num_files=4000, seed=42)).generate()
        cls.attributes = ("extension", "category", "uid", "mode", "depth")

    def load(self, encoding):
        bpm = BufferPoolManager(DiskManager(), pool_size=256)
        table = HeapFile(bpm)
        rids = [table.insert(r.pack()) for r in self.records]
        index = BitmapIndex(bpm, self.attributes, encoding=encoding)
        # The index reads the attributes straight from the records.
        index.bulk_load(zip(rids, self.records))
        return table, index

    def test_point_queries_match_the_oracle_and_the_heap_scan(self):
        queries = QueryGenerator(self.records, seed=1)
        for encoding in ENCODINGS:
            table, index = self.load(encoding)
            for attribute in self.attributes:
                for selectivity in (None, 0.001, 0.01, 0.1, 0.5):
                    q = queries.point(attribute, selectivity)
                    with self.subTest(encoding=encoding, attribute=attribute, value=q.value):
                        found = index.search(q.attribute, q.value)
                        self.assertEqual(len(found), q.expected_count)
                        scanned = [
                            rid for rid, raw in table.scan()
                            if q.matches(FileMetadata.unpack(raw))
                        ]
                        self.assertEqual(found, scanned)
                        for rid in found[:20]:
                            self.assertTrue(q.matches(FileMetadata.unpack(table.get(rid))))

    def test_conjunctions_match_the_heap_scan(self):
        rng = random.Random(2)
        for encoding in ENCODINGS:
            table, index = self.load(encoding)
            for _ in range(15):
                a = rng.choice(self.records)
                b = rng.choice(self.records)
                conditions = (("extension", a.extension), ("uid", b.uid), ("depth", a.depth))
                scanned = []
                for rid, raw in table.scan():
                    r = FileMetadata.unpack(raw)
                    if r.extension == a.extension and r.uid == b.uid and r.depth == a.depth:
                        scanned.append(rid)
                with self.subTest(encoding=encoding, conditions=conditions):
                    self.assertEqual(index.search_and(*conditions), scanned)


if __name__ == "__main__":
    unittest.main()
