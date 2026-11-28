# Benchmark: scale-num_files=1000000

- Dataset: 1,000,000 rows (synthetic, seed 0), 25,109 heap pages
- Workload: 75 queries (query seed 0)
- Buffer pool: 256 pages. Page I/O is counted on one run per query from a cold cache; latency is the median and 95th percentile of up to 50 warm runs after 1 warm-up run(s) (at least 3, fewer than 50 once 1 s is spent)
- Index results include fetching the matching records from the heap (in RID order)
- I/O cost = sequential reads + k x random reads, for k in 1, 4, 10
- Run at 2026-11-16T07:13:54+00:00 on commit e08b05d, Python 3.13.9

## Correctness

210 of 210 query results match the sequential scan (and the workload's expected counts).
After 200 inserts, 135 results were checked again: 0 mismatches.

## Build and storage

| method | build time (s) | pages written | size (pages) | size |
|---|---|---|---|---|
| seqscan | 18.46 | 25,109 | 25,109 | 98.08 MB |
| btree | 3.10 | 8,888 | 8,886 | 34.71 MB |
| bitmap-wah | 2.00 | 2,186 | 2,184 | 8.47 MB |
| bitmap-roaring | 2.02 | 1,933 | 1,931 | 7.47 MB |
| inverted | 3.88 | 2,022 | 2,022 | 7.90 MB |
| inverted-raw | 2.84 | 6,225 | 6,225 | 24.31 MB |

## Inserts (200 rows)

The seqscan row is the heap insert alone; every index pays it too.

| method | ms / insert | page reads / insert | page writes / insert |
|---|---|---|---|
| seqscan | 0.012 | 0.005 | 0.03 |
| btree | 0.172 | 1.30 | 2.38 |
| bitmap-wah | 2.143 | 16.43 | 16.43 |
| bitmap-roaring | 0.882 | 9.21 | 9.21 |
| inverted | 143.209 | 44.37 | 48.73 |
| inverted-raw | 108.299 | 240.88 | 240.46 |

## Measured versus estimated page I/O

Each method's analytical model of its cold-cache page reads (see `dbmsi/bench/methods.py`) against the measured count. *error* compares the totals over all queries of a spec; *within 5%* is the share of single queries within 5% of their estimate. Over all queries: 3,513,341 measured, 3,514,632 estimated (-0.0%).

| method | spec | queries | measured | estimated | error | within 5% |
|---|---|---|---|---|---|---|
| seqscan | and | 15 | 376,635 | 376,635.0 | +0.0% | 100% |
| seqscan | point:extension | 15 | 376,635 | 376,635.0 | +0.0% | 100% |
| seqscan | point:size | 15 | 376,635 | 376,635.0 | +0.0% | 100% |
| seqscan | range:size | 15 | 376,635 | 376,635.0 | +0.0% | 100% |
| seqscan | term | 15 | 376,635 | 376,635.0 | +0.0% | 100% |
| btree | point:extension | 15 | 232,444 | 232,653.0 | -0.1% | 100% |
| btree | point:size | 15 | 125,112 | 125,352.5 | -0.2% | 100% |
| btree | range:size | 15 | 184,102 | 184,122.2 | -0.0% | 100% |
| bitmap-wah | point:extension | 15 | 251,148 | 251,355.2 | -0.1% | 100% |
| bitmap-roaring | point:extension | 15 | 251,052 | 251,259.2 | -0.1% | 100% |
| inverted | and | 15 | 111,592 | 111,747.2 | -0.1% | 100% |
| inverted | term | 15 | 178,520 | 178,607.2 | -0.0% | 100% |
| inverted-raw | and | 15 | 116,190 | 116,264.6 | -0.1% | 100% |
| inverted-raw | term | 15 | 180,006 | 180,095.9 | -0.0% | 100% |

## Concurrent searches and inserts (B+ tree)

20,000 operations (4,005 inserts) on a `size` tree, split across the threads, from a cold pool of 256 frames. `global` takes one tree-wide latch per operation; `crabbing` latches one root-to-leaf path. *read latency* is the modeled wait per physical page read; *speedup* is against the fewest threads of the same scheme and latency.

| latching | read latency (ms) | threads | ops / s | speedup | page reads | lost inserts |
|---|---|---|---|---|---|---|
| global | 0 | 1 | 3,942 | 1.00x | 23,864 | 0 |
| global | 0 | 2 | 3,719 | 0.94x | 24,298 | 0 |
| global | 0 | 4 | 3,516 | 0.89x | 23,738 | 0 |
| global | 0 | 8 | 3,371 | 0.86x | 23,607 | 0 |
| crabbing | 0 | 1 | 3,914 | 1.00x | 23,864 | 0 |
| crabbing | 0 | 2 | 3,672 | 0.94x | 23,983 | 0 |
| crabbing | 0 | 4 | 3,615 | 0.92x | 23,923 | 0 |
| crabbing | 0 | 8 | 3,381 | 0.86x | 23,974 | 0 |

Crabbing against the global latch at the same latency and thread count:

| read latency (ms) | threads | global ops / s | crabbing ops / s | crabbing / global |
|---|---|---|---|---|
| 0 | 1 | 3,942 | 3,914 | 0.99x |
| 0 | 2 | 3,719 | 3,672 | 0.99x |
| 0 | 4 | 3,516 | 3,615 | 1.03x |
| 0 | 8 | 3,371 | 3,381 | 1.00x |

## Posting list decoding

Median time spent decoding posting lists per query (warm runs), against the whole query.

| spec | target sel. | method | decode (ms) | query (ms) | decode share |
|---|---|---|---|---|---|
| and | 0.0001 | inverted | 17.121 | 18.434 | 93% |
| and | 0.0001 | inverted-raw | 12.993 | 14.318 | 91% |
| and | 0.001 | inverted | 39.571 | 47.056 | 84% |
| and | 0.001 | inverted-raw | 30.924 | 38.877 | 80% |
| and | 0.0100 | inverted | 139.201 | 207.966 | 67% |
| and | 0.0100 | inverted-raw | 114.544 | 192.440 | 60% |
| and | 0.1000 | inverted | 149.465 | 245.780 | 61% |
| and | 0.1000 | inverted-raw | 126.051 | 228.544 | 55% |
| and | 0.5000 | inverted | 230.128 | 326.906 | 70% |
| and | 0.5000 | inverted-raw | 125.734 | 224.605 | 56% |
| term | 0.0001 | inverted | 0.065 | 0.331 | 20% |
| term | 0.0001 | inverted-raw | 0.042 | 0.289 | 15% |
| term | 0.001 | inverted | 0.509 | 5.206 | 10% |
| term | 0.001 | inverted-raw | 0.348 | 5.155 | 7% |
| term | 0.0100 | inverted | 5.576 | 52.114 | 11% |
| term | 0.0100 | inverted-raw | 4.304 | 51.340 | 8% |
| term | 0.1000 | inverted | 46.017 | 311.771 | 15% |
| term | 0.1000 | inverted-raw | 37.206 | 302.827 | 12% |
| term | 0.5000 | inverted | 99.771 | 625.920 | 16% |
| term | 0.5000 | inverted-raw | 84.445 | 616.313 | 14% |

## Queries

Mean physical page reads per query, cold cache (median and 95th percentile warm latency in ms in parentheses). A dash means the method does not support the query. Full I/O costs for each k are in `summary.csv`.

### `and`

| target sel. | actual sel. | seqscan | inverted | inverted-raw |
|---|---|---|---|---|
| 0.0001 | 0.0001 | 25,109.0 (2,097.91 / 2,130.26) | 123.7 (18.43 / 20.66) | 171.3 (14.32 / 15.10) |
| 0.001 | 0.0010 | 25,109.0 (2,127.04 / 2,176.89) | 1,066.3 (47.06 / 206.33) | 1,175.3 (38.88 / 41.31) |
| 0.010 | 0.0113 | 25,109.0 (2,195.59 / 2,203.31) | 9,217.7 (207.97 / 378.06) | 9,641.3 (192.44 / 366.73) |
| 0.100 | 0.0191 | 25,109.0 (2,159.65 / 2,215.40) | 13,542.7 (245.78 / 401.36) | 14,022.0 (228.54 / 385.79) |
| 0.500 | 0.0185 | 25,109.0 (2,144.43 / 2,169.44) | 13,247.0 (326.91 / 416.96) | 13,720.0 (224.60 / 391.61) |

### `point:extension`

| target sel. | actual sel. | seqscan | btree | bitmap-wah | bitmap-roaring |
|---|---|---|---|---|---|
| 0.0001 | 0.0111 | 25,109.0 (1,827.18 / 1,835.54) | 9,089.7 (59.37 / 60.27) | 10,508.7 (65.65 / 68.12) | 10,498.7 (62.49 / 64.16) |
| 0.001 | 0.0113 | 25,109.0 (1,831.06 / 1,838.15) | 9,179.3 (59.40 / 60.51) | 10,598.0 (65.22 / 68.42) | 10,588.0 (63.23 / 64.12) |
| 0.010 | 0.0111 | 25,109.0 (1,700.45 / 1,706.88) | 9,037.7 (53.53 / 55.91) | 10,457.3 (59.45 / 62.65) | 10,447.3 (56.38 / 59.64) |
| 0.100 | 0.0977 | 25,109.0 (1,674.17 / 1,682.49) | 25,200.7 (354.17 / 499.04) | 26,169.0 (336.09 / 349.50) | 26,168.0 (333.00 / 496.65) |
| 0.500 | 0.0901 | 25,109.0 (1,670.63 / 1,694.27) | 24,974.0 (316.52 / 326.26) | 25,983.0 (307.40 / 464.27) | 25,982.0 (303.99 / 307.16) |

### `point:size`

| target sel. | actual sel. | seqscan | btree |
|---|---|---|---|
| 0.0001 | 0.0001 | 25,109.0 (2,083.71 / 2,088.88) | 90.0 (0.37 / 0.43) |
| 0.001 | 0.0001 | 25,109.0 (1,519.78 / 1,537.02) | 97.0 (0.40 / 0.46) |
| 0.010 | 0.0198 | 25,109.0 (1,415.99 / 1,421.75) | 13,839.0 (96.15 / 98.90) |
| 0.100 | 0.0198 | 25,109.0 (1,405.80 / 1,422.34) | 13,839.0 (95.38 / 98.84) |
| 0.500 | 0.0198 | 25,109.0 (1,409.70 / 1,426.29) | 13,839.0 (97.22 / 100.08) |

### `range:size`

| target sel. | actual sel. | seqscan | btree |
|---|---|---|---|
| 0.0001 | 0.0001 | 25,109.0 (1,300.60 / 1,324.17) | 108.0 (0.34 / 0.37) |
| 0.001 | 0.0010 | 25,109.0 (1,309.00 / 1,321.08) | 1,024.0 (5.11 / 5.70) |
| 0.010 | 0.0100 | 25,109.0 (1,294.62 / 1,314.35) | 8,329.3 (49.46 / 54.54) |
| 0.100 | 0.1000 | 25,109.0 (1,312.49 / 1,337.75) | 25,081.3 (361.47 / 500.09) |
| 0.500 | 0.5000 | 25,109.0 (1,430.07 / 1,616.38) | 26,824.7 (1,922.50 / 1,945.26) |

### `term`

| target sel. | actual sel. | seqscan | inverted | inverted-raw |
|---|---|---|---|---|
| 0.0001 | 0.0001 | 25,109.0 (2,085.48 / 2,111.17) | 110.7 (0.33 / 0.34) | 111.0 (0.29 / 0.35) |
| 0.001 | 0.0011 | 25,109.0 (2,122.07 / 2,132.21) | 1,045.7 (5.21 / 5.74) | 1,046.3 (5.15 / 5.99) |
| 0.010 | 0.0104 | 25,109.0 (2,119.40 / 2,120.80) | 8,552.7 (52.11 / 55.61) | 8,566.0 (51.34 / 53.86) |
| 0.100 | 0.0948 | 25,109.0 (2,134.50 / 2,146.80) | 24,630.7 (311.77 / 353.98) | 24,780.7 (302.83 / 353.57) |
| 0.500 | 0.2009 | 25,109.0 (2,168.47 / 2,297.67) | 25,167.0 (625.92 / 814.15) | 25,498.0 (616.31 / 842.94) |
