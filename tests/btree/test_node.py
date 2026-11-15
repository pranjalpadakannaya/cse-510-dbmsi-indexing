import unittest

from dbmsi.btree.keys import IntKey
from dbmsi.btree.node import (
    INTERNAL,
    LEAF,
    InternalNode,
    LeafNode,
    NodeLayout,
    max_internal_keys,
    max_leaf_entries,
)
from dbmsi.storage import INVALID_PAGE_ID, PAGE_SIZE, RID


def entries(codec, keys):
    return [codec.encode_entry(k, RID(k, 0)) for k in keys]


class TestCapacities(unittest.TestCase):
    def test_full_nodes_fit_in_a_page(self):
        es = IntKey().entry_size  # 14 bytes
        self.assertEqual(max_leaf_entries(es), (PAGE_SIZE - 8) // 14)
        k = max_internal_keys(es)
        self.assertLessEqual(8 + 4 * (k + 1) + es * k, PAGE_SIZE)
        self.assertGreater(8 + 4 * (k + 2) + es * (k + 1), PAGE_SIZE)

    def test_capacity_bounds(self):
        es = IntKey().entry_size
        with self.assertRaises(ValueError):
            NodeLayout(es, leaf_capacity=2)
        with self.assertRaises(ValueError):
            NodeLayout(es, internal_capacity=max_internal_keys(es) + 1)
        layout = NodeLayout(es, leaf_capacity=5, internal_capacity=7)
        self.assertEqual((layout.min_leaf_entries, layout.min_internal_keys), (2, 3))


class TestRoundTrip(unittest.TestCase):
    def setUp(self):
        self.codec = IntKey()
        self.layout = NodeLayout(self.codec.entry_size)

    def test_leaf(self):
        node = LeafNode(entries(self.codec, range(self.layout.leaf_capacity)), 42)
        data = bytearray(PAGE_SIZE)
        self.layout.write(node, data)
        self.assertEqual(NodeLayout.node_type(data), LEAF)
        self.assertEqual(self.layout.read(data), node)
        self.assertEqual(self.layout.read_next_leaf(data), 42)
        self.layout.set_next_leaf(data, INVALID_PAGE_ID)
        self.assertEqual(self.layout.read(data).entries, node.entries)
        self.assertEqual(self.layout.read(data).next_leaf, INVALID_PAGE_ID)

    def test_internal(self):
        n = self.layout.internal_capacity
        node = InternalNode(entries(self.codec, range(n)), list(range(100, 100 + n + 1)))
        data = bytearray(PAGE_SIZE)
        self.layout.write(node, data)
        self.assertEqual(NodeLayout.node_type(data), INTERNAL)
        self.assertEqual(self.layout.read(data), node)

    def test_shrinking_node_rewrites_cleanly(self):
        data = bytearray(PAGE_SIZE)
        self.layout.write(LeafNode(entries(self.codec, range(10))), data)
        self.layout.write(LeafNode(entries(self.codec, [3])), data)
        self.assertEqual(self.layout.read(data).entries, entries(self.codec, [3]))

    def test_rejects_invalid_nodes(self):
        data = bytearray(PAGE_SIZE)
        small = NodeLayout(self.codec.entry_size, leaf_capacity=3, internal_capacity=3)
        with self.assertRaises(ValueError):
            small.write(LeafNode(entries(self.codec, range(4))), data)
        with self.assertRaises(ValueError):
            small.write(InternalNode(entries(self.codec, [1]), [1]), data)
        with self.assertRaises(ValueError):
            small.read(bytearray(PAGE_SIZE))  # zeroed page is not a node
        small.write(InternalNode(entries(self.codec, [1]), [1, 2]), data)
        with self.assertRaises(ValueError):
            small.set_next_leaf(data, 3)


if __name__ == "__main__":
    unittest.main()
