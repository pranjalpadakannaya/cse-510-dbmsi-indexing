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
- **Keys:** `IntKey()` for signed 64-bit integers (sizes, inode numbers),
  `FloatKey()` for continuous numeric values, `TimestampKey()` for
  `datetime` values at microsecond precision (mtime, ctime, atime; naive
  datetimes are treated as UTC), and `StringKey(max_len)` for UTF-8 strings.
  Keys are encoded so that byte order matches key order.
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

## Bitmap index (`dbmsi.bitmap`)
A disk-resident bitmap index for categorical attributes (extension, category,
owner, ...). For each distinct value it keeps one bitmap with a bit per row,
and queries combine bitmaps with AND, OR and NOT before any RID is looked up.
Bitmaps live in 4 KB pages behind the buffer pool, so queries are charged
real page I/O.

**Features**
- **Three encodings** that give identical results:
  - `encoding="plain"` stores one bit per row (the uncompressed baseline).
  - `encoding="wah"` uses Word-Aligned Hybrid compression: 31-bit groups in
    32-bit words, where a run of all-zero or all-one groups becomes a single
    fill word.
  - `encoding="roaring"` uses Roaring bitmaps: rows are split into chunks of
    65,536, and each chunk is stored as a sorted array, a bitset, or a list
    of runs, whichever is smallest. Empty chunks take no space.
- **Operations on compressed data:** `&`, `|`, `^` and `~` work on the
  compressed form. WAH handles a fill in one step, so an AND with a sparse
  bitmap skips the other operand instead of walking it. Roaring only looks
  at the chunks both operands have.
- **Several attributes in one index:** attributes share one row numbering, so
  `extension = 'pdf' AND category = 'document'` is a single bitmap AND.
- **Queries:** `search(attr, value)`, `search_and(...)` and `search_or(...)`
  return RIDs in row order. `bitmap(attr, value)` and `any_of(attr, values)`
  return bitmaps to combine freely; `rids(bitmap)` turns the result into
  RIDs and `bitmap.count()` counts matches without reading any RID.
- **Building:** `bulk_load(rows)` writes every bitmap on consecutive pages, so
  reading one is sequential I/O. `insert(rid, values)` appends a row. Rows
  can be dicts, tuples, or records such as `FileMetadata`. Values are `str`
  or `int`. Deletes are not supported.
- **Persistence:** call `flush()`, then reopen with the index's
  `meta_page_id`.
- **Introspection:** `stats()` reports cardinality, bitmap bytes and pages
  per attribute, and RID directory and catalog pages.
- **Concurrency:** each index has one latch, like the B+ tree.

### Example
```python
from dbmsi.bitmap import BitmapIndex
from dbmsi.storage import measure

index = BitmapIndex(bpm, ["extension", "category"], encoding="wah")
index.bulk_load((rid, record) for rid, record in rows)   # record: FileMetadata

bpm.clear()
with measure(bpm.stats) as m:
    hits = index.search_and(("extension", "pdf"), ("category", "document"))
print(len(hits), m.delta.page_reads)

not_code = ~index.bitmap("category", "code") & index.any_of("extension", ["md", "txt"])
print(not_code.count(), index.rids(not_code)[:5])

index.flush()
reopened = BitmapIndex(bpm, meta_page_id=index.meta_page_id)
```

### Storage layout and I/O cost
- **Bitmaps:** each one is a chain of pages holding 4,090 bytes each. An
  uncompressed bitmap over 1M rows is 31 pages; a compressed bitmap is as
  small as one page. Every bitmap takes at least one page, so with thousands
  of distinct values the page count is set by the number of values rather
  than by the compressed size. `stats()` reports both bytes and pages.
- **RID directory:** row numbers map to RIDs through pages of 682 RIDs. A
  query reads only the directory pages that contain a matching row, so an
  equality lookup costs *bitmap pages + matching directory pages*.
- **Catalog:** the list of values and where each bitmap starts is read once
  when the index is opened and kept in memory, so it is not charged to
  queries. A value that does not occur costs no I/O.
- **Inserts:** each insert reads and rewrites the bitmaps of the row's own
  values, so its cost grows with the size of those bitmaps. Use `bulk_load`
  for the initial build.

### Encoding comparison at 1M rows
One attribute with Zipf-distributed values (s = 1). Latency is the median
time to AND a bitmap of that attribute with one from a 20-value attribute
and count the result, from a cold cache.

