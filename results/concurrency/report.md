# Benchmark: concurrency

- Dataset: 1,000,000 rows (synthetic, seed 0), 25,109 heap pages
- Workload: 1 queries (query seed 0)
- Buffer pool: 256 pages. Page I/O is counted on one run per query from a cold cache; latency is the median and 95th percentile of up to 1 warm runs after 1 warm-up run(s) (at least 1, fewer than 1 once 2 s is spent)
- Index results exclude fetching the matching records from the heap
- I/O cost = sequential reads + k x random reads, for k in 3.08 (measured: a random read takes 90 us and a sequential read 29 us on macOS-26.6.2-arm64-arm-64bit-Mach-O)
- Run at 2026-11-25T06:19:55+00:00 on commit 90d7512, Python 3.15.2

## Correctness

2 of 2 query results match the sequential scan (and the workload's expected counts).

## Build and storage

| method | build time (s) | pages written | size (pages) | size |
|---|---|---|---|---|
| seqscan | 10.58 | 25,109 | 25,109 | 98.08 MB |
| btree | 0.65 | 3,444 | 3,443 | 13.45 MB |

## Measured versus estimated page I/O

Each method's analytical model of its cold-cache page reads (see `dbmsi/bench/methods.py`) against the measured count. *error* compares the totals over all queries of a spec; *within 5%* is the share of single queries within 5% of their estimate. Over all queries: 25,112 measured, 25,112 estimated (-0.0%).

| method | spec | queries | measured | estimated | error | within 5% |
|---|---|---|---|---|---|---|
| seqscan | point:size | 1 | 25,109 | 25,109.0 | +0.0% | 100% |
| btree | point:size | 1 | 3 | 3.3 | -8.9% | 0% |

## Concurrent searches and inserts (B+ tree)

20,000 operations (4,005 inserts) on a `size` tree, split across the threads, from a cold pool of 256 frames. `global` takes one tree-wide latch per operation; `crabbing` latches one root-to-leaf path. *read latency* is the modeled wait per physical page read; *speedup* is against the fewest threads of the same scheme and latency.

| latching | read latency (ms) | threads | ops / s | speedup | page reads | lost inserts |
|---|---|---|---|---|---|---|
| global | 0 | 1 | 4,183 | 1.00x | 23,864 | 0 |
| global | 0 | 2 | 3,896 | 0.93x | 23,830 | 0 |
| global | 0 | 4 | 3,889 | 0.93x | 24,202 | 0 |
| global | 0 | 8 | 3,670 | 0.88x | 23,800 | 0 |
| global | 0 | 16 | 3,304 | 0.79x | 23,877 | 0 |
| global | 0 | 32 | 3,260 | 0.78x | 23,295 | 0 |
| crabbing | 0 | 1 | 3,975 | 1.00x | 23,864 | 0 |
| crabbing | 0 | 2 | 3,831 | 0.96x | 24,047 | 0 |
| crabbing | 0 | 4 | 3,810 | 0.96x | 23,960 | 0 |
| crabbing | 0 | 8 | 3,802 | 0.96x | 23,656 | 0 |
| crabbing | 0 | 16 | 3,793 | 0.95x | 23,997 | 0 |
| crabbing | 0 | 32 | 3,783 | 0.95x | 24,139 | 0 |
| global | 0.09 | 1 | 2,643 | 1.00x | 23,864 | 0 |
| global | 0.09 | 2 | 2,614 | 0.99x | 23,427 | 0 |
| global | 0.09 | 4 | 2,536 | 0.96x | 23,603 | 0 |
| global | 0.09 | 8 | 2,379 | 0.90x | 23,893 | 0 |
| global | 0.09 | 16 | 2,321 | 0.88x | 23,775 | 0 |
| global | 0.09 | 32 | 2,274 | 0.86x | 23,243 | 0 |
| crabbing | 0.09 | 1 | 2,523 | 1.00x | 23,864 | 0 |
| crabbing | 0.09 | 2 | 3,473 | 1.38x | 23,666 | 0 |
| crabbing | 0.09 | 4 | 3,689 | 1.46x | 23,403 | 0 |
| crabbing | 0.09 | 8 | 3,556 | 1.41x | 23,287 | 0 |
| crabbing | 0.09 | 16 | 3,308 | 1.31x | 23,105 | 0 |
| crabbing | 0.09 | 32 | 3,157 | 1.25x | 22,698 | 0 |

Crabbing against the global latch at the same latency and thread count:

| read latency (ms) | threads | global ops / s | crabbing ops / s | crabbing / global |
|---|---|---|---|---|
| 0 | 1 | 4,183 | 3,975 | 0.95x |
| 0 | 2 | 3,896 | 3,831 | 0.98x |
| 0 | 4 | 3,889 | 3,810 | 0.98x |
| 0 | 8 | 3,670 | 3,802 | 1.04x |
| 0 | 16 | 3,304 | 3,793 | 1.15x |
| 0 | 32 | 3,260 | 3,783 | 1.16x |
| 0.09 | 1 | 2,643 | 2,523 | 0.95x |
| 0.09 | 2 | 2,614 | 3,473 | 1.33x |
| 0.09 | 4 | 2,536 | 3,689 | 1.45x |
| 0.09 | 8 | 2,379 | 3,556 | 1.49x |
| 0.09 | 16 | 2,321 | 3,308 | 1.42x |
| 0.09 | 32 | 2,274 | 3,157 | 1.39x |

## Queries

Mean physical page reads per query, cold cache (median and 95th percentile warm latency in ms in parentheses). A dash means the method does not support the query. Full I/O costs for each k are in `summary.csv`.

### `point:size`

| target sel. | actual sel. | seqscan | btree |
|---|---|---|---|
| 0.0001 | 0.0001 | 25,109.0 (1,115.94 / 1,115.94) | 3.0 (0.13 / 0.13) |
