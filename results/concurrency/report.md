# Benchmark: concurrency

- Dataset: 1,000,000 rows (synthetic, seed 0), 25,109 heap pages
- Workload: 1 queries (query seed 0)
- Buffer pool: 256 pages. Page I/O is counted on one run per query from a cold cache; latency is the median and 95th percentile of up to 1 warm runs after 1 warm-up run(s) (at least 1, fewer than 1 once 2 s is spent)
- Index results exclude fetching the matching records from the heap
- I/O cost = sequential reads + k x random reads, for k in 1, 4, 10
- Run at 2026-11-16T07:41:11+00:00 on commit e08b05d, Python 3.13.9

## Correctness

2 of 2 query results match the sequential scan (and the workload's expected counts).

## Build and storage

| method | build time (s) | pages written | size (pages) | size |
|---|---|---|---|---|
| seqscan | 11.32 | 25,109 | 25,109 | 98.08 MB |
| btree | 0.59 | 3,444 | 3,443 | 13.45 MB |

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
| global | 0 | 1 | 4,237 | 1.00x | 23,864 | 0 |
| global | 0 | 2 | 3,911 | 0.92x | 23,788 | 0 |
| global | 0 | 4 | 3,655 | 0.86x | 23,868 | 0 |
| global | 0 | 8 | 3,385 | 0.80x | 23,627 | 0 |
| global | 0 | 16 | 3,350 | 0.79x | 23,908 | 0 |
| global | 0 | 32 | 3,291 | 0.78x | 23,429 | 0 |
| crabbing | 0 | 1 | 3,888 | 1.00x | 23,864 | 0 |
| crabbing | 0 | 2 | 3,637 | 0.94x | 23,864 | 0 |
| crabbing | 0 | 4 | 3,559 | 0.92x | 23,887 | 0 |
| crabbing | 0 | 8 | 3,415 | 0.88x | 23,720 | 0 |
| crabbing | 0 | 16 | 3,420 | 0.88x | 23,826 | 0 |
| crabbing | 0 | 32 | 3,193 | 0.82x | 23,808 | 0 |
| global | 0.10 | 1 | 2,515 | 1.00x | 23,864 | 0 |
| global | 0.10 | 2 | 2,313 | 0.92x | 24,095 | 0 |
| global | 0.10 | 4 | 2,245 | 0.89x | 23,814 | 0 |
| global | 0.10 | 8 | 2,222 | 0.88x | 23,570 | 0 |
| global | 0.10 | 16 | 2,171 | 0.86x | 23,551 | 0 |
| global | 0.10 | 32 | 2,158 | 0.86x | 23,269 | 0 |
| crabbing | 0.10 | 1 | 2,380 | 1.00x | 23,864 | 0 |
| crabbing | 0.10 | 2 | 3,235 | 1.36x | 23,665 | 0 |
| crabbing | 0.10 | 4 | 3,555 | 1.49x | 23,637 | 0 |
| crabbing | 0.10 | 8 | 3,357 | 1.41x | 23,058 | 0 |
| crabbing | 0.10 | 16 | 3,197 | 1.34x | 22,920 | 0 |
| crabbing | 0.10 | 32 | 3,165 | 1.33x | 22,734 | 0 |
| global | 0.50 | 1 | 669 | 1.00x | 23,864 | 0 |
| global | 0.50 | 2 | 727 | 1.09x | 23,836 | 0 |
| global | 0.50 | 4 | 577 | 0.86x | 23,653 | 0 |
| global | 0.50 | 8 | 595 | 0.89x | 23,260 | 0 |
| global | 0.50 | 16 | 582 | 0.87x | 23,524 | 0 |
| global | 0.50 | 32 | 573 | 0.86x | 23,334 | 0 |
| crabbing | 0.50 | 1 | 746 | 1.00x | 23,864 | 0 |
| crabbing | 0.50 | 2 | 1,509 | 2.02x | 23,721 | 0 |
| crabbing | 0.50 | 4 | 2,569 | 3.44x | 23,549 | 0 |
| crabbing | 0.50 | 8 | 3,263 | 4.37x | 23,280 | 0 |
| crabbing | 0.50 | 16 | 3,201 | 4.29x | 23,235 | 0 |
| crabbing | 0.50 | 32 | 3,143 | 4.21x | 22,830 | 0 |
| global | 1.00 | 1 | 448 | 1.00x | 23,864 | 0 |
| global | 1.00 | 2 | 403 | 0.90x | 23,942 | 0 |
| global | 1.00 | 4 | 373 | 0.83x | 24,166 | 0 |
| global | 1.00 | 8 | 376 | 0.84x | 23,391 | 0 |
| global | 1.00 | 16 | 376 | 0.84x | 23,268 | 0 |
| global | 1.00 | 32 | 381 | 0.85x | 23,410 | 0 |
| crabbing | 1.00 | 1 | 445 | 1.00x | 23,864 | 0 |
| crabbing | 1.00 | 2 | 824 | 1.85x | 23,733 | 0 |
| crabbing | 1.00 | 4 | 1,539 | 3.46x | 23,530 | 0 |
| crabbing | 1.00 | 8 | 2,554 | 5.73x | 23,571 | 0 |
| crabbing | 1.00 | 16 | 3,143 | 7.06x | 23,040 | 0 |
| crabbing | 1.00 | 32 | 3,037 | 6.82x | 23,061 | 0 |
| global | 2.00 | 1 | 257 | 1.00x | 23,864 | 0 |
| global | 2.00 | 2 | 248 | 0.96x | 23,800 | 0 |
| global | 2.00 | 4 | 237 | 0.92x | 24,108 | 0 |
| global | 2.00 | 8 | 234 | 0.91x | 23,514 | 0 |
| global | 2.00 | 16 | 232 | 0.90x | 23,702 | 0 |
| global | 2.00 | 32 | 233 | 0.90x | 23,530 | 0 |
| crabbing | 2.00 | 1 | 255 | 1.00x | 23,864 | 0 |
| crabbing | 2.00 | 2 | 483 | 1.89x | 23,389 | 0 |
| crabbing | 2.00 | 4 | 834 | 3.27x | 23,761 | 0 |
| crabbing | 2.00 | 8 | 1,293 | 5.06x | 23,424 | 0 |
| crabbing | 2.00 | 16 | 2,061 | 8.07x | 23,238 | 0 |
| crabbing | 2.00 | 32 | 2,807 | 10.99x | 22,895 | 0 |

Crabbing against the global latch at the same latency and thread count:

| read latency (ms) | threads | global ops / s | crabbing ops / s | crabbing / global |
|---|---|---|---|---|
| 0 | 1 | 4,237 | 3,888 | 0.92x |
| 0 | 2 | 3,911 | 3,637 | 0.93x |
| 0 | 4 | 3,655 | 3,559 | 0.97x |
| 0 | 8 | 3,385 | 3,415 | 1.01x |
| 0 | 16 | 3,350 | 3,420 | 1.02x |
| 0 | 32 | 3,291 | 3,193 | 0.97x |
| 0.10 | 1 | 2,515 | 2,380 | 0.95x |
| 0.10 | 2 | 2,313 | 3,235 | 1.40x |
| 0.10 | 4 | 2,245 | 3,555 | 1.58x |
| 0.10 | 8 | 2,222 | 3,357 | 1.51x |
| 0.10 | 16 | 2,171 | 3,197 | 1.47x |
| 0.10 | 32 | 2,158 | 3,165 | 1.47x |
| 0.50 | 1 | 669 | 746 | 1.12x |
| 0.50 | 2 | 727 | 1,509 | 2.08x |
| 0.50 | 4 | 577 | 2,569 | 4.45x |
| 0.50 | 8 | 595 | 3,263 | 5.48x |
| 0.50 | 16 | 582 | 3,201 | 5.50x |
| 0.50 | 32 | 573 | 3,143 | 5.48x |
| 1.00 | 1 | 448 | 445 | 0.99x |
| 1.00 | 2 | 403 | 824 | 2.04x |
| 1.00 | 4 | 373 | 1,539 | 4.12x |
| 1.00 | 8 | 376 | 2,554 | 6.79x |
| 1.00 | 16 | 376 | 3,143 | 8.36x |
| 1.00 | 32 | 381 | 3,037 | 7.97x |
| 2.00 | 1 | 257 | 255 | 0.99x |
| 2.00 | 2 | 248 | 483 | 1.95x |
| 2.00 | 4 | 237 | 834 | 3.53x |
| 2.00 | 8 | 234 | 1,293 | 5.52x |
| 2.00 | 16 | 232 | 2,061 | 8.90x |
| 2.00 | 32 | 233 | 2,807 | 12.07x |

## Queries

Mean physical page reads per query, cold cache (median and 95th percentile warm latency in ms in parentheses). A dash means the method does not support the query. Full I/O costs for each k are in `summary.csv`.

### `point:size`

| target sel. | actual sel. | seqscan | btree |
|---|---|---|---|
| 0.0001 | 0.0001 | 25,109.0 (1,156.26 / 1,156.26) | 3.0 (0.13 / 0.13) |