| Distinct values | Encoding | Bitmap size | Bitmap pages | Rarest value: page reads | Rarest value: latency |
|---|---|---|---|---|---|
| 10 | plain | 1.25 MB | 310 | 62 | 0.9 ms |
| 10 | WAH | 1.24 MB | 309 | 59 | 44 ms |
| 10 | Roaring | 1.08 MB | 268 | 42 | 7 ms |
| 100 | plain | 12.5 MB | 3,100 | 62 | 0.9 ms |
| 100 | WAH | 3.97 MB | 1,020 | 35 | 25 ms |
| 100 | Roaring | 1.68 MB | 464 | 26 | 4 ms |
| 1,000 | plain | 125 MB | 31,000 | 62 | 0.9 ms |
| 1,000 | WAH | 5.54 MB | 1,891 | 32 | 18 ms |
| 1,000 | Roaring | 1.94 MB | 1,251 | 26 | 4 ms |
| 5,000 | plain | 625 MB | 155,000 | 62 | 0.9 ms |
| 5,000 | WAH | 6.16 MB | 5,728 | 32 | 18 ms |
| 5,000 | Roaring | 2.31 MB | 5,199 | 26 | 4 ms |

The uncompressed index grows in proportion to the number of values, while
both compressed encodings stay within a few megabytes, and Roaring is the
smaller of the two throughout. The latency column mostly reflects this
implementation: the uncompressed AND is a single big-integer operation in
C, whereas WAH and Roaring decode their words and containers in Python, so
compare encodings on page reads and size rather than on wall-clock time.

## Metadata extraction and workload generation (`dbmsi.workload`)
Tools for producing the datasets and queries used in the benchmarks. You can
scan a real directory tree, measure its distributions, generate synthetic
datasets of any size that imitate them (or follow your own parameters), and
build query workloads with a chosen selectivity.

**Features**
- **Metadata records:** `FileMetadata` holds a file's path, size, mtime,
  ctime, atime, owner uid and permission bits. The name, extension, category
  (image, code, document, ...), directory depth and name terms are derived
  from these fields. Records pack into bytes for a `HeapFile` and convert to
  dicts for JSON Lines files.
- **Scanner:** `MetadataScanner` walks a tree in sorted order, so the same
  tree always gives the same records and file ids. It does not follow
  symlinks unless asked, never visits a directory twice, and counts
  unreadable entries instead of failing.
- **Profiles:** `MetadataProfile` records the frequencies of extensions,
  owners, modes, depths and terms per name; quantiles of size, mtime, and
  ctime/atime offsets from mtime; name-term document frequencies; and fitted
  Zipf exponents for term and extension skew. Profiles are saved as JSON.
- **Synthetic generator:** `SyntheticGenerator` controls skew with Zipf
  exponents: `term_skew` for name terms (posting-list lengths),
  `extension_skew` for extensions (bitmap cardinalities), `dir_skew` for
  files per directory, and `user_skew` for owners. Sizes, times and depths
  are configurable too. `GeneratorConfig.from_profile(...)` copies a measured
  profile, and any field can be overridden, for example to change only the
  term skew while keeping everything else realistic.
- **Reproducible scaling:** the same seed always gives the same data, and a
  smaller dataset is an exact prefix of a larger one with the same seed.
- **Query workloads:** `QueryGenerator` builds point queries (`extension`,
  `uid`, ...), inclusive range queries (`size`, `mtime`, ...) and AND/OR
  term queries close to a target selectivity. Every query stores its exact
  `expected_count` and a `matches(record)` predicate, which serve as the
  correctness oracle for the index implementations.

### Example
```python
from dbmsi.workload import (
    GeneratorConfig, MetadataProfile, QueryGenerator, SyntheticGenerator, scan_directory,
)

profile = MetadataProfile.from_records(scan_directory("/path/to/tree"))
config = GeneratorConfig.from_profile(profile, num_files=1_000_000, term_skew=1.2)
records = SyntheticGenerator(config).generate()

queries = QueryGenerator(records, seed=0).sweep("range:size", [0.001, 0.01, 0.1], per_level=20)
print(queries[0].low, queries[0].high, queries[0].expected_count)
```

### Command line
```sh
python3 -m dbmsi.workload scan ~/Documents -o real.jsonl -p profile.json
python3 -m dbmsi.workload profile real.jsonl            # print a summary
python3 -m dbmsi.workload generate -n 1000000 --profile profile.json --term-skew 1.2 -o synth.jsonl
python3 -m dbmsi.workload queries synth.jsonl -o queries.jsonl \
    --spec range:size range:mtime point:extension term and \
    --selectivity 0.001 0.01 0.1 --per-level 20
```
Query specs are `point:<attr>`, `range:<attr>`, `term`, `and[:k]` and
`or[:k]`. Scans of real directories can contain private file names, so keep
`real.jsonl` and `profile.json` out of the repository.
