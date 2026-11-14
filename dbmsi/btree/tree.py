"""Disk-resident B+ tree mapping keys to RIDs.

Every node is one page accessed through the :class:`BufferPoolManager`, so
lookups, inserts and scans are charged real page I/O in :class:`IOStats`.
Entries are ``(key, rid)`` pairs; duplicate keys are allowed (many files share
a size or extension) and are kept distinct by their RID, so the tree behaves
as a unique index over the composite ``(key, rid)``.

The tree is identified by its meta page id; pass it back to the constructor
to reopen an existing tree::

    tree = BPlusTree(bpm, IntKey())
    tree.insert(4096, rid)
    tree.search(4096)            # -> [rid, ...]
    tree.flush()
    reopened = BPlusTree(bpm, meta_page_id=tree.meta_page_id)

Concurrency: public operations take a single tree-wide latch, so a tree can
be shared between threads; operations on one tree are serialized.
"""

from __future__ import annotations

import struct
import threading
from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from typing import Any, Iterator, Optional

from dbmsi.storage import INVALID_PAGE_ID, RID, BufferPoolManager

from .keys import MAX_RID_BYTES, MIN_RID_BYTES, KeyCodec, codec_from_meta
from .node import InternalNode, LeafNode, Node, NodeLayout

_MAGIC = b"BPT1"
# magic | codec type | codec param | leaf cap | internal cap | root | height | entries
_META = struct.Struct("<4sBHHHiHQ")


class DuplicateEntryError(KeyError):
    """Raised when inserting a ``(key, rid)`` pair that is already indexed."""


@dataclass
class TreeStats:
    height: int
    num_entries: int
    leaf_pages: int
    internal_pages: int

    @property
    def total_pages(self) -> int:
        """Node pages plus the meta page."""
        return self.leaf_pages + self.internal_pages + 1


