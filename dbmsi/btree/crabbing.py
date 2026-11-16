"""B+ tree with latch crabbing, so searches and inserts run concurrently.

:class:`CrabbingBPlusTree` has the same on-disk format and interface as
:class:`~dbmsi.btree.BPlusTree`, but replaces its tree-wide latch with one
reader-writer latch per node page (see :mod:`dbmsi.btree.latch`):

* **Search** latches the root shared, then repeatedly latches the child and
  releases the parent ("crabbing"), so it holds at most two latches and
  only one once it reaches the leaf. Range scans move along the leaf chain
  one leaf at a time, left to right.
* **Insert** first tries the optimistic path: shared latches down to the
  leaf, an exclusive latch on the leaf only. If the leaf has room the entry
  goes in and nothing else was blocked. If the leaf is full, the insert
  restarts pessimistically: exclusive latches from the root down, releasing
  every ancestor as soon as a node is *safe* (it has room for one more key,
  so a split below cannot reach above it). Only the unsafe part of the path
  stays latched while the split propagates.
* A separate latch protects the root pointer and height. Readers hold it
  only until the root page is latched; a pessimistic insert holds it until
  the root is known to be safe, so a root split is never observed half done.

Latches are always taken top-down and, along the leaf chain, left to right,
and never re-entered, so there is no deadlock between searches and inserts.

Deletes, bulk loads and the maintenance calls (``flush``, ``stats``,
``validate``) take a tree-wide latch exclusively. A delete may merge a node
into its *left* sibling, which a left-to-right scan could be waiting on, so
running deletes alone keeps the latch order acyclic without restarts.
Searches and inserts hold the tree-wide latch shared, which never blocks
each other.
"""

from __future__ import annotations

import threading
from bisect import bisect_left, bisect_right
from typing import Any, Iterator, Optional

from dbmsi.storage import INVALID_PAGE_ID, RID

from .latch import LatchTable, RWLatch
from .node import InternalNode, LeafNode, Node
from .tree import BPlusTree, DuplicateEntryError


class _ExclusiveTreeLatch:
    """Context manager the base class uses as ``self._latch``."""

    def __init__(self, latch: RWLatch):
        self._latch = latch

    def __enter__(self) -> None:
        self._latch.acquire_exclusive()

    def __exit__(self, *exc) -> None:
        self._latch.release_exclusive()


