"""Disk-resident B+ tree index built on the page-aware storage layer.

Each node is one ``PAGE_SIZE`` page accessed through the buffer pool, so
every lookup, scan and update is charged real page I/O. Keys map to RIDs;
duplicate keys are supported. :class:`BPlusTree` serializes operations
with one tree-wide latch; :class:`CrabbingBPlusTree` uses per-node latch
crabbing so searches and inserts run concurrently.
"""

from .crabbing import CrabbingBPlusTree
from .keys import FloatKey, IntKey, KeyCodec, StringKey, TimestampKey
from .latch import LatchTable, RWLatch
from .node import LeafNode, InternalNode, NodeLayout
from .tree import BPlusTree, DuplicateEntryError, TreeStats

__all__ = [
    "BPlusTree",
    "CrabbingBPlusTree",
    "DuplicateEntryError",
    "FloatKey",
    "IntKey",
    "InternalNode",
    "KeyCodec",
    "LatchTable",
    "LeafNode",
    "NodeLayout",
    "RWLatch",
    "StringKey",
    "TimestampKey",
    "TreeStats",
]
