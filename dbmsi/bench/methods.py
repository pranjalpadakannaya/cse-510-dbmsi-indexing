"""Access methods under test, behind one interface.

Every method answers a :class:`~dbmsi.workload.Query` with the set of
matching RIDs from the same :class:`~dbmsi.storage.HeapFile`, so results can
be compared one-to-one against the sequential-scan baseline. All of them
read pages through the same :class:`~dbmsi.storage.BufferPoolManager`, so
page I/O is counted by the storage layer's :class:`~dbmsi.storage.IOStats`.
Each method also gives an analytical estimate of the pages a query reads
from a cold cache (:meth:`AccessMethod.estimate_reads`), worked out from its
structure (tree height, entries per leaf, bitmap and posting list sizes) and
the number of matches; the harness compares it with the measured count.

* :class:`SequentialScan` reads every heap page and tests each record;
* :class:`BTreeMethod` keeps one :class:`~dbmsi.btree.BPlusTree` per
  attribute and answers point and range queries;
* :class:`BitmapMethod` keeps one :class:`~dbmsi.bitmap.BitmapIndex` over
  the categorical attributes and answers point queries (and ranges on
  low-cardinality attributes by OR-ing the bitmaps of the values in range);
* :class:`InvertedMethod` answers name-term queries from posting lists
  stored in pages, delta + variable-byte compressed or uncompressed.
"""

from __future__ import annotations

import struct
import time
from typing import Iterable, Optional, Sequence

from dbmsi.bitmap import BitmapIndex
from dbmsi.bitmap.blob import blob_pages
from dbmsi.btree import BPlusTree, FloatKey, IntKey, KeyCodec, StringKey
from dbmsi.inverted_index.compression import (
    compress_postings, decompress_postings, int_to_rid, rid_to_int,
)
from dbmsi.inverted_index.bm25 import BM25Index
from dbmsi.inverted_index.index import InvertedIndex
from dbmsi.storage import PAGE_SIZE, RID, BufferPoolManager, HeapFile
from dbmsi.workload import FileMetadata, PointQuery, Query, RangeQuery, TermQuery

INT_ATTRIBUTES = ("size", "uid", "mode", "depth")
FLOAT_ATTRIBUTES = ("mtime", "ctime", "atime")
STRING_ATTRIBUTES = ("extension", "category")


def pages_touched(num_pages: int, num_rows: int, matches: int) -> float:
    """Expected pages holding ``matches`` of ``num_rows`` rows spread evenly
    over ``num_pages`` pages, when the matches fall at random (Yao)."""
    if matches <= 0 or num_pages <= 0 or num_rows <= 0:
        return 0.0
    if matches >= num_rows:
        return float(num_pages)
    return num_pages * (1 - (1 - matches / num_rows) ** (num_rows / num_pages))


def pages_spanned(num_bytes: int) -> float:
    """Expected pages overlapped by ``num_bytes`` contiguous bytes starting
    at a uniformly random offset in a page."""
    return 0.0 if num_bytes <= 0 else 1 + (num_bytes - 1) / PAGE_SIZE


class AccessMethod:
    """One way of answering queries over the shared heap file.

    Subclasses build their structures in :meth:`build`, answer queries in
    :meth:`query` and absorb new rows in :meth:`insert`. Building and
    querying must go through ``bpm`` so the I/O is counted.
    """

    name: str = ""

    def __init__(self, bpm: BufferPoolManager, heap: HeapFile):
        self.bpm = bpm
        self.heap = heap

        # Seconds spent decoding stored data, for methods that measure it.
        self.decode_seconds = 0.0

        # Seconds spent ranking retrieved candidates, for ranked methods.
        self.ranking_seconds = 0.0

    def supports(self, query: Query) -> bool:
        raise NotImplementedError

    def build(self, rows: Sequence[tuple[RID, FileMetadata]]) -> None:
        raise NotImplementedError

    def query(self, query: Query) -> set[RID]:
        raise NotImplementedError

    def insert(self, rid: RID, record: FileMetadata) -> None:
        raise NotImplementedError

    def estimate_reads(self, query: Query, matches: int) -> Optional[float]:
        """Expected page reads of :meth:`query` from a cold cache when it
        matches ``matches`` rows; ``None`` when there is no model."""
        return None

    def size_pages(self) -> int:
        """Pages owned by the structure (the heap itself only for the scan)."""
        raise NotImplementedError

    def size_bytes(self) -> Optional[int]:
        """Payload bytes, when the structure tracks them; pages otherwise."""
        return None

    def describe(self) -> dict:
        """Structure details for the report (heights, cardinalities, ...)."""
        return {}


