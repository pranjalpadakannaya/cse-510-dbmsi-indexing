"""Disk-resident bitmap index over the categorical attributes of a table.

For every distinct value of every indexed attribute the index keeps one
bitmap with a bit per row: bit ``i`` is set when row ``i`` has that value.
A query combines bitmaps with AND, OR and NOT and only then turns the set
bits into RIDs, so a multi-attribute filter never touches the table.

One index covers several attributes because they must share the same row
numbering to be combined. Rows are numbered in the order they are added, and
a *RID directory* (682 RIDs per page) maps a row number back to its RID.

Everything is stored in 4 KB pages read through the
:class:`BufferPoolManager`, so queries are charged real page I/O:

- each bitmap is a chain of pages (see :mod:`dbmsi.bitmap.blob`);
- the RID directory is one page per 682 rows, and a query reads only the
  directory pages that hold a matching row;
- the catalog (the attributes, the values and where each bitmap starts) is
  read once when the index is opened and then kept in memory, like the
  system catalog of a DBMS, so it is not charged to individual queries.

The index is identified by its meta page id; pass it back to the constructor
to reopen it::

    index = BitmapIndex(bpm, ["extension", "category"], encoding="wah")
    index.bulk_load((rid, record) for rid, record in table_rows)
    index.search("extension", "pdf")                       # -> [rid, ...]
    hits = index.bitmap("extension", "pdf") & ~index.bitmap("category", "code")
    index.rids(hits)
    reopened = BitmapIndex(bpm, meta_page_id=index.meta_page_id)

Concurrency: public operations take a single index-wide latch, so an index
can be shared between threads; operations on one index are serialized.
"""

from __future__ import annotations

import json
import struct
import threading
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Optional, Sequence, Union

from dbmsi.storage import PAGE_SIZE, RID, BufferPoolManager

from .blob import blob_pages, read_blob, write_blob
from .plain import Bitmap
from .roaring import RoaringBitmap
from .wah import WAHBitmap

AnyBitmap = Union[Bitmap, WAHBitmap, RoaringBitmap]

ENCODINGS: dict[str, type] = {"plain": Bitmap, "wah": WAHBitmap, "roaring": RoaringBitmap}
_ENCODING_IDS = {"plain": 0, "wah": 1, "roaring": 2}
_ENCODING_NAMES = {v: k for k, v in _ENCODING_IDS.items()}

_MAGIC = b"BMI1"
# magic | encoding | number of rows | catalog first page | catalog bytes
_META = struct.Struct("<4sBQiI")

RIDS_PER_PAGE = PAGE_SIZE // RID.SIZE


class _Entry:
    """Where the bitmap of one value is stored."""

    __slots__ = ("first_page", "num_bytes", "count")

    def __init__(self, first_page: int, num_bytes: int, count: int):
        self.first_page = first_page
        self.num_bytes = num_bytes
        self.count = count  # rows with this value


@dataclass
class BitmapIndexStats:
    encoding: str
    num_rows: int
    cardinality: dict[str, int]  # distinct values per attribute
    bitmap_bytes: dict[str, int]  # serialized bitmap bytes per attribute
    bitmap_pages: dict[str, int]  # bitmap pages per attribute
    rid_pages: int
    catalog_pages: int

    @property
    def total_bitmap_bytes(self) -> int:
        return sum(self.bitmap_bytes.values())

    @property
    def total_bitmap_pages(self) -> int:
        return sum(self.bitmap_pages.values())

    @property
    def total_pages(self) -> int:
        """Bitmap, RID directory and catalog pages plus the meta page."""
        return self.total_bitmap_pages + self.rid_pages + self.catalog_pages + 1


def _sort_key(value: Any) -> tuple:
    return (type(value).__name__, value)


