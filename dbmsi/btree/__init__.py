"""Disk-resident B+ tree index built on the page-aware storage layer.

Each node is one ``PAGE_SIZE`` page accessed through the buffer pool, so
every lookup, scan and update is charged real page I/O. Keys map to RIDs;
duplicate keys are supported.
"""

from .keys import IntKey, KeyCodec, StringKey
from .node import LeafNode, InternalNode, NodeLayout
from .tree import BPlusTree, DuplicateEntryError, TreeStats

__all__ = [
    "BPlusTree",
    "DuplicateEntryError",
    "IntKey",
    "InternalNode",
    "KeyCodec",
    "LeafNode",
    "NodeLayout",
    "StringKey",
    "TreeStats",
]