class SequentialScan(AccessMethod):
    """The baseline: read every heap page and test every record."""

    name = "seqscan"

    def supports(self, query: Query) -> bool:
        return True

    def build(self, rows: Sequence[tuple[RID, FileMetadata]]) -> None:
        pass

    def query(self, query: Query) -> set[RID]:
        return {
            rid for rid, data in self.heap.scan() if query.matches(FileMetadata.unpack(data))
        }

    def insert(self, rid: RID, record: FileMetadata) -> None:
        pass  # the heap insert itself is the whole cost

    def estimate_reads(self, query: Query, matches: int) -> Optional[float]:
        return float(self.heap.num_pages)

    def size_pages(self) -> int:
        return self.heap.num_pages


def codec_for(attribute: str, rows: Iterable[tuple[RID, FileMetadata]] = ()) -> KeyCodec:
    """The B+ tree key codec for an attribute; strings are sized to the data."""
    if attribute in INT_ATTRIBUTES:
        return IntKey()
    if attribute in FLOAT_ATTRIBUTES:
        return FloatKey()
    if attribute in STRING_ATTRIBUTES:
        longest = max((len(getattr(r, attribute).encode("utf-8")) for _, r in rows), default=0)
        return StringKey(max(16, longest))
    raise ValueError(f"no B+ tree key type for attribute {attribute!r}")


class BTreeMethod(AccessMethod):
    """One B+ tree per attribute, bulk loaded from the heap's RIDs."""

    name = "btree"

    def __init__(
        self,
        bpm: BufferPoolManager,
        heap: HeapFile,
        attributes: Sequence[str],
        fill_factor: float = 1.0,
    ):
        super().__init__(bpm, heap)
        self.attributes = tuple(attributes)
        self.fill_factor = fill_factor
        self.trees: dict[str, BPlusTree] = {}
        self._loaded: dict = {}  # TreeStats right after the bulk load

    def supports(self, query: Query) -> bool:
        return isinstance(query, (PointQuery, RangeQuery)) and query.attribute in self.attributes

    def build(self, rows: Sequence[tuple[RID, FileMetadata]]) -> None:
        for attribute in self.attributes:
            tree = BPlusTree(self.bpm, codec_for(attribute, rows))
            tree.bulk_load(((getattr(r, attribute), rid) for rid, r in rows), self.fill_factor)
            tree.flush()
            self.trees[attribute] = tree
            self._loaded[attribute] = tree.stats()

    def query(self, query: Query) -> set[RID]:
        tree = self.trees[query.attribute]
        if isinstance(query, PointQuery):
            return set(tree.search(query.value))
        return {rid for _, rid in tree.range_scan(query.low, query.high)}

    def insert(self, rid: RID, record: FileMetadata) -> None:
        for attribute, tree in self.trees.items():
            tree.insert(getattr(record, attribute), rid)

    def estimate_reads(self, query: Query, matches: int) -> Optional[float]:
        # One page per level down to the first leaf, then the leaves the
        # matches span: k consecutive entries at C entries per leaf overlap
        # 1 + (k - 1) / C leaves on average. Uses the bulk-loaded shape.
        s = self._loaded[query.attribute]
        per_leaf = s.num_entries / s.leaf_pages
        return s.height + max(matches - 1, 0) / per_leaf

    def size_pages(self) -> int:
        return sum(t.stats().total_pages for t in self.trees.values())

    def describe(self) -> dict:
        return {
            a: {"height": s.height, "leaf_pages": s.leaf_pages,
                "internal_pages": s.internal_pages}
            for a, s in ((a, t.stats()) for a, t in self.trees.items())
        }