class BPlusTree:
    def __init__(
        self,
        bpm: BufferPoolManager,
        codec: Optional[KeyCodec] = None,
        meta_page_id: Optional[int] = None,
        leaf_capacity: Optional[int] = None,
        internal_capacity: Optional[int] = None,
    ):
        """Create a new empty tree, or reopen one with ``meta_page_id``.

        :param codec: key encoding; required for a new tree. When reopening it
            may be omitted (it is read from the meta page) or must match.
        :param leaf_capacity, internal_capacity: maximum entries per leaf and
            separator keys per internal node; default to what fits in a page.
        """
        self.bpm = bpm
        self._latch = threading.RLock()
        if meta_page_id is None:
            if codec is None:
                raise ValueError("a codec is required to create a new tree")
            self.codec = codec
            self.layout = NodeLayout(codec.entry_size, leaf_capacity, internal_capacity)
            with bpm.create() as meta:
                self.meta_page_id = meta.page_id
            with bpm.create() as root:
                self.layout.write(LeafNode(), root.data)
                self.root_page_id = root.page_id
            self.height = 1
            self.num_entries = 0
            self._write_meta()
        else:
            self.meta_page_id = meta_page_id
            self._read_meta(codec)

    # -- meta page ----------------------------------------------------------

    def _write_meta(self) -> None:
        with self.bpm.fetch(self.meta_page_id) as page:
            _META.pack_into(
                page.data,
                0,
                _MAGIC,
                self.codec.type_id,
                self.codec.param,
                self.layout.leaf_capacity,
                self.layout.internal_capacity,
                self.root_page_id,
                self.height,
                self.num_entries,
            )
            page.mark_dirty()

    def _read_meta(self, codec: Optional[KeyCodec]) -> None:
        with self.bpm.fetch(self.meta_page_id) as page:
            magic, ctype, cparam, leaf_cap, int_cap, root, height, n = _META.unpack_from(
                page.data, 0
            )
        if magic != _MAGIC:
            raise ValueError(f"page {self.meta_page_id} is not a B+ tree meta page")
        stored = codec_from_meta(ctype, cparam)
        if codec is not None and codec != stored:
            raise ValueError(f"tree was built with {stored!r}, not {codec!r}")
        self.codec = stored
        self.layout = NodeLayout(stored.entry_size, leaf_cap, int_cap)
        self.root_page_id = root
        self.height = height
        self.num_entries = n

    def flush(self) -> None:
        """Persist the entry count to the meta page.

        Structural changes (root splits and collapses) are written to the
        meta page immediately; only ``num_entries`` is kept in memory between
        flushes, so routine inserts and deletes do not touch the meta page.
        """
        with self._latch:
            self._write_meta()

    def __len__(self) -> int:
        return self.num_entries

    # -- node access --------------------------------------------------------

    def _read_node(self, page_id: int) -> Node:
        with self.bpm.fetch(page_id) as page:
            return self.layout.read(page.data)

    def _write_node(self, page_id: int, node: Node) -> None:
        with self.bpm.fetch(page_id) as page:
            self.layout.write(node, page.data)
            page.mark_dirty()

    def _new_node(self, node: Node) -> int:
        with self.bpm.create() as page:
            self.layout.write(node, page.data)
            return page.page_id

    def _descend(self, entry: bytes) -> tuple[int, list[tuple[int, InternalNode, int]]]:
        """Walk from the root to the leaf that should contain ``entry``.

        Returns the leaf's page id and the path of
        ``(page_id, internal_node, child_index)`` from the root down.
        """
        path: list[tuple[int, InternalNode, int]] = []
        page_id = self.root_page_id
        for _ in range(self.height - 1):
            node = self._read_node(page_id)
            idx = bisect_right(node.keys, entry)
            path.append((page_id, node, idx))
            page_id = node.children[idx]
        return page_id, path

    # -- lookups ------------------------------------------------------------

    def search(self, key: Any) -> list[RID]:
        """Return the RIDs of every entry whose key equals ``key``, in RID order."""
        prefix = self.codec.encode(key)
        return [
            self.codec.decode_entry(e)[1]
            for e in self._iter_entries(prefix + MIN_RID_BYTES, prefix + MAX_RID_BYTES)
        ]

    def _iter_entries(
        self, start: Optional[bytes], stop: Optional[bytes]
    ) -> Iterator[bytes]:
        """Yield encoded entries with ``start <= entry <= stop`` in order.

        ``None`` leaves a side unbounded. The scan descends once to the first
        qualifying leaf and then follows the leaf chain, reading the next leaf
        only if the current one ends below ``stop``. Each leaf is pinned only
        while its entries are copied out, so an abandoned iterator never
        leaves a page pinned.
        """
        with self._latch:
            if start is None:
                page_id, path = self._leftmost_leaf(), []
            else:
                page_id, path = self._descend(start)
            leaf = self._read_node(page_id)
        # Everything right of this leaf is >= the nearest separator above it,
        # so a point lookup never reads a sibling it cannot match.
        high = next(
            (n.keys[i] for _, n, i in reversed(path) if i < len(n.keys)), None
        )
        pos = 0 if start is None else bisect_left(leaf.entries, start)
        while True:
            entries = leaf.entries
            if stop is not None and entries and entries[-1] >= stop:
                yield from entries[pos : bisect_right(entries, stop)]
                return
            yield from entries[pos:]
            if leaf.next_leaf == INVALID_PAGE_ID:
                return
            if stop is not None and high is not None and high > stop:
                return
            high = None
            with self._latch:
                leaf = self._read_node(leaf.next_leaf)
            pos = 0

    def _leftmost_leaf(self) -> int:
        page_id = self.root_page_id
        for _ in range(self.height - 1):
            page_id = self._read_node(page_id).children[0]
        return page_id

    # -- insertion ----------------------------------------------------------

    def insert(self, key: Any, rid: RID) -> None:
        """Index ``rid`` under ``key``.

        Raises :class:`DuplicateEntryError` if this exact pair is present.
        """
        entry = self.codec.encode_entry(key, rid)
        with self._latch:
            leaf_id, path = self._descend(entry)
            with self.bpm.fetch(leaf_id) as page:
                leaf = self.layout.read(page.data)
                pos = bisect_left(leaf.entries, entry)
                if pos < len(leaf.entries) and leaf.entries[pos] == entry:
                    raise DuplicateEntryError(f"({key!r}, {rid}) is already indexed")
                leaf.entries.insert(pos, entry)
                self.num_entries += 1
                if len(leaf.entries) <= self.layout.leaf_capacity:
                    self.layout.write(leaf, page.data)
                    page.mark_dirty()
                    return
                # Split: the upper half moves to a new right sibling.
                mid = len(leaf.entries) // 2
                right = LeafNode(leaf.entries[mid:], leaf.next_leaf)
                right_id = self._new_node(right)
                leaf.entries = leaf.entries[:mid]
                leaf.next_leaf = right_id
                self.layout.write(leaf, page.data)
                page.mark_dirty()
            self._insert_into_parent(path, right.entries[0], right_id)

    def _insert_into_parent(
        self, path: list[tuple[int, InternalNode, int]], sep: bytes, right_id: int
    ) -> None:
        """Insert separator ``sep`` pointing at ``right_id``, splitting upward."""
        while path:
            page_id, node, idx = path.pop()
            node.keys.insert(idx, sep)
            node.children.insert(idx + 1, right_id)
            if len(node.keys) <= self.layout.internal_capacity:
                self._write_node(page_id, node)
                return
            # Split: the middle key moves up instead of being copied.
            mid = len(node.keys) // 2
            sep = node.keys[mid]
            right = InternalNode(node.keys[mid + 1 :], node.children[mid + 1 :])
            right_id = self._new_node(right)
            node.keys = node.keys[:mid]
            node.children = node.children[: mid + 1]
            self._write_node(page_id, node)
        # The root itself split: grow the tree by one level.
        self.root_page_id = self._new_node(
            InternalNode([sep], [self.root_page_id, right_id])
        )
        self.height += 1
        self._write_meta()

    # -- introspection ------------------------------------------------------

    def stats(self) -> TreeStats:
        """Count node pages by walking the whole tree (this performs I/O)."""
        with self._latch:
            leaves = internals = 0
            level = [self.root_page_id]
            for depth in range(self.height):
                if depth == self.height - 1:
                    leaves = len(level)
                    break
                internals += len(level)
                level = [c for pid in level for c in self._read_node(pid).children]
            return TreeStats(self.height, self.num_entries, leaves, internals)

    def validate(self) -> None:
        """Check every structural invariant; raise ``AssertionError`` if broken.

        Intended for tests: verifies sorted nodes, separator bounds, uniform
        leaf depth, minimum occupancy (except at the root), an intact leaf
        chain, and the entry count.
        """
        with self._latch:
            leaves: list[int] = []
            self._validate(self.root_page_id, 1, None, None, leaves)
            count = 0
            for i, pid in enumerate(leaves):
                node = self._read_node(pid)
                count += len(node.entries)
                expected = leaves[i + 1] if i + 1 < len(leaves) else INVALID_PAGE_ID
                assert node.next_leaf == expected, f"broken leaf chain at {pid}"
            assert count == self.num_entries, f"{count} entries != {self.num_entries}"

    def _validate(
        self,
        page_id: int,
        depth: int,
        low: Optional[bytes],
        high: Optional[bytes],
        leaves: list[int],
    ) -> None:
        node = self._read_node(page_id)
        is_root = page_id == self.root_page_id
        items = node.entries if node.is_leaf else node.keys
        assert items == sorted(items) and len(set(items)) == len(items), (
            f"node {page_id} is not strictly sorted"
        )
        if items:
            assert low is None or items[0] >= low, f"node {page_id} below its bound"
            assert high is None or items[-1] < high, f"node {page_id} above its bound"
        if node.is_leaf:
            assert depth == self.height, f"leaf {page_id} at depth {depth}"
            if not is_root:
                assert len(items) >= self.layout.min_leaf_entries, (
                    f"leaf {page_id} underfull"
                )
            leaves.append(page_id)
            return
        assert depth < self.height, f"internal node {page_id} at leaf depth"
        assert len(node.children) == len(node.keys) + 1
        if is_root:
            assert node.keys, "internal root has no keys"
        else:
            assert len(node.keys) >= self.layout.min_internal_keys, (
                f"internal node {page_id} underfull"
            )
        bounds = [low, *node.keys, high]
        for i, child in enumerate(node.children):
            self._validate(child, depth + 1, bounds[i], bounds[i + 1], leaves)
