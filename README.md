# cse-510-dbmsi-indexing
A repository for CSE 510's Course Project

## Benchmarking Index Structures for File Metadata Search

A page-aware storage kernel and indexing benchmark framework built to evaluate physical I/O costs, latency, storage overhead, and concurrency across B+ Tree, Compressed Bitmap, and Inverted Index structures on file system metadata.

## Requirements
Python 3.10+. The storage layer uses only the standard library.

## Running tests
```sh
python3 -m unittest discover -s tests -t .
```

## Storage layer and LRU buffer pool (`dbmsi.storage`)
The storage kernel that every table and index is built on. All data lives in
fixed 4 KB pages, and every page access goes through an LRU buffer pool, so
page I/O is counted the same way for every index structure.

**Features**
- **Disk manager:** reads and writes 4 KB pages to a database file (or to
  memory for tests), allocates and frees page ids, and labels each read as
  sequential or random.
- **LRU buffer pool:** a fixed number of frames with pin/unpin, eviction of
  the least recently used unpinned page, write-back of dirty pages, a
  cold-cache reset for benchmarks, and thread-safe access.
- **Slotted pages:** variable-length records inside a page, with
  automatic compaction and record ids that stay valid after deletes.
- **Record ids (RIDs):** records are addressed by `(page_id, slot_id)`
  instead of memory pointers.
- **Heap file:** table storage as a chain of pages, with insert, get, update
  and delete by RID and full sequential scans.
- **I/O accounting:** shared counters for page reads and writes (sequential
  and random), buffer hits, misses and evictions, per-query measurement, and
  the `sequential + k × random` I/O cost model.

### Example
```python
from dbmsi.storage import BufferPoolManager, DiskManager, HeapFile, measure

bpm = BufferPoolManager(DiskManager("data.db"), pool_size=256)
table = HeapFile(bpm)
rid = table.insert(b"serialized record")

bpm.clear()                             # cold cache before measuring
with measure(bpm.stats) as m:
    rows = list(table.scan())
print(m.delta.page_reads, m.delta.io_cost(k=4))
```

### Rules for index implementers
- Access pages only through the buffer pool (`bpm.fetch` / `bpm.create`) so
  the I/O is counted.
- Call `page.mark_dirty()` after modifying a page, and don't keep
  references to page data after unpinning.
- One index node = one page; store `RID`s, not Python references.

## B+ Tree index (`dbmsi.btree`)
A disk-resident B+ tree that maps file metadata keys (size, timestamps,
names, extensions) to the RIDs of their records. Every node is one 4 KB page
read and written through the buffer pool, so lookups, scans and updates are
charged real page I/O.

**Features**
- **Keys:** `IntKey()` for signed 64-bit integers and `StringKey(max_len)` for
  UTF-8 strings. Keys are encoded so that byte order matches key order.
- **Duplicate keys:** entries are `(key, RID)` pairs, so many files can share
  a key. Inserting the same exact pair twice raises `DuplicateEntryError`.
- **Point and range queries:** `search(key)` returns every matching RID.
  `range_scan(low, high, include_low, include_high)` walks the leaf chain in
  key order, and either bound can be left open. A point lookup reads one page
  per level.
- **Insert and delete:** full nodes split, and the splits can grow a new root.
  Underfull nodes borrow from a sibling or merge with it, the root shrinks
  when it is left with one child, and freed pages go back to the disk manager.
- **Bulk loading:** `bulk_load(pairs, fill_factor)` builds the tree from the
  bottom up. Leaves are written in order on new pages, so a full scan reads
  them mostly sequentially.
- **Persistence:** a meta page stores the root, height, key type and node
  capacities. To reopen a tree, pass its `meta_page_id`.
- **Introspection:** `stats()` reports the height and the number of leaf and
  internal pages, and `validate()` checks every tree invariant (for tests).
- **Concurrency:** each tree has one latch, so it can be shared between
  threads, but operations on the same tree run one at a time.

### Example
```python
from dbmsi.btree import BPlusTree, IntKey
from dbmsi.storage import measure

by_size = BPlusTree(bpm, IntKey())
by_size.bulk_load((size, rid) for rid, size in files)   # or insert() one by one

bpm.clear()
with measure(bpm.stats) as m:
    hits = list(by_size.range_scan(1 << 20, 10 << 20))  # files of 1-10 MB
print(len(hits), m.delta.page_reads, by_size.height)

by_size.flush()                                          # persist entry count
reopened = BPlusTree(bpm, meta_page_id=by_size.meta_page_id)
```

### Node layout
Entries have a fixed width (encoded key + 6-byte RID). With `IntKey`, a leaf
holds 292 entries and an internal node holds 226 keys, so about 66,000
entries fit in a tree of height 2 and about 15 million in a tree of height 3.
For tests, pass `leaf_capacity` and `internal_capacity` to build deep trees
from small data sets.