class BitmapMethod(AccessMethod):
    """One bitmap index over every categorical attribute in the workload."""

    def __init__(
        self,
        bpm: BufferPoolManager,
        heap: HeapFile,
        attributes: Sequence[str],
        encoding: str = "roaring",
    ):
        super().__init__(bpm, heap)
        self.attributes = tuple(attributes)
        self.encoding = encoding
        self.name = f"bitmap-{encoding}"
        self.index: Optional[BitmapIndex] = None

    def supports(self, query: Query) -> bool:
        return isinstance(query, (PointQuery, RangeQuery)) and query.attribute in self.attributes

    def build(self, rows: Sequence[tuple[RID, FileMetadata]]) -> None:
        self.index = BitmapIndex(self.bpm, self.attributes, encoding=self.encoding)
        self.index.bulk_load(rows)

    def _values_read(self, query: Query) -> list:
        if isinstance(query, PointQuery):
            return [query.value]
        # The catalog lookup is free; only the bitmaps of values in range are read.
        return [
            v for v in self.index.values(query.attribute)
            if not isinstance(v, str) and query.low <= v <= query.high
        ]

    def query(self, query: Query) -> set[RID]:
        if isinstance(query, PointQuery):
            return set(self.index.search(query.attribute, query.value))
        values = self._values_read(query)
        return set(self.index.rids(self.index.any_of(query.attribute, values)))

    def insert(self, rid: RID, record: FileMetadata) -> None:
        self.index.insert(rid, record)

    def estimate_reads(self, query: Query, matches: int) -> Optional[float]:
        # The pages of each bitmap read, plus the RID directory pages holding
        # a match. Per-value bitmap sizes are in the catalog, which
        # BitmapIndex.stats() only sums per attribute, so read it directly.
        column = self.index._values[self.index._col(query.attribute)]
        bitmaps = sum(blob_pages(column[v].num_bytes)
                      for v in self._values_read(query) if v in column)
        s = self.index.stats()
        return bitmaps + pages_touched(s.rid_pages, s.num_rows, matches)

    def size_pages(self) -> int:
        return self.index.stats().total_pages

    def size_bytes(self) -> Optional[int]:
        s = self.index.stats()
        return s.total_bitmap_bytes + s.rid_pages * PAGE_SIZE

    def describe(self) -> dict:
        s = self.index.stats()
        return {"cardinality": s.cardinality, "bitmap_pages": s.bitmap_pages,
                "bitmap_bytes": s.bitmap_bytes, "rid_pages": s.rid_pages}


_RAW = struct.Struct("<Q")


def raw_encode(postings) -> bytes:
    """Uncompressed postings: sorted RIDs as fixed 8-byte integers."""
    ids = sorted(rid_to_int(rid) for rid in postings)
    return struct.pack(f"<{len(ids)}Q", *ids)


def raw_decode(data: bytes) -> list[RID]:
    return [int_to_rid(v) for (v,) in _RAW.iter_unpack(data)]


POSTING_ENCODINGS = {
    "vb": (compress_postings, decompress_postings),  # delta + variable-byte
    "raw": (raw_encode, raw_decode),
}


