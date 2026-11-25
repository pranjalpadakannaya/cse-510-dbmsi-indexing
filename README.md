# cse-510-dbmsi-indexing
A repository for CSE 510's Course Project

## Benchmarking Index Structures for File Metadata Search

A page-aware storage kernel and indexing benchmark framework built to evaluate physical I/O costs, latency, storage overhead, and concurrency across B+ Tree, Compressed Bitmap, and Inverted Index structures on file system metadata.

## Final Report Work — Storage & B+ Tree

**Owner: Pranjal Padakannaya**

### Completed
- [x] 4 KB page storage, LRU buffer pool, and heap file
- [x] B+ tree with point/range queries, bulk load, and delete
- [x] Metadata extraction and workload generation
- [x] Latch crabbing and RQ4 concurrency experiment
- [x] B+ tree selectivity benchmark (RQ1)

### Remaining before final report
- [ ] Integrate measured sequential/random I/O costs with the shared benchmark
- [ ] Rerun RQ1 using the finalized real-I/O measurement
- [ ] Replace the assumed 1x/4x/10x RQ1 comparison with measured results
- [ ] Update the final RQ1 graph/results for the report
- [ ] Verify final B+ tree/RQ4 results and document tradeoffs

## Requirements
Python 3.10+. The storage layer uses only the standard library; matplotlib
is optional and only draws the benchmark figures.

For a reproducible environment, use Nix. `flake.lock` pins nixpkgs, so
everyone gets the same Python 3.12 and matplotlib:
```sh
nix develop                 # shell with python3 + matplotlib, PYTHONHASHSEED=0
nix-shell                   # the same, without flakes (reads flake.lock)
nix flake check             # run the test suite in the Nix sandbox
nix build                   # the dbmsi package
```
`nix flake update` moves the pin; commit the new `flake.lock` with it.

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
  cold-cache reset for benchmarks, and thread-safe access. A missing page is
  read from disk without holding the pool lock, so reads of different pages
  by different threads overlap; a second thread asking for a page that is
  being read waits for that read instead of issuing another.