class BitmapIndex:
    def __init__(
        self,
        bpm: BufferPoolManager,
        attributes: Optional[Sequence[str]] = None,
        encoding: str = "wah",
        meta_page_id: Optional[int] = None,
    ):
        """Create a new empty index, or reopen one with ``meta_page_id``.

        :param attributes: names of the indexed attributes; required for a
            new index and read from the catalog when reopening.
        :param encoding: ``"wah"`` or ``"roaring"`` (compressed), or
            ``"plain"`` (uncompressed baseline). Ignored when reopening.
        """
        self.bpm = bpm
        self._latch = threading.RLock()
        if meta_page_id is None:
            if not attributes:
                raise ValueError("at least one attribute is required for a new index")
            if len(set(attributes)) != len(attributes):
                raise ValueError("attribute names must be distinct")
            if encoding not in ENCODINGS:
                raise ValueError(f"unknown encoding {encoding!r}; use one of {sorted(ENCODINGS)}")
            self.attributes = tuple(attributes)
            self.encoding = encoding
            self.num_rows = 0
            self._values: list[dict[Any, _Entry]] = [{} for _ in self.attributes]
            self._rid_pages: list[int] = []
            with bpm.create() as meta:
                self.meta_page_id = meta.page_id
            self._catalog_page: Optional[int] = None
            self._catalog_bytes = 0
            self.flush()
        else:
            self.meta_page_id = meta_page_id
            self._read_meta()
        self._bitmap_cls = ENCODINGS[self.encoding]
        self._column = {name: i for i, name in enumerate(self.attributes)}

    # -- meta page and catalog ----------------------------------------------

    def flush(self) -> None:
        """Persist the catalog and the row count.

        Bitmap and RID directory pages are written as rows are added; the
        catalog is kept in memory in between, so call this before closing a
        file that will be reopened. :meth:`bulk_load` calls it for you.
        """
        with self._latch:
            catalog = json.dumps(
                {
                    "attributes": list(self.attributes),
                    "rid_pages": self._rid_pages,
                    "values": [
                        [[v, e.first_page, e.num_bytes, e.count] for v, e in column.items()]
                        for column in self._values
                    ],
                },
                separators=(",", ":"),
            ).encode("utf-8")
            self._catalog_page = write_blob(self.bpm, catalog, self._catalog_page)
            self._catalog_bytes = len(catalog)
            with self.bpm.fetch(self.meta_page_id) as page:
                _META.pack_into(
                    page.data, 0, _MAGIC, _ENCODING_IDS[self.encoding],
                    self.num_rows, self._catalog_page, self._catalog_bytes,
                )
                page.mark_dirty()

    def _read_meta(self) -> None:
        with self.bpm.fetch(self.meta_page_id) as page:
            magic, encoding_id, num_rows, catalog_page, catalog_bytes = _META.unpack_from(
                page.data, 0
            )
        if magic != _MAGIC:
            raise ValueError(f"page {self.meta_page_id} is not a bitmap index meta page")
        self.encoding = _ENCODING_NAMES[encoding_id]
        self.num_rows = num_rows
        self._catalog_page = catalog_page
        self._catalog_bytes = catalog_bytes
        catalog = json.loads(read_blob(self.bpm, catalog_page).decode("utf-8"))
        self.attributes = tuple(catalog["attributes"])
        self._rid_pages = list(catalog["rid_pages"])
        self._values = [
            {v: _Entry(first, size, count) for v, first, size, count in column}
            for column in catalog["values"]
        ]

    def __len__(self) -> int:
        return self.num_rows

    # -- input handling -----------------------------------------------------

    def _row(self, values: Any) -> list:
        """The attribute values of one row, in attribute order.

        ``values`` may be a mapping, a sequence aligned with ``attributes``,
        or any object with the attributes as fields (such as a
        :class:`~dbmsi.workload.FileMetadata`).
        """
        if isinstance(values, Mapping):
            row = [values[name] for name in self.attributes]
        elif isinstance(values, (tuple, list)):
            if len(values) != len(self.attributes):
                raise ValueError(
                    f"expected {len(self.attributes)} values, got {len(values)}"
                )
            row = list(values)
        else:
            row = [getattr(values, name) for name in self.attributes]
        for value in row:
            self._check_value(value)
        return row

    @staticmethod
    def _check_value(value: Any) -> None:
        if isinstance(value, bool) or not isinstance(value, (str, int)):
            raise TypeError(f"indexed values must be str or int, not {type(value).__name__}")

    def _col(self, attribute: str) -> int:
        try:
            return self._column[attribute]
        except KeyError:
            raise KeyError(f"attribute {attribute!r} is not indexed") from None

    # -- building -----------------------------------------------------------

    def bulk_load(self, rows: Iterable[tuple[RID, Any]]) -> None:
        """Build the index from ``(rid, values)`` pairs; the index must be empty.

        The RID directory and then each bitmap are written to new pages one
        after another, so reading any of them is sequential I/O.
        """
        with self._latch:
            if self.num_rows:
                raise RuntimeError("bulk_load needs an empty index")
            positions: list[dict[Any, list[int]]] = [defaultdict(list) for _ in self.attributes]
            rids = bytearray()
            n = 0
            for rid, values in rows:
                for column, value in enumerate(self._row(values)):
                    positions[column][value].append(n)
                rids += RID(*rid).pack()
                n += 1

            page_bytes = RIDS_PER_PAGE * RID.SIZE
            for start in range(0, len(rids), page_bytes):
                with self.bpm.create() as page:
                    chunk = rids[start : start + page_bytes]
                    page.data[: len(chunk)] = chunk
                    self._rid_pages.append(page.page_id)

            for column, by_value in enumerate(positions):
                for value in sorted(by_value, key=_sort_key):
                    rows_with_value = by_value[value]
                    data = self._bitmap_cls.from_positions(rows_with_value, n).to_bytes()
                    self._values[column][value] = _Entry(
                        write_blob(self.bpm, data), len(data), len(rows_with_value)
                    )
            self.num_rows = n
            self.flush()

    def insert(self, rid: RID, values: Any) -> int:
        """Add one row and return its row number.

        The bitmap of each of the row's values is read, extended by one bit
        and written back; bitmaps of other values are left alone (they are
        padded with zeros when read). The row's RID goes on the last RID
        directory page.
        """
        with self._latch:
            row = self._row(values)
            number = self.num_rows
            for column, value in enumerate(row):
                entry = self._values[column].get(value)
                bitmap = self._bitmap_cls.zeros(0) if entry is None else self._load(entry)
                bitmap = bitmap.resized(number)
                bitmap.append(True)
                data = bitmap.to_bytes()
                if entry is None:
                    self._values[column][value] = _Entry(write_blob(self.bpm, data), len(data), 1)
                else:
                    write_blob(self.bpm, data, entry.first_page)
                    entry.num_bytes = len(data)
                    entry.count += 1

            slot = number % RIDS_PER_PAGE
            if slot == 0:
                with self.bpm.create() as page:
                    page.data[: RID.SIZE] = RID(*rid).pack()
                    self._rid_pages.append(page.page_id)
            else:
                with self.bpm.fetch(self._rid_pages[-1]) as page:
                    offset = slot * RID.SIZE
                    page.data[offset : offset + RID.SIZE] = RID(*rid).pack()
                    page.mark_dirty()
            self.num_rows = number + 1
            return number

    # -- catalog lookups (no page I/O) --------------------------------------

    def values(self, attribute: str) -> list:
        """The distinct values of ``attribute``, sorted."""
        with self._latch:
            return sorted(self._values[self._col(attribute)], key=_sort_key)

    def cardinality(self, attribute: str) -> int:
        with self._latch:
            return len(self._values[self._col(attribute)])

    def count(self, attribute: str, value: Any) -> int:
        """Number of rows where ``attribute`` equals ``value``."""
        with self._latch:
            entry = self._values[self._col(attribute)].get(value)
            return 0 if entry is None else entry.count

    # -- bitmaps ------------------------------------------------------------

    def _load(self, entry: _Entry) -> AnyBitmap:
        return self._bitmap_cls.from_bytes(read_blob(self.bpm, entry.first_page))

    def bitmap(self, attribute: str, value: Any) -> AnyBitmap:
        """The bitmap of the rows where ``attribute`` equals ``value``.

        Reads the pages of that bitmap. Combine the results with ``&``,
        ``|`` and ``~``, then pass them to :meth:`rids` or call ``count()``.
        A value that does not occur gives an all-zero bitmap without I/O.
        """
        with self._latch:
            entry = self._values[self._col(attribute)].get(value)
            if entry is None:
                return self._bitmap_cls.zeros(self.num_rows)
            return self._load(entry).resized(self.num_rows)

    def any_of(self, attribute: str, values: Iterable[Any]) -> AnyBitmap:
        """The bitmap of the rows where ``attribute`` is one of ``values``."""
        with self._latch:
            result = self._bitmap_cls.zeros(self.num_rows)
            for value in values:
                result = result | self.bitmap(attribute, value)
            return result

    def rids(self, bitmap: AnyBitmap) -> list[RID]:
        """The RIDs of the rows whose bits are set, in row order.

        Reads each RID directory page that holds at least one of them.
        """
        with self._latch:
            if bitmap.num_bits > self.num_rows:
                raise ValueError("bitmap is longer than the index")
            out: list[RID] = []
            current, data = -1, b""
            for position in bitmap.positions():
                page_no, slot = divmod(position, RIDS_PER_PAGE)
                if page_no != current:
                    with self.bpm.fetch(self._rid_pages[page_no]) as page:
                        data = bytes(page.data)
                    current = page_no
                out.append(RID.unpack(data, slot * RID.SIZE))
            return out

    # -- queries ------------------------------------------------------------

    def search(self, attribute: str, value: Any) -> list[RID]:
        """RIDs of the rows where ``attribute`` equals ``value``."""
        with self._latch:
            return self.rids(self.bitmap(attribute, value))

    def search_and(self, *conditions: tuple[str, Any]) -> list[RID]:
        """RIDs of the rows matching every ``(attribute, value)`` condition."""
        return self._search_all(conditions, conjunction=True)

    def search_or(self, *conditions: tuple[str, Any]) -> list[RID]:
        """RIDs of the rows matching at least one ``(attribute, value)`` condition."""
        return self._search_all(conditions, conjunction=False)

    def _search_all(self, conditions: Sequence[tuple[str, Any]], conjunction: bool) -> list[RID]:
        if not conditions:
            raise ValueError("at least one condition is required")
        with self._latch:
            if conjunction:
                # Cheapest first, and stop reading bitmaps once nothing is left.
                conditions = sorted(conditions, key=lambda c: self.count(*c))
            result = self.bitmap(*conditions[0])
            for condition in conditions[1:]:
                if conjunction and result.count() == 0:
                    break
                other = self.bitmap(*condition)
                result = result & other if conjunction else result | other
            return self.rids(result)

    # -- introspection ------------------------------------------------------

    def stats(self) -> BitmapIndexStats:
        """Sizes for the storage-overhead experiments (no page I/O)."""
        with self._latch:
            return BitmapIndexStats(
                encoding=self.encoding,
                num_rows=self.num_rows,
                cardinality={a: len(c) for a, c in zip(self.attributes, self._values)},
                bitmap_bytes={
                    a: sum(e.num_bytes for e in c.values())
                    for a, c in zip(self.attributes, self._values)
                },
                bitmap_pages={
                    a: sum(blob_pages(e.num_bytes) for e in c.values())
                    for a, c in zip(self.attributes, self._values)
                },
                rid_pages=len(self._rid_pages),
                catalog_pages=blob_pages(self._catalog_bytes),
            )
