"""On-page layout of B+ tree nodes. One node occupies exactly one page.

All entries are fixed-width (see :mod:`dbmsi.btree.keys`), so nodes use a
simple packed-array layout instead of a slotted page::

    header (8 B): node_type (uint8) | pad (uint8) | num_keys (uint16)
                  | next_leaf (int32)

    leaf     : header | entry[0] ... entry[n-1]
    internal : header | child[0] ... child[cap] (int32 each) | key[0] ... key[n-1]

A leaf holds sorted ``(key, rid)`` entries and links to its right sibling
via ``next_leaf``, forming the chain used by range scans. An internal node
with ``n`` separator keys has ``n + 1`` children; child ``i`` holds entries
``< key[i]`` and child ``i + 1`` holds entries ``>= key[i]``.

Nodes are decoded into small Python objects, modified, and encoded back into
the page buffer, which keeps the split/merge logic independent of the byte
layout.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Optional, Union

from dbmsi.storage import INVALID_PAGE_ID, PAGE_SIZE

LEAF = 1
INTERNAL = 2

_HEADER = struct.Struct("<BxHi")
HEADER_SIZE = _HEADER.size
_CHILD = struct.Struct("<i")
CHILD_SIZE = _CHILD.size


@dataclass
class LeafNode:
    entries: list[bytes] = field(default_factory=list)
    next_leaf: int = INVALID_PAGE_ID

    is_leaf = True


@dataclass
class InternalNode:
    keys: list[bytes] = field(default_factory=list)
    children: list[int] = field(default_factory=list)

    is_leaf = False


Node = Union[LeafNode, InternalNode]


def max_leaf_entries(entry_size: int) -> int:
    """Most entries a leaf page can hold."""
    return (PAGE_SIZE - HEADER_SIZE) // entry_size


def max_internal_keys(entry_size: int) -> int:
    """Most separator keys an internal page can hold (children = keys + 1)."""
    return (PAGE_SIZE - HEADER_SIZE - CHILD_SIZE) // (entry_size + CHILD_SIZE)


class NodeLayout:
    """Encodes and decodes nodes whose entries are ``entry_size`` bytes.

    ``leaf_capacity`` / ``internal_capacity`` may be set below the physical
    maximum (tests use tiny fan-outs to build deep trees from little data).
    """

    def __init__(
        self,
        entry_size: int,
        leaf_capacity: Optional[int] = None,
        internal_capacity: Optional[int] = None,
    ):
        self.entry_size = entry_size
        max_leaf = max_leaf_entries(entry_size)
        max_internal = max_internal_keys(entry_size)
        self.leaf_capacity = max_leaf if leaf_capacity is None else leaf_capacity
        self.internal_capacity = (
            max_internal if internal_capacity is None else internal_capacity
        )
        if not 3 <= self.leaf_capacity <= max_leaf:
            raise ValueError(f"leaf_capacity must be in [3, {max_leaf}]")
        if not 3 <= self.internal_capacity <= max_internal:
            raise ValueError(f"internal_capacity must be in [3, {max_internal}]")
        # Children live in a fixed region sized for a full node, so keys
        # always start at the same offset regardless of the current count.
        self._keys_offset = HEADER_SIZE + CHILD_SIZE * (self.internal_capacity + 1)

    @property
    def min_leaf_entries(self) -> int:
        return self.leaf_capacity // 2

    @property
    def min_internal_keys(self) -> int:
        return self.internal_capacity // 2

    # -- decoding -----------------------------------------------------------

    @staticmethod
    def node_type(data: bytearray) -> int:
        return data[0]

    def read(self, data: bytearray) -> Node:
        node_type, n, next_leaf = _HEADER.unpack_from(data, 0)
        es = self.entry_size
        if node_type == LEAF:
            base = HEADER_SIZE
            entries = [bytes(data[base + i * es : base + (i + 1) * es]) for i in range(n)]
            return LeafNode(entries, next_leaf)
        if node_type == INTERNAL:
            children = [
                _CHILD.unpack_from(data, HEADER_SIZE + i * CHILD_SIZE)[0]
                for i in range(n + 1)
            ]
            base = self._keys_offset
            keys = [bytes(data[base + i * es : base + (i + 1) * es]) for i in range(n)]
            return InternalNode(keys, children)
        raise ValueError(f"page is not a B+ tree node (type byte {node_type})")

    def read_next_leaf(self, data: bytearray) -> int:
        return _HEADER.unpack_from(data, 0)[2]

    # -- encoding -----------------------------------------------------------

    def write(self, node: Node, data: bytearray) -> None:
        es = self.entry_size
        if node.is_leaf:
            n = len(node.entries)
            if n > self.leaf_capacity:
                raise ValueError(f"leaf overflow: {n} > {self.leaf_capacity}")
            _HEADER.pack_into(data, 0, LEAF, n, node.next_leaf)
            base = HEADER_SIZE
            if n:
                data[base : base + n * es] = b"".join(node.entries)
            return
        n = len(node.keys)
        if n > self.internal_capacity:
            raise ValueError(f"internal overflow: {n} > {self.internal_capacity}")
        if len(node.children) != n + 1:
            raise ValueError("internal node needs exactly len(keys) + 1 children")
        _HEADER.pack_into(data, 0, INTERNAL, n, INVALID_PAGE_ID)
        for i, child in enumerate(node.children):
            _CHILD.pack_into(data, HEADER_SIZE + i * CHILD_SIZE, child)
        base = self._keys_offset
        if n:
            data[base : base + n * es] = b"".join(node.keys)

    def set_next_leaf(self, data: bytearray, next_leaf: int) -> None:
        node_type, n, _ = _HEADER.unpack_from(data, 0)
        if node_type != LEAF:
            raise ValueError("next_leaf is only defined for leaf nodes")
        _HEADER.pack_into(data, 0, node_type, n, next_leaf)