- **Modeled read latency:** `DiskManager(read_latency=seconds)` makes every
  physical page read wait that long, outside every lock, to model a storage
  device for the concurrency experiments. It is 0 by default and does not
  change any I/O count. The benchmarks set it from the measured random read
  time (see [Measured I/O cost](#measured-io-cost-calibration)).
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
- **Concurrency:** `BPlusTree` has one tree-wide latch, so it can be shared
  between threads, but operations on the same tree run one at a time.
  `CrabbingBPlusTree` (same format and interface) uses latch crabbing so
  searches and inserts run concurrently; see below.

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

### Latch crabbing (`CrabbingBPlusTree`)
Every node page has a reader-writer latch (`dbmsi/btree/latch.py`); waiting
writers block new readers, so inserts are not starved.
- **Search:** latch the root shared, then latch the child and release the
  parent at each level, so a search holds at most two latches. Range scans
  copy one leaf at a time and move right along the leaf chain.
- **Insert, optimistic first:** shared latches down to the leaf and an
  exclusive latch on the leaf only. If the leaf has room, the entry goes in
  and no other operation was blocked.
- **Insert, pessimistic fallback:** if the leaf is full, the insert restarts
  with exclusive latches from the root down, releasing all ancestors as soon
  as a node is *safe* (it has room for one more key, so a split below it
  stops there). Only the part of the path the split can reach stays latched.
- **Root:** a separate latch protects the root pointer and height. A
  pessimistic insert keeps it until the root is known to be safe, so a root
  split is never seen half done.
- **No deadlocks:** latches are taken top-down and, along the leaf chain,
  left to right, and never re-entered. Deletes can merge a node into its
  *left* sibling, which breaks that order, so deletes, bulk loads and
  `stats`/`validate`/`flush` take a tree-wide latch exclusively; searches
  and inserts hold it shared and never block each other on it.
- `optimistic_inserts` and `pessimistic_inserts` count which path each
  insert took.

`tests/btree/test_crabbing.py` runs the whole single-threaded B+ tree test
suite against `CrabbingBPlusTree`, plus concurrent stress tests (inserts
with evictions and read latency, searches and range scans during inserts,
deletes during inserts) that check the tree invariants and that no latch or
pin is left held.

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

## Benchmark harness (`dbmsi.bench`)
**Results and findings for all four research questions are in
[`results/README.md`](results/README.md).**

Runs one query workload against every access method over the same heap file
and buffer pool, checks every answer against the sequential scan, and writes
the measurements as CSV, JSON, a Markdown report and figures.

**Access methods**
- `seqscan`: reads every heap page and tests each record. It is the
  correctness baseline and always runs first.
- `btree`: one B+ tree per queried attribute, bulk loaded. Answers point and
  range queries.
- `bitmap-plain`, `bitmap-wah`, `bitmap-roaring`: one bitmap index over the
  queried categorical attributes. Answers point queries, and ranges on
  low-cardinality attributes such as `depth` by OR-ing the bitmaps of the
  values in range.
- `inverted`: name-term `term`, `and` and `or` queries. Postings come from
  `InvertedIndex` and are compressed with its delta + variable-byte
  encoding, then packed back to back into a postings file of 4 KB pages read
  through the buffer pool. The term dictionary is kept in memory, like the
  bitmap catalog. This layer lives in `dbmsi/bench/methods.py` until the
  inverted index stores its own postings on disk.
- `inverted-raw`: the same, with uncompressed postings (sorted 8-byte RIDs),
  the baseline for the compression trade-off. Delta + Gamma is not
  implemented in `dbmsi.inverted_index`, so it is not compared.

Each method implements `AccessMethod` (`supports`, `build`, `query`,
`insert`, `size_pages`, `estimate_reads`), so adding a structure means adding
one class.

**What is measured**
- **Queries:** each query runs once from a cold cache for its page I/O:
  physical page reads (sequential and random, split into index reads and
  heap fetch reads), buffer requests and hits, and the
  `sequential + k × random` I/O cost for every `k` in `random_io_weights`
  (by default the `k` measured on this machine, see
  [Measured I/O cost](#measured-io-cost-calibration)).
  Then, with the pool kept warm, `warmup` untimed runs and up to `repeat`
  timed runs give the median and 95th percentile latency. With
  `fetch_records` each index also reads its matching records from the heap,
  in RID order, as a bitmap heap scan does. All I/O numbers are `IOStats`
  deltas from the storage layer.
- **Analytical estimates:** every method predicts its cold-cache page reads
  from its structure and the match count (`estimate_reads`): the scan reads
  every heap page; the B+ tree reads `height + (k - 1) / entries per leaf`
  pages; the bitmap reads each value's bitmap pages plus the RID directory
  pages holding a match (Yao's formula); the inverted index reads the pages
  its posting lists overlap. With `fetch_records`, Yao's formula over the
  heap is added. `estimates.csv` and the report compare the totals per
  method and spec against the 5% target, and `estimates.png` plots them.
- **Decoding:** the inverted index times its posting-list decoding
  (`decode_ms`), to set CPU cost against the bytes compression saves.
- **Concurrency (W7):** with `throughput_threads`, a fresh B+ tree is bulk
  loaded into its own cold buffer pool for every latching scheme, read
  latency and thread count, and a fixed mix of point searches and inserts
  is split across that many threads (`throughput.csv`, `throughput.png`).
  `global` is `BPlusTree` with its tree-wide latch; `crabbing` is
  `CrabbingBPlusTree`. Python's GIL serializes the threads' CPU work, so
  with pages in memory (`0` ms) neither scheme can beat one thread; the
  modeled read latency (`throughput_read_latency_ms`) is a wait that
  releases the GIL, as a disk read would, and shows whether the latching
  lets threads overlap their page reads. Every insert is checked afterwards
  (`lost_inserts` must be 0).
- **Correctness:** every result must equal the scan's RID set, and the scan
  must match the workload's `expected_count`. After the inserts, every query
  is checked again.
- **Build and storage:** build time, pages written, and size in pages and
  payload bytes.
- **Updates:** time and page reads and writes per insert of `num_updates`
  extra rows. The heap insert is reported as the `seqscan` row.

### Measured I/O cost (calibration)

The I/O cost of a query is `sequential reads + k × random reads`, where `k`
says how much slower a random page read is than a sequential one.

**Old approach (assumed).** The first benchmark runs did not measure `k`.
They assumed `k` = 1, 4 and 10 and reported the cost for each, and the
concurrency benchmark assumed a read latency of 0.1 to 2 ms per page read
(`throughput_read_latency_ms`). These numbers were guesses: 4 and 10 are
typical textbook values for spinning disks, and nothing checked them against
the machine the benchmark ran on. They are still available:
`--set 'random_io_weights=[1,4,10]'` (set the weights explicitly and they win
over the calibration) and `--set 'throughput_read_latency_ms=[0,0.5,1,2]'`.
`benchmarks/cardinality.json`, `scale.json` and `smoke.json` still use them.

**Current approach (measured).** `python3 -m dbmsi.bench calibrate` times
real 4 KB page reads (`dbmsi/bench/calibrate.py`):
- it writes a 512 MB file of random bytes, and reads it with the operating
  system's page cache switched off (`F_NOCACHE` on macOS; on Linux
  `posix_fadvise(DONTNEED)`), so reads hit the device and not memory;
- **sequential:** 20,000 reads of consecutive pages, which is what
  `DiskManager` counts as a sequential read;
- **random:** 20,000 reads of uniformly random pages;
- `k` = mean random read time ÷ mean sequential read time (floored at 1,
  since a random read cannot really be cheaper), and the mean random read
  time is used as the modeled read latency.

The result is saved in `benchmarks/calibration.json` (with the machine and
date), and `default.json` and `concurrency.json` point to it with
`"io_calibration"`. On the test machine (Apple silicon Mac, SSD):

| | mean | median |
|---|---|---|
| sequential read | 29 µs | 24 µs |
| random read | 90 µs | 84 µs |
| **k** | **3.08** | |

Two runs gave k = 3.08 and 3.16. Some things to keep in mind:
- `k` and the latency belong to one machine and one disk. Run `calibrate` on
  the machine used for the benchmark, and commit the file with the results.
  An SSD gives a small `k`; a spinning disk would give a much larger one.
- The page cache must be bypassed. In an early version of the script the
  freshly written file was still cached, sequential reads came back at 0.6 µs
  and `k` was 78, which was a measurement error.
- Only page reads are measured. The benchmark still counts reads of the
  in-memory page store, so latency columns are Python time, not disk time;
  the measured numbers feed the cost model and the modeled read latency.

### Running
```sh
python3 -m dbmsi.bench run benchmarks/smoke.json        # ~10 s, every method
python3 -m dbmsi.bench run benchmarks/default.json      # 100k files, the report numbers
python3 -m dbmsi.bench run benchmarks/default.json --set num_files=1000000 --set name=1m
python3 -m dbmsi.bench report results/default           # redraw tables and figures
python3 -m dbmsi.bench calibrate                        # re-measure k, writes benchmarks/calibration.json

# One config at several values of one parameter, in parallel processes
python3 -m dbmsi.bench sweep benchmarks/scale.json --param num_files \
    --values 10000 100000 1000000 --jobs 3 -o results/scale
python3 -m dbmsi.bench sweep benchmarks/cardinality.json --param generator.num_users \
    --values 10 100 1000 5000 --jobs 4 -o results/cardinality
```
Results go to `results/<name>/`: `results.json` (config,
environment and git commit included), `queries.csv` (one row per query and
method), `summary.csv` (means per spec, selectivity and method), `build.csv`,
`updates.csv`, `estimates.csv`, `throughput.csv`, `report.md`, and
`page_reads.png`, `latency.png`, `storage.png` and `estimates.png` when
matplotlib is installed. The command exits with 2 if any result disagrees
with the scan.

A sweep writes each run to `<out>/<param>=<value>/` as above, then
`sweep.csv`, `sweep_build.csv`, `sweep_estimates.csv`, `sweep.json`,
`sweep.md` and `sweep_{storage,page_reads,latency}.png` across the values.
`--param` takes dotted keys to reach into `generator`.
`benchmarks/scale.json` is the 10K to 1M scaling run and
`benchmarks/cardinality.json` compares the bitmap encodings as the number of
distinct uids grows.

### Configuration
A config is a JSON object; any field can also be set with `--set key=value`
(values are parsed as JSON).

| key | default | meaning |
|---|---|---|
| `num_files`, `seed`, `generator` | 10000, 0, `{}` | synthetic dataset; `generator` overrides `GeneratorConfig` fields such as `term_skew` |
| `records` | none | JSON Lines dataset to use instead; its last `num_updates` rows are the inserts |
| `specs`, `selectivities`, `per_level`, `query_seed` | see `harness.py` | generated workload |
| `queries` | none | JSON Lines workload to use instead |
| `methods` | seqscan, btree, bitmap-roaring, inverted | must include `seqscan` |
| `btree_attributes`, `bitmap_attributes` | queried attributes | what to index |
| `pool_size` | 256 | buffer pool frames (4 KB each) |
| `db_path` | none | database file; in memory by default, with identical I/O counts |
| `repeat`, `warmup` | 50, 1 | timed and untimed warm runs per query, after the cold run |
| `min_repeat`, `max_repeat_seconds` | 5, 2.0 | stop the timed runs early once this time is spent, but never before `min_repeat` runs |
| `fetch_records` | false | also read each matching record from the heap |
| `io_calibration` | none | calibration file from `calibrate`; sets `random_io_weights` and `throughput_read_latency_ms` from the measurement unless the config sets them itself. `default.json` and `concurrency.json` use `benchmarks/calibration.json` |
| `random_io_weights` | [1, 4, 10] | the `k` values of the I/O cost (the old assumed values; replaced by the measured `k` when `io_calibration` is set) |
| `num_updates`, `verify_after_updates` | 100, true | insert phase |
| `throughput_threads` | [] | thread counts for the concurrent B+ tree run; empty skips it |
| `throughput_ops`, `throughput_insert_fraction`, `throughput_attribute` | 20000, 0.2, `size` | its operation mix and the indexed attribute |
| `throughput_latching` | `["global", "crabbing"]` | B+ tree latching schemes to compare |
| `throughput_read_latency_ms`, `throughput_pool_size` | `[0]`, `pool_size` | modeled wait per physical page read (the measured random read time when `io_calibration` is set), and the pool the throughput trees use |

The same config always produces the same dataset, workload and I/O counts;
only latencies vary between runs. Latency measures this Python
implementation on in-memory pages, so compare methods on page reads first.
