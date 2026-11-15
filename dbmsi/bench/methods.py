"""Access methods under test, behind one interface.

Every method answers a :class:`~dbmsi.workload.Query` with the set of
matching RIDs from the same :class:`~dbmsi.storage.HeapFile`, so results can
be compared one-to-one against the sequential-scan baseline. All of them
read pages through the same :class:`~dbmsi.storage.BufferPoolManager`, so
page I/O is counted by the storage layer's :class:`~dbmsi.storage.IOStats`
and never estimated.

* :class:`SequentialScan` reads every heap page and tests each record;
* :class:`BTreeMethod` keeps one :class:`~dbmsi.btree.BPlusTree` per
  attribute and answers point and range queries;
* :class:`BitmapMethod` keeps one :class:`~dbmsi.bitmap.BitmapIndex` over
  the categorical attributes and answers point queries (and ranges on
  low-cardinality attributes by OR-ing the bitmaps of the values in range);
* :class:`InvertedMethod` answers name-term queries from compressed posting
  lists stored in pages.
"""

from __future__ import annotations

from typing import Iterable, Optional, Sequence

from dbmsi.bitmap import BitmapIndex
from dbmsi.btree import BPlusTree, FloatKey, IntKey, KeyCodec, StringKey
from dbmsi.inverted_index.compression import compress_postings, decompress_postings
from dbmsi.inverted_index.index import InvertedIndex
from dbmsi.storage import PAGE_SIZE, RID, BufferPoolManager, HeapFile
from dbmsi.workload import FileMetadata, PointQuery, Query, RangeQuery, TermQuery

INT_ATTRIBUTES = ("size", "uid", "mode", "depth")
FLOAT_ATTRIBUTES = ("mtime", "ctime", "atime")
STRING_ATTRIBUTES = ("extension", "category")


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

    def supports(self, query: Query) -> bool:
        raise NotImplementedError

    def build(self, rows: Sequence[tuple[RID, FileMetadata]]) -> None:
        raise NotImplementedError

    def query(self, query: Query) -> set[RID]:
        raise NotImplementedError

    def insert(self, rid: RID, record: FileMetadata) -> None:
        raise NotImplementedError

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

    def supports(self, query: Query) -> bool:
        return isinstance(query, (PointQuery, RangeQuery)) and query.attribute in self.attributes

    def build(self, rows: Sequence[tuple[RID, FileMetadata]]) -> None:
        for attribute in self.attributes:
            tree = BPlusTree(self.bpm, codec_for(attribute, rows))
            tree.bulk_load(((getattr(r, attribute), rid) for rid, r in rows), self.fill_factor)
            tree.flush()
            self.trees[attribute] = tree

    def query(self, query: Query) -> set[RID]:
        tree = self.trees[query.attribute]
        if isinstance(query, PointQuery):
            return set(tree.search(query.value))
        return {rid for _, rid in tree.range_scan(query.low, query.high)}

    def insert(self, rid: RID, record: FileMetadata) -> None:
        for attribute, tree in self.trees.items():
            tree.insert(getattr(record, attribute), rid)

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

    def query(self, query: Query) -> set[RID]:
        if isinstance(query, PointQuery):
            return set(self.index.search(query.attribute, query.value))
        # The catalog lookup is free; only the bitmaps of values in range are read.
        values = [
            v for v in self.index.values(query.attribute)
            if not isinstance(v, str) and query.low <= v <= query.high
        ]
        return set(self.index.rids(self.index.any_of(query.attribute, values)))

    def insert(self, rid: RID, record: FileMetadata) -> None:
        self.index.insert(rid, record)

    def size_pages(self) -> int:
        return self.index.stats().total_pages

    def size_bytes(self) -> Optional[int]:
        s = self.index.stats()
        return s.total_bitmap_bytes + s.rid_pages * PAGE_SIZE

    def describe(self) -> dict:
        s = self.index.stats()
        return {"cardinality": s.cardinality, "bitmap_pages": s.bitmap_pages,
                "rid_pages": s.rid_pages}


class InvertedMethod(AccessMethod):
    """Name-term search over compressed posting lists stored in pages.

    Postings are collected with :class:`~dbmsi.inverted_index.InvertedIndex`
    and compressed with its delta + variable-byte encoding. The lists are
    written back to back into a postings file of whole pages, and the term
    dictionary (term -> offset and length) is kept in memory like a catalog,
    so a query is charged only the pages its posting lists overlap. This
    keeps the comparison page-based until the inverted index stores its own
    postings on disk.

    An insert rewrites each affected list at the end of the file; the old
    copy becomes dead space, which :meth:`size_pages` still counts.
    """

    name = "inverted"

    def __init__(self, bpm: BufferPoolManager, heap: HeapFile):
        super().__init__(bpm, heap)
        self.terms: dict[str, tuple[int, int]] = {}  # term -> (offset, bytes)
        self._pages: list[int] = []  # postings file, in order
        self._end = 0  # bytes used in the postings file

    def supports(self, query: Query) -> bool:
        return isinstance(query, TermQuery)

    def build(self, rows: Sequence[tuple[RID, FileMetadata]]) -> None:
        index = InvertedIndex()
        for rid, record in rows:
            index.add(rid, record.name)
        for term in sorted(index.postings):
            self.terms[term] = self._append(compress_postings(set(index.postings[term])))

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
        return set(decompress_postings(b"".join(parts)[start : start + length]))

    def query(self, query: Query) -> set[RID]:
        if query.op == "and":
            # Shortest list first, and stop as soon as nothing is left.
            terms = sorted(query.terms, key=lambda t: self.terms.get(t, (0, 0))[1])
            result = self._postings(terms[0])
            for term in terms[1:]:
                if not result:
                    break
                result &= self._postings(term)
            return result
        result: set[RID] = set()
        for term in query.terms:
            result |= self._postings(term)
        return result

    def insert(self, rid: RID, record: FileMetadata) -> None:
        for term in set(record.tokens):
            postings = self._postings(term)
            postings.add(rid)
            self.terms[term] = self._append(compress_postings(postings))

    def size_pages(self) -> int:
        return len(self._pages)

    def size_bytes(self) -> Optional[int]:
        return sum(n for _, n in self.terms.values())

    def describe(self) -> dict:
        return {"terms": len(self.terms), "file_bytes": self._end}
