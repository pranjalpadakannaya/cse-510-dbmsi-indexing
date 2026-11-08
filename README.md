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

## Storage layer and LRU buffer pool (`dbmsi.storage`)
This is the storage kernel that every other component sits on. All tables
and index nodes live in fixed 4 KB pages, records are addressed by
`(page_id, slot_id)` tuple identifiers instead of memory pointers, and every
page access goes through an LRU buffer pool. Because of that, page I/O is
counted the same way for every structure, and those counts are the main cost
metric for all experiments.

```
  HeapFile / B+ tree / bitmap / inverted index
                    │  fetch / create / unpin
                    ▼
          BufferPoolManager  ── LRUReplacer
                    │  read_page / write_page
                    ▼
              DiskManager  (file or in-memory)
                    │
                 IOStats  (shared counters)
```

### Components

| Component | Module | Role |
|---|---|---|
| `PAGE_SIZE`, `INVALID_PAGE_ID` | `constants.py` | 4096-byte page size; `-1` sentinel for "no page". |
| `DiskManager` | `disk_manager.py` | Physical page I/O against a database file, or in memory for tests. |
| `Page` | `page.py` | A buffer frame: `page_id`, 4 KB `data`, `pin_count`, `is_dirty`. |
| `LRUReplacer` | `lru_replacer.py` | Picks the least recently used unpinned frame to evict. |
| `BufferPoolManager` | `buffer_pool.py` | Fixed-size pool with pin/unpin, LRU eviction, and dirty write-back. |
| `SlottedPage` | `slotted_page.py` | Variable-length record layout inside one page. |
| `RID` | `rid.py` | `(page_id, slot_id)` tuple identifier; packs to 6 bytes. |
| `HeapFile` | `heap_file.py` | Table storage as a chain of slotted pages. |
| `IOStats`, `measure` | `stats.py` | Shared I/O counters and per-query measurement. |

### Disk manager
- Reads and writes whole 4 KB pages, either in a single database file or in
  memory. Both backends count I/O the same way.
- `allocate_page()` hands out page ids densely and reuses ids freed by
  `deallocate_page()` first. Allocation does no I/O: a new page reads back as
  zeros until it is first written.
- Each read is labeled **sequential** if it targets the page right after the
  previously read page, and **random** otherwise (the first read counts as
  random). This feeds the `sequential + k × random` cost model.
- Reopening an existing file keeps its pages. The list of freed page ids is
  kept only in memory, so freed pages are not reused after a reopen.

### LRU buffer pool
- Holds `pool_size` frames. `fetch_page(page_id)` returns the page **pinned**:
  a hit if it is already resident, otherwise a miss that costs one physical
  read.
- `unpin_page(page_id, is_dirty)` releases one pin. A page can be pinned
  several times and is evictable only when its pin count reaches 0.
- **Eviction policy:** the frame that has been unpinned the longest goes
  first. Re-pinning a frame removes it from the eviction candidates. If every
  frame is pinned, the pool raises `BufferPoolFullError`.
- A dirty victim is written back before its frame is reused; a clean victim
  costs no write.
- `new_page()` allocates a zeroed page and marks it dirty, so it reaches disk
  on flush or eviction.
- `flush_page` / `flush_all` write dirty pages; `delete_page` drops a page
  and frees its id on disk.
- `clear()` flushes and evicts every unpinned page so each benchmark run
  starts with a cold cache.
- `fetch(page_id)` and `create()` are context managers that unpin
  automatically.
- One re-entrant lock guards the pool, so it is safe to share between
  threads. This is the "global lock" baseline; per-page latches for latch
  crabbing are left to the B+ tree concurrency extension.

### Slotted pages
Page layout (little-endian):

```
+-------------------------------------------------------------------+
| header (8 B) | slot 0 | slot 1 | ... -->    free    <-- records    |
+-------------------------------------------------------------------+
header : next_page_id (int32) | num_slots (uint16) | free_end (uint16)
slot   : offset (uint16) | length (uint16)                 4 B each
```

- The slot directory grows forward from the header; record bytes grow
  backward from the end of the page.
- Maximum record size is 4084 bytes (`MAX_RECORD_SIZE`).
- **Slot ids are stable.** Deleting a record empties its slot, and
  compaction moves record bytes without renumbering slots, so a RID stays
  valid for the life of its record. Empty slots are reused by later inserts.
- Inserts compact the page automatically when there is enough total free
  space but not enough in one contiguous block.
- `update` rewrites in place when the record shrinks, and relocates it within
  the same page when it grows. It raises `PageFullError` without changing the
  old record if the page has no room.

### Heap file
- A table is a linked chain of slotted pages, identified by its first page
  id; pass that id back to the constructor to reopen it.
- `insert`, `get`, `update`, and `delete` work by RID. `update` never changes
  the RID.
- Inserts append to the last page, so a bulk load lays pages out
  contiguously and a cold `scan()` is all sequential reads except the first.
- `scan()` pins each page only while copying its records, so an abandoned
  scan never leaves a page pinned.
- Records are opaque bytes; turning metadata rows into bytes is up to the
  caller.

### I/O accounting
One `IOStats` object is shared by the disk manager and the buffer pool
(`bpm.stats`).

| Counter | Recorded by | Meaning |
|---|---|---|
| `page_reads`, `page_writes` | disk manager | Physical page I/O |
| `sequential_reads`, `random_reads` | disk manager | Read classification |
| `pages_allocated`, `pages_deallocated` | disk manager | Page id allocation |
| `page_requests` | buffer pool | Logical page fetches |
| `buffer_hits`, `buffer_misses` | buffer pool | Cache behavior (`hit_rate`) |
| `evictions`, `dirty_writebacks` | buffer pool | Frame replacement |

`measure(stats)` captures counter changes inside a `with` block, and
`io_cost(k)` returns `sequential_reads + k × random_reads`.

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

Rough numbers: inserting 100K 80-byte records takes about 1 s; a cold scan
of the resulting 2,084 pages takes 0.04 s with 2,084 page reads
(`io_cost(k=4)` = 2,087).

### Tests
`tests/storage/` has 51 unit tests covering:
- disk manager round trips, page id reuse, bounds checks, and
  sequential/random labeling, on both the file and in-memory backends;
- slotted page inserts, deletes, slot reuse, compaction, updates, and a
  randomized comparison against a dictionary model;
- LRU victim order, hit/miss counts, pinning, dirty write-back, cold-cache
  `clear()`, page deletion, file persistence, and concurrent access from
  several threads;
- heap file multi-page inserts, scans, reopening, two tables sharing one
  pool, and a randomized comparison against a dictionary model.

### Rules for index implementers
- Get pages only through `bpm.fetch(page_id)` / `bpm.create()` (or the
  `fetch_page`/`new_page` + `unpin_page` pair). Never call `DiskManager`
  directly, or the I/O will not show up as buffer-pool activity.
- Call `page.mark_dirty()` after modifying `page.data`.
- Do not keep references to `page.data` after unpinning; the frame is reused.
- One node = one page. Lay out your own node format on `page.data`, or use
  `SlottedPage` for variable-length entries.
- Store `RID`s (`RID.pack()` is 6 bytes) rather than Python references.