class CrabbingBPlusTree(BPlusTree):
    latching = "latch crabbing"

    def __init__(self, *args, **kwargs):
        self._tree_latch = RWLatch()
        self._root_latch = RWLatch()
        self._latches = LatchTable()
        self._count_lock = threading.Lock()
        super().__init__(*args, **kwargs)
        # Base-class operations that are not overridden run alone.
        self._latch = _ExclusiveTreeLatch(self._tree_latch)
        self.optimistic_inserts = 0
        self.pessimistic_inserts = 0

    def latches_free(self) -> bool:
        """True if no latch is held or awaited (for tests)."""
        return (self._tree_latch.is_free and self._root_latch.is_free
                and self._latches.all_free())

    # -- lookups ------------------------------------------------------------

    def _shared_descent(self, entry: Optional[bytes]) -> tuple[int, LeafNode, Optional[bytes]]:
        """Crab down with shared latches to the leaf for ``entry`` (``None``
        means the leftmost leaf). Returns the leaf's page id, its contents
        and the nearest separator to its right, with every latch released."""
        with self._root_latch.shared():
            page_id = self.root_page_id
            latch = self._latches.get(page_id)
            latch.acquire_shared()
        try:
            high = None
            node = self._read_node(page_id)
            while not node.is_leaf:
                idx = 0 if entry is None else bisect_right(node.keys, entry)
                if idx < len(node.keys):
                    high = node.keys[idx]
                child_id = node.children[idx]
                child_latch = self._latches.get(child_id)
                child_latch.acquire_shared()
                latch.release_shared()
                page_id, latch = child_id, child_latch
                node = self._read_node(page_id)
            return page_id, node, high
        finally:
            latch.release_shared()

    def _read_leaf_shared(self, page_id: int) -> LeafNode:
        latch = self._latches.get(page_id)
        with self._tree_latch.shared():
            with latch.shared():
                return self._read_node(page_id)

    def _iter_entries(
        self, start: Optional[bytes], stop: Optional[bytes]
    ) -> Iterator[bytes]:
        """As in :class:`BPlusTree`: entries with ``start <= entry <= stop``.

        Each leaf is copied under its shared latch, and no latch is held
        while entries are yielded, so an abandoned iterator blocks nobody.
        """
        with self._tree_latch.shared():
            _, leaf, high = self._shared_descent(start)
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
            leaf = self._read_leaf_shared(leaf.next_leaf)
            pos = 0

    # -- insertion ----------------------------------------------------------

    def insert(self, key: Any, rid: RID) -> None:
        """Index ``rid`` under ``key``; see the module docstring for latching.

        Raises :class:`DuplicateEntryError` if this exact pair is present.
        """
        entry = self.codec.encode_entry(key, rid)
        with self._tree_latch.shared():
            if self._insert_optimistic(entry, key, rid):
                return
            self._insert_pessimistic(entry, key, rid)

    def _leaf_insert(self, page_id: int, entry: bytes, key: Any, rid: RID,
                     allow_split: bool) -> Optional[tuple[LeafNode, bytes, int]]:
        """Insert ``entry`` into the exclusively latched leaf ``page_id``.

        Returns ``None`` when it fit. On overflow it returns ``False`` without
        writing if ``allow_split`` is false, and otherwise splits the leaf and
        returns ``(leaf, separator, right_id)`` for the parent.
        """
        with self.bpm.fetch(page_id) as page:
            leaf = self.layout.read(page.data)
            pos = bisect_left(leaf.entries, entry)
            if pos < len(leaf.entries) and leaf.entries[pos] == entry:
                raise DuplicateEntryError(f"({key!r}, {rid}) is already indexed")
            if len(leaf.entries) >= self.layout.leaf_capacity and not allow_split:
                return False
            leaf.entries.insert(pos, entry)
            with self._count_lock:
                self.num_entries += 1
            if len(leaf.entries) <= self.layout.leaf_capacity:
                self.layout.write(leaf, page.data)
                page.mark_dirty()
                return None
            mid = len(leaf.entries) // 2
            right = LeafNode(leaf.entries[mid:], leaf.next_leaf)
            right_id = self._new_node(right)
            leaf.entries = leaf.entries[:mid]
            leaf.next_leaf = right_id
            self.layout.write(leaf, page.data)
            page.mark_dirty()
            return leaf, right.entries[0], right_id

    def _insert_optimistic(self, entry: bytes, key: Any, rid: RID) -> bool:
        """Shared latches to the leaf, exclusive on the leaf; ``False`` if the
        leaf is full and the insert must be redone pessimistically."""
        with self._root_latch.shared():
            page_id = self.root_page_id
            levels = self.height
            latch = self._latches.get(page_id)
            exclusive = levels == 1
            latch.acquire(exclusive)
        try:
            for depth in range(1, levels):
                node = self._read_node(page_id)
                child_id = node.children[bisect_right(node.keys, entry)]
                child_latch = self._latches.get(child_id)
                child_exclusive = depth == levels - 1
                child_latch.acquire(child_exclusive)
                latch.release(exclusive)
                page_id, latch, exclusive = child_id, child_latch, child_exclusive
            done = self._leaf_insert(page_id, entry, key, rid, allow_split=False) is None
        finally:
            latch.release(exclusive)
        if done:
            with self._count_lock:
                self.optimistic_inserts += 1
        return done

    def _is_safe(self, node: Node) -> bool:
        """True if inserting one entry below ``node`` cannot split it."""
        if node.is_leaf:
            return len(node.entries) < self.layout.leaf_capacity
        return len(node.keys) < self.layout.internal_capacity

    def _insert_pessimistic(self, entry: bytes, key: Any, rid: RID) -> None:
        """Exclusive crabbing from the root, keeping only unsafe ancestors."""
        held: list[RWLatch] = []
        root_held = True
        self._root_latch.acquire_exclusive()
        try:
            page_id = self.root_page_id
            path: list[tuple[int, InternalNode, int]] = []
            while True:
                latch = self._latches.get(page_id)
                latch.acquire_exclusive()
                node = self._read_node(page_id)
                if self._is_safe(node):
                    for ancestor in held:
                        ancestor.release_exclusive()
                    held.clear()
                    path.clear()
                    if root_held:
                        self._root_latch.release_exclusive()
                        root_held = False
                held.append(latch)
                if node.is_leaf:
                    break
                idx = bisect_right(node.keys, entry)
                path.append((page_id, node, idx))
                page_id = node.children[idx]

            split = self._leaf_insert(page_id, entry, key, rid, allow_split=True)
            if split is not None:
                _, sep, right_id = split
                # Every node left in ``path`` is latched exclusively, and the
                # root pointer is still held if the split can reach the root.
                self._insert_into_parent(path, sep, right_id)
            with self._count_lock:
                self.pessimistic_inserts += 1
        finally:
            for latch in held:
                latch.release_exclusive()
            if root_held:
                self._root_latch.release_exclusive()