class InvertedMethod(AccessMethod):
    """Name-term search over posting lists stored in pages.

    Postings are collected with :class:`~dbmsi.inverted_index.InvertedIndex`
    and encoded with its delta + variable-byte compression (``"vb"``) or left
    uncompressed as 8-byte RIDs (``"raw"``, the baseline). The lists are
    written back to back into a postings file of whole pages, and the term
    dictionary (term -> offset and length) is kept in memory like a catalog,
    so a query is charged only the pages its posting lists overlap. This
    keeps the comparison page-based until the inverted index stores its own
    postings on disk. Time spent decoding lists is added to
    ``decode_seconds``.

    An insert rewrites each affected list at the end of the file; the old
    copy becomes dead space, which :meth:`size_pages` still counts.
    """

    def __init__(self, bpm: BufferPoolManager, heap: HeapFile, encoding: str = "vb"):
        super().__init__(bpm, heap)
        if encoding not in POSTING_ENCODINGS:
            raise ValueError(f"unknown posting encoding {encoding!r}")
        self.encoding = encoding
        self.name = "inverted" if encoding == "vb" else f"inverted-{encoding}"
        self._encode, self._decode = POSTING_ENCODINGS[encoding]
        self.terms: dict[str, tuple[int, int]] = {}  # term -> (offset, bytes)
        self._pages: list[int] = []  # postings file, in order
        self._end = 0  # bytes used in the postings file
        self.bm25 = BM25Index()

    def supports(self, query: Query) -> bool:
        return isinstance(query, TermQuery)

    def build(self, rows: Sequence[tuple[RID, FileMetadata]]) -> None:
        index = InvertedIndex()

        for rid, record in rows:
            index.add(rid, record.name)
            self.bm25.add(rid, record.name)

        for term in sorted(index.postings):
            self.terms[term] = self._append(
                self._encode(set(index.postings[term]))
            )

    def _append(self, data: bytes) -> tuple[int, int]:
        """Write ``data`` at the end of the postings file; returns (offset, length)."""
        offset = self._end
        view = memoryview(data)
        while view:
            in_page = self._end % PAGE_SIZE
            if in_page == 0:
                with self.bpm.create() as page:
                    self._pages.append(page.page_id)
            with self.bpm.fetch(self._pages[-1]) as page:
                n = min(len(view), PAGE_SIZE - in_page)
                page.data[in_page : in_page + n] = view[:n]
                page.mark_dirty()
            view = view[n:]
            self._end += n
        return offset, len(data)

    def _postings(self, term: str) -> set[RID]:
        entry = self.terms.get(term)
        if entry is None:
            return set()
        offset, length = entry
        first, last = offset // PAGE_SIZE, (offset + length - 1) // PAGE_SIZE
        parts = []
        for i in range(first, last + 1):
            with self.bpm.fetch(self._pages[i]) as page:
                parts.append(bytes(page.data))
        start = offset - first * PAGE_SIZE
        t = time.perf_counter()
        postings = set(self._decode(b"".join(parts)[start : start + length]))
        self.decode_seconds += time.perf_counter() - t
        return postings

    def _ordered(self, query: TermQuery) -> list[str]:
        if query.op == "and":
            # Shortest list first, and stop as soon as nothing is left.
            return sorted(query.terms, key=lambda t: self.terms.get(t, (0, 0))[1])
        return list(query.terms)

    def query(self, query: Query) -> set[RID]:
        terms = self._ordered(query)

        if query.op == "and":
            result = self._postings(terms[0])
            for term in terms[1:]:
                if not result:
                    break
                result &= self._postings(term)
        else:
            result: set[RID] = set()
            for term in terms:
                result |= self._postings(term)

        start = time.perf_counter()
        self.bm25.rank(
            candidates=result,
            query_terms=list(query.terms),
            k=10,
        )
        self.ranking_seconds += time.perf_counter() - start

        return result

    def estimate_reads(self, query: Query, matches: int) -> Optional[float]:
        # Each list read overlaps 1 + (bytes - 1) / page size pages on
        # average. Lists at independent places in a file of P pages, each
        # covering s pages, read P * (1 - prod(1 - s / P)) distinct pages,
        # which is the plain sum unless the file is small. An AND stops at a
        # term that is missing.
        total = len(self._pages)
        if not total:
            return 0.0
        missed = 1.0
        for term in self._ordered(query):
            entry = self.terms.get(term)
            if entry is None:
                if query.op == "and":
                    break
                continue
            missed *= max(0.0, 1 - pages_spanned(entry[1]) / total)
        return total * (1 - missed)

    def insert(self, rid: RID, record: FileMetadata) -> None:
        for term in set(record.tokens):
            postings = self._postings(term)
            postings.add(rid)
            self.terms[term] = self._append(self._encode(postings))

        self.bm25.add(rid, record.name)

    def size_pages(self) -> int:
        return len(self._pages)

    def size_bytes(self) -> Optional[int]:
        return sum(n for _, n in self.terms.values())

    def describe(self) -> dict:
        return {"encoding": self.encoding, "terms": len(self.terms), "file_bytes": self._end}
