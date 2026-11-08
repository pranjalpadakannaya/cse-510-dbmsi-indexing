# cse-510-dbmsi-indexing
A repository for CSE 510's Course Project: benchmarking B+ tree, bitmap, and
inverted indexes for file metadata search on a simulated page-based storage
layer.

## Requirements
Python 3.10+. The storage layer uses only the standard library.

## Running tests
```sh
python3 -m unittest discover -s tests -t .
```

## Storage layer (`dbmsi.storage`)
All tables and index nodes live in fixed 4 KB pages. Every page access goes
through the buffer pool, so page I/O is counted the same way for every
structure.

| Component | Module | Role |
|---|---|---|
| `DiskManager` | `disk_manager.py` | Physical page reads/writes against a file (or in memory). Counts reads/writes and classifies each read as sequential or random. |
| `BufferPoolManager` | `buffer_pool.py` | Fixed number of frames, LRU eviction, pin/unpin, dirty write-back. Counts requests, hits, misses, evictions. |
| `SlottedPage` | `slotted_page.py` | Variable-length records in a page; stable slot ids. |
| `RID` | `rid.py` | `(page_id, slot_id)` tuple identifier stored by indexes. |
| `HeapFile` | `heap_file.py` | Table storage as a chain of slotted pages; insert/get/update/delete by RID and full scans. |
| `IOStats`, `measure` | `stats.py` | Shared counters plus a context manager for per-query deltas and `io_cost(k)` = sequential + k * random reads. |

### Example
```python
from dbmsi.storage import BufferPoolManager, DiskManager, HeapFile, measure

disk = DiskManager("data.db")          # or DiskManager() for in-memory
bpm = BufferPoolManager(disk, pool_size=256)

table = HeapFile(bpm)
rid = table.insert(b"serialized record")

bpm.clear()                             # cold cache before measuring
with measure(bpm.stats) as m:
    rows = list(table.scan())
print(m.delta.page_reads, m.delta.io_cost(k=4))
```

### Rules for index implementers
- Get pages only via `bpm.fetch(page_id)` / `bpm.create()` (or the
  `fetch_page`/`new_page` + `unpin_page` pair). Never touch `DiskManager`
  directly, or the I/O will not show up as buffer-pool activity.
- Call `page.mark_dirty()` after modifying `page.data`.
- Do not keep references to `page.data` after unpinning; the frame is reused.
- One node = one page. Lay out your own node format on `page.data`, or use
  `SlottedPage` for variable-length entries.
- Store `RID`s (`RID.pack()` is 6 bytes) rather than Python references.
