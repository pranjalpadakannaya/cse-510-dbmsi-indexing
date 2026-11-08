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
