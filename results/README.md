# Benchmark results and findings

Measured results for the four research questions. Every run in this folder
checks every index result against the sequential scan: **all results match,
in every run, before and after inserts.** Page I/O is counted by the storage
layer on a cold 256-page (1 MB) buffer pool; I/O cost is
`sequential reads + k × random reads` for k = 1, 4, 10.

| folder | what it is | config |
|---|---|---|
| `1m/` | every method and query type, 1,000,000 files | `benchmarks/default.json --set num_files=1000000 --set name=1m` |
| `default/` | the same at 100,000 files | `benchmarks/default.json` |
| `scale/` | 10K → 100K → 1M files (`sweep.md`) | `sweep benchmarks/scale.json --param num_files` |
| `cardinality/` | bitmap encodings vs. number of distinct uids (`sweep.md`) | `sweep benchmarks/cardinality.json --param generator.num_users` |
| `concurrency/` | B+ tree latching, 1M files, 0–2 ms modeled read latency, 1–32 threads | `benchmarks/concurrency.json` |
| `smoke/` | 3,000-file sanity run | `benchmarks/smoke.json` |

Each run folder has `report.md` (all tables), CSVs, `results.json` (config,
environment, git commit) and figures. Runs used Python 3.13.9 on an Apple
silicon Mac, from commit `e08b05d`. Page I/O is deterministic for a given
config; latencies vary between machines and runs.

## Correctness and the cost model (success criteria)

- `1m/`: 625 of 625 query results match the scan; after 200 inserts, 425
  results re-checked, 0 mismatches. Same in `default/`, `scale/`,
  `cardinality/`.
- **Analytical page-read estimates:** at 1M rows the total is 10,735,311
  measured vs 10,733,545 estimated (+0.0%). Every method/spec total is within
  0.2%, and **100% of single queries are within 5%** of their estimate. In
  the scale sweep every method is within 1.1% at every size
  (`scale/sweep.md`, `1m/estimates.png`).

## RQ1: when does an index beat the scan? (`1m/`, `scale/`)

At 1M rows the scan always reads 25,109 heap pages. A B+ tree range query on
`mtime` (matches scattered across the heap, so each fetch is a random read):

| selectivity | B+ tree page reads | cost k=4 | cost k=10 | scan |
|---|---|---|---|---|
| 0.01% | 103 | 408 | 1,017 | 25,109 |
| 0.1% | 989 | 3,837 | 9,533 | 25,109 |
| 1% | 8,326 | 24,974 | 58,270 | 25,109 |
| 10% | 25,065 | 26,225 | 28,546 | 25,109 |

- **Crossover:** by raw page reads the index stops winning at about **10%**
  selectivity; when a random read costs 4× a sequential one, at about **1%**;
  at 10×, at about **0.4%** (between 0.1% and 1%).
- Point lookups are cheap: a `size` point query reads about 90 pages at 1M
  rows, vs 12 at 100K and 3 at 10K (`scale/sweep.md`): the cost grows with
  the matches, not the table.
- Bitmaps on `extension`/`category` read ~15% more pages than the B+ tree at
  1% selectivity (bitmap pages plus the RID directory), and both pass the
  scan's page count at ~10% selectivity, the same crossover.
- Wall-clock latency is lower for indexes even near the crossover (e.g. 316
  ms vs 1,205 ms at 10%) because the scan decodes and tests every record in
  Python; compare on page I/O first.

## RQ2: bitmap encodings vs. cardinality (`cardinality/`, 100K rows)

| distinct uids | 10 | 100 | 1,000 | 5,000 |
|---|---|---|---|---|
| uncompressed | 0.71 MB | 1.77 MB | 11.53 MB | 24.50 MB |
| WAH | 0.70 MB | 0.81 MB | 0.85 MB | 0.87 MB |
| Roaring | 0.68 MB | 0.71 MB | 0.73 MB | 0.74 MB |

- Uncompressed size grows linearly with cardinality (one full-length bitmap
  per value); **WAH and Roaring stay almost flat** (28× and 33× smaller at
  5,000 values). Roaring is the smallest at every cardinality.
- Page reads per query are nearly identical across encodings (they are
  dominated by RID-directory pages); at 0.01% selectivity and 1,000+
  distinct values, WAH and Roaring read about 20% fewer pages than
  uncompressed.
- At 1M rows (`1m/`) on `extension`/`category`: uncompressed 10.4 MB, WAH
  9.6 MB, Roaring 8.3 MB.
- Inserts: Roaring touches the fewest pages per insert at 1M (24 vs 37
  uncompressed, 33 WAH).

## RQ3: posting-list compression (`1m/`, `default/`)

| at 1M rows | delta + variable-byte | uncompressed |
|---|---|---|
| postings size | 7.90 MB | 24.31 MB (**−67%**) |
| index pages read, `term` at 1% / 10% | 7.4 / 37.4 | 20.6 / 193.8 |
| index pages read, `and` at 1% | 85 | 480 |
| decode time, `term` at 10% | 46 ms | 38 ms (+22%) |

- Compression saves two thirds of the storage and reads **3–6× fewer index
  pages**, for about 20–30% more decode CPU.
- Once matching records are fetched, heap reads dominate (e.g. 8,240 total
  reads at 1% for both), so the end-to-end difference is small; compression
  matters most for index-only (count / AND) queries.
- Delta + Gamma coding is not implemented, so it is not compared.

## RQ4: B+ tree concurrency, global latch vs. latch crabbing (`concurrency/`)

20,000 operations (20% inserts) on a 1M-entry `size` tree, cold 256-page
pool. **0 lost inserts in every configuration.**

| read latency | threads | global latch (ops/s) | latch crabbing (ops/s) | crabbing / global |
|---|---|---|---|---|
| 0 ms | 1 | 4,237 | 3,888 | 0.92× |
| 0 ms | 16 | 3,350 | 3,420 | 1.02× |
| 0.5 ms | 1 | 669 | 746 | 1.1× |
| 0.5 ms | 8 | 595 | 3,263 | 5.5× |
| 1 ms | 1 | 448 | 445 | 1.0× |
| 1 ms | 16 | 376 | 3,143 | 8.4× |
| 2 ms | 1 | 257 | 255 | 1.0× |
| 2 ms | 32 | 233 | 2,807 | **12.1×** |

![throughput](concurrency/throughput.png)

- **Global latch: adding threads never helps.** Throughput stays flat or
  drops (contention) at every latency, because a thread waiting on a page
  read holds the whole tree.
- **Latch crabbing scales with threads while page reads are the
  bottleneck**: other threads keep working on other root-to-leaf paths
  during a read. Speedup grows with read latency, up to 12× at 2 ms and 32
  threads.
- **It flattens at about 3,200–3,500 ops/s**, the CPU ceiling: Python's GIL
  runs only one thread's CPU work at a time, so once reads are overlapped,
  CPU is the limit. With no read latency (pages in memory) the two schemes
  are within 8%; crabbing pays a little for its extra latching.
- The same pattern holds in `default/` (100K rows: 9.6× at 1 ms, 16 threads)
  and `1m/` (9.0×).
- *Read latency is modeled*: `DiskManager(read_latency=...)` waits that long
  per physical read, outside every lock, as a disk would. Page I/O counts
  are unchanged by it.

## Not covered

- **W4** (`extension AND semester`): the data has no `semester`/`course`
  fields.
- **W6** (BM25 ranking, issue #10) and **Delta + Gamma** posting coding: not
  implemented.
- **10M rows** (issue #11): results stop at 1M. The whole dataset is held in
  Python objects, which would not fit in memory on the 16 GB test machine.
- Latency numbers measure this Python implementation on in-memory pages;
  conclusions are drawn from page I/O, and for RQ4 from relative throughput.
