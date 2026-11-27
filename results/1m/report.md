# Benchmark: 1m

- Dataset: 1,000,000 rows (synthetic, seed 0), 25,109 heap pages
- Workload: 200 queries (query seed 0)
- Buffer pool: 256 pages. Page I/O is counted on one run per query from a cold cache; latency is the median and 95th percentile of up to 50 warm runs after 1 warm-up run(s) (at least 5, fewer than 50 once 2 s is spent)
- Index results include fetching the matching records from the heap (in RID order)
- I/O cost = sequential reads + k x random reads, for k in 3.08 (measured: a random read takes 90 us and a sequential read 29 us on macOS-26.6.2-arm64-arm-64bit-Mach-O)
- Run at 2026-11-25T06:16:06+00:00 on commit 90d7512, Python 3.15.2

## Correctness

625 of 625 query results match the sequential scan (and the workload's expected counts).
After 200 inserts, 425 results were checked again: 0 mismatches.

## Build and storage

| method | build time (s) | pages written | size (pages) | size |
|---|---|---|---|---|
| seqscan | 10.14 | 25,109 | 25,109 | 98.08 MB |
| btree | 3.78 | 17,776 | 17,772 | 69.42 MB |
| bitmap-plain | 1.55 | 2,682 | 2,680 | 10.38 MB |
| bitmap-wah | 1.64 | 2,468 | 2,466 | 9.56 MB |
| bitmap-roaring | 1.71 | 2,157 | 2,155 | 8.31 MB |
| inverted | 2.26 | 2,022 | 2,022 | 7.90 MB |
| inverted-raw | 1.66 | 6,225 | 6,225 | 24.31 MB |

## Inserts (200 rows)

The seqscan row is the heap insert alone; every index pays it too.

| method | ms / insert | page reads / insert | page writes / insert |
|---|---|---|---|
| seqscan | 0.0097 | 0.005 | 0.03 |
| btree | 0.278 | 2.83 | 4.80 |
| bitmap-plain | 0.624 | 36.74 | 36.74 |
| bitmap-wah | 3.713 | 33.20 | 33.20 |
| bitmap-roaring | 1.787 | 23.91 | 23.91 |
| inverted | 128.447 | 44.87 | 48.73 |
| inverted-raw | 104.828 | 240.84 | 240.46 |

## Measured versus estimated page I/O

Each method's analytical model of its cold-cache page reads (see `dbmsi/bench/methods.py`) against the measured count. *error* compares the totals over all queries of a spec; *within 5%* is the share of single queries within 5% of their estimate. Over all queries: 10,735,311 measured, 10,733,545 estimated (+0.0%).

| method | spec | queries | measured | estimated | error | within 5% |
|---|---|---|---|---|---|---|
| seqscan | and | 25 | 627,725 | 627,725.0 | +0.0% | 100% |
| seqscan | or | 25 | 627,725 | 627,725.0 | +0.0% | 100% |
| seqscan | point:category | 25 | 627,725 | 627,725.0 | +0.0% | 100% |
| seqscan | point:extension | 25 | 627,725 | 627,725.0 | +0.0% | 100% |
| seqscan | point:size | 25 | 627,725 | 627,725.0 | +0.0% | 100% |
| seqscan | range:mtime | 25 | 627,725 | 627,725.0 | +0.0% | 100% |
| seqscan | range:size | 25 | 627,725 | 627,725.0 | +0.0% | 100% |
| seqscan | term | 25 | 627,725 | 627,725.0 | +0.0% | 100% |
| btree | point:category | 25 | 398,732 | 397,919.9 | +0.2% | 100% |
| btree | point:extension | 25 | 387,681 | 387,981.4 | -0.1% | 100% |
| btree | point:size | 25 | 208,538 | 208,938.9 | -0.2% | 100% |
| btree | range:mtime | 25 | 306,531 | 306,555.5 | -0.0% | 100% |
| btree | range:size | 25 | 332,526 | 332,697.6 | -0.1% | 100% |
| bitmap-plain | point:category | 25 | 424,585 | 423,769.3 | +0.2% | 100% |
| bitmap-plain | point:extension | 25 | 419,087 | 419,384.8 | -0.1% | 100% |
| bitmap-wah | point:category | 25 | 424,370 | 423,554.3 | +0.2% | 100% |
| bitmap-wah | point:extension | 25 | 418,872 | 419,169.8 | -0.1% | 100% |
| bitmap-roaring | point:category | 25 | 424,225 | 423,409.3 | +0.2% | 100% |
| bitmap-roaring | point:extension | 25 | 418,712 | 419,009.8 | -0.1% | 100% |
| inverted | and | 25 | 175,635 | 175,600.3 | +0.0% | 100% |
| inverted | or | 25 | 296,635 | 296,655.7 | -0.0% | 100% |
| inverted | term | 25 | 295,758 | 295,718.6 | +0.0% | 100% |
| inverted-raw | and | 25 | 183,428 | 183,261.2 | +0.1% | 100% |
| inverted-raw | or | 25 | 299,931 | 299,890.9 | +0.0% | 100% |
| inverted-raw | term | 25 | 298,265 | 298,227.7 | +0.0% | 100% |

## Concurrent searches and inserts (B+ tree)

20,000 operations (4,005 inserts) on a `size` tree, split across the threads, from a cold pool of 256 frames. `global` takes one tree-wide latch per operation; `crabbing` latches one root-to-leaf path. *read latency* is the modeled wait per physical page read; *speedup* is against the fewest threads of the same scheme and latency.

| latching | read latency (ms) | threads | ops / s | speedup | page reads | lost inserts |
|---|---|---|---|---|---|---|
| global | 0 | 1 | 4,402 | 1.00x | 23,864 | 0 |
| global | 0 | 2 | 4,111 | 0.93x | 23,471 | 0 |
| global | 0 | 4 | 4,088 | 0.93x | 23,668 | 0 |
| global | 0 | 8 | 3,841 | 0.87x | 23,735 | 0 |
| global | 0 | 16 | 3,465 | 0.79x | 23,792 | 0 |
| crabbing | 0 | 1 | 4,124 | 1.00x | 23,864 | 0 |
| crabbing | 0 | 2 | 3,846 | 0.93x | 24,050 | 0 |
| crabbing | 0 | 4 | 3,836 | 0.93x | 24,055 | 0 |
| crabbing | 0 | 8 | 3,807 | 0.92x | 23,451 | 0 |
| crabbing | 0 | 16 | 3,817 | 0.93x | 24,099 | 0 |
| global | 0.09 | 1 | 2,528 | 1.00x | 23,864 | 0 |
| global | 0.09 | 2 | 2,461 | 0.97x | 23,532 | 0 |
| global | 0.09 | 4 | 2,448 | 0.97x | 23,378 | 0 |
| global | 0.09 | 8 | 2,287 | 0.90x | 23,880 | 0 |
| global | 0.09 | 16 | 2,199 | 0.87x | 23,803 | 0 |
| crabbing | 0.09 | 1 | 2,422 | 1.00x | 23,864 | 0 |
| crabbing | 0.09 | 2 | 3,338 | 1.38x | 23,597 | 0 |
| crabbing | 0.09 | 4 | 3,720 | 1.54x | 23,412 | 0 |
| crabbing | 0.09 | 8 | 3,565 | 1.47x | 23,298 | 0 |
| crabbing | 0.09 | 16 | 3,311 | 1.37x | 23,254 | 0 |

Crabbing against the global latch at the same latency and thread count:

| read latency (ms) | threads | global ops / s | crabbing ops / s | crabbing / global |
|---|---|---|---|---|
| 0 | 1 | 4,402 | 4,124 | 0.94x |
| 0 | 2 | 4,111 | 3,846 | 0.94x |
| 0 | 4 | 4,088 | 3,836 | 0.94x |
| 0 | 8 | 3,841 | 3,807 | 0.99x |
| 0 | 16 | 3,465 | 3,817 | 1.10x |
| 0.09 | 1 | 2,528 | 2,422 | 0.96x |
| 0.09 | 2 | 2,461 | 3,338 | 1.36x |
| 0.09 | 4 | 2,448 | 3,720 | 1.52x |
| 0.09 | 8 | 2,287 | 3,565 | 1.56x |
| 0.09 | 16 | 2,199 | 3,311 | 1.51x |

## Posting list decoding

Median time spent decoding posting lists per query (warm runs), against the whole query.

| spec | target sel. | method | decode (ms) | query (ms) | decode share |
|---|---|---|---|---|---|
| and | 0.0001 | inverted | 24.221 | 26.121 | 93% |
| and | 0.0001 | inverted-raw | 20.255 | 22.243 | 91% |
| and | 0.001 | inverted | 37.624 | 45.005 | 84% |
| and | 0.001 | inverted-raw | 31.856 | 39.528 | 81% |
| and | 0.0100 | inverted | 112.373 | 169.522 | 66% |
| and | 0.0100 | inverted-raw | 99.887 | 159.031 | 63% |
| and | 0.1000 | inverted | 134.542 | 214.935 | 63% |
| and | 0.1000 | inverted-raw | 118.881 | 201.457 | 59% |
| and | 0.5000 | inverted | 123.757 | 197.687 | 63% |
| and | 0.5000 | inverted-raw | 110.208 | 185.686 | 59% |
| or | 0.0001 | inverted | 0.040 | 0.209 | 19% |
| or | 0.0001 | inverted-raw | 0.031 | 0.201 | 15% |
| or | 0.001 | inverted | 0.385 | 3.526 | 11% |
| or | 0.001 | inverted-raw | 0.301 | 3.443 | 9% |
| or | 0.0100 | inverted | 4.217 | 37.577 | 11% |
| or | 0.0100 | inverted-raw | 3.427 | 36.858 | 9% |
| or | 0.1000 | inverted | 42.658 | 264.526 | 16% |
| or | 0.1000 | inverted-raw | 35.899 | 257.170 | 14% |
| or | 0.5000 | inverted | 129.279 | 732.796 | 18% |
| or | 0.5000 | inverted-raw | 113.627 | 721.422 | 16% |
| term | 0.0001 | inverted | 0.040 | 0.212 | 19% |
| term | 0.0001 | inverted-raw | 0.031 | 0.200 | 15% |
| term | 0.001 | inverted | 0.365 | 3.379 | 11% |
| term | 0.001 | inverted-raw | 0.287 | 3.248 | 9% |
| term | 0.0100 | inverted | 3.888 | 34.806 | 11% |
| term | 0.0100 | inverted-raw | 3.116 | 33.934 | 9% |
| term | 0.1000 | inverted | 44.031 | 274.268 | 16% |
| term | 0.1000 | inverted-raw | 37.331 | 268.214 | 14% |
| term | 0.5000 | inverted | 84.702 | 504.868 | 17% |
| term | 0.5000 | inverted-raw | 74.212 | 496.474 | 15% |

## Queries

Mean physical page reads per query, cold cache (median and 95th percentile warm latency in ms in parentheses). A dash means the method does not support the query. Full I/O costs for each k are in `summary.csv`.

### `and`

| target sel. | actual sel. | seqscan | inverted | inverted-raw |
|---|---|---|---|---|
| 0.0001 | 0.0001 | 25,109.0 (1,780.51 / 1,788.05) | 127.6 (26.12 / 106.83) | 223.2 (22.24 / 102.48) |
| 0.001 | 0.0010 | 25,109.0 (1,785.09 / 1,789.61) | 1,041.2 (45.00 / 195.45) | 1,166.4 (39.53 / 188.85) |
| 0.010 | 0.0102 | 25,109.0 (1,788.65 / 1,798.27) | 8,458.2 (169.52 / 327.05) | 8,852.6 (159.03 / 320.17) |
| 0.100 | 0.0189 | 25,109.0 (1,788.62 / 1,790.37) | 13,439.8 (214.94 / 394.31) | 13,925.2 (201.46 / 389.75) |
| 0.500 | 0.0163 | 25,109.0 (1,792.24 / 1,794.16) | 12,060.2 (197.69 / 371.58) | 12,518.2 (185.69 / 356.27) |

### `or`

| target sel. | actual sel. | seqscan | inverted | inverted-raw |
|---|---|---|---|---|
| 0.0001 | 0.0001 | 25,109.0 (1,827.15 / 1,830.53) | 96.8 (0.21 / 0.21) | 97.2 (0.20 / 0.21) |
| 0.001 | 0.0009 | 25,109.0 (1,827.75 / 1,835.26) | 934.8 (3.53 / 3.59) | 935.6 (3.44 / 3.53) |
| 0.010 | 0.0100 | 25,109.0 (1,844.60 / 1,850.17) | 8,300.6 (37.58 / 38.78) | 8,313.8 (36.86 / 37.58) |
| 0.100 | 0.1018 | 25,109.0 (1,854.42 / 1,978.75) | 24,786.2 (264.53 / 380.89) | 24,941.8 (257.17 / 374.81) |
| 0.500 | 0.2815 | 25,109.0 (1,893.26 / 2,061.14) | 25,208.6 (732.80 / 933.82) | 25,697.8 (721.42 / 944.42) |

### `point:category`

| target sel. | actual sel. | seqscan | btree | bitmap-plain | bitmap-wah | bitmap-roaring |
|---|---|---|---|---|---|---|
| 0.0001 | 0.0112 | 25,109.0 (1,407.50 / 1,411.04) | 9,188.0 (42.07 / 42.74) | 10,622.0 (45.97 / 46.87) | 10,607.0 (46.24 / 47.00) | 10,597.0 (44.04 / 44.74) |
| 0.001 | 0.0112 | 25,109.0 (1,402.96 / 1,406.72) | 9,188.0 (41.98 / 42.82) | 10,622.0 (46.03 / 46.84) | 10,607.0 (46.22 / 47.20) | 10,597.0 (43.86 / 44.84) |
| 0.010 | 0.0112 | 25,109.0 (1,412.35 / 1,415.85) | 9,188.0 (42.16 / 42.94) | 10,622.0 (45.93 / 46.72) | 10,607.0 (46.21 / 46.99) | 10,597.0 (43.97 / 44.84) |
| 0.100 | 0.1181 | 25,109.0 (1,432.27 / 1,556.10) | 25,587.0 (319.17 / 451.33) | 26,444.0 (297.78 / 433.84) | 26,445.0 (301.88 / 446.52) | 26,445.0 (299.45 / 435.49) |
| 0.500 | 0.2743 | 25,109.0 (1,469.57 / 1,650.64) | 26,595.4 (736.15 / 988.80) | 26,607.0 (674.92 / 1,018.90) | 26,608.0 (685.66 / 953.29) | 26,609.0 (680.70 / 945.05) |

### `point:extension`

| target sel. | actual sel. | seqscan | btree | bitmap-plain | bitmap-wah | bitmap-roaring |
|---|---|---|---|---|---|---|
| 0.0001 | 0.0112 | 25,109.0 (1,360.51 / 1,364.17) | 9,134.0 (42.14 / 42.90) | 10,568.2 (45.85 / 46.65) | 10,553.2 (46.08 / 46.94) | 10,543.2 (44.06 / 44.81) |
| 0.001 | 0.0112 | 25,109.0 (1,356.37 / 1,359.73) | 9,126.4 (42.07 / 42.81) | 10,560.2 (46.00 / 46.92) | 10,545.2 (46.20 / 47.08) | 10,535.2 (44.21 / 45.27) |
| 0.010 | 0.0112 | 25,109.0 (1,365.01 / 1,368.96) | 9,123.8 (42.16 / 42.85) | 10,557.6 (45.96 / 46.77) | 10,542.6 (46.12 / 46.98) | 10,532.6 (44.13 / 44.86) |
| 0.100 | 0.0924 | 25,109.0 (1,377.47 / 1,383.05) | 25,042.0 (251.74 / 361.75) | 26,037.8 (237.67 / 354.57) | 26,038.8 (241.27 / 361.49) | 26,037.8 (239.00 / 357.41) |
| 0.500 | 0.0946 | 25,109.0 (1,378.92 / 1,405.92) | 25,110.0 (253.20 / 363.16) | 26,093.6 (238.26 / 355.49) | 26,094.6 (241.06 / 363.89) | 26,093.6 (239.15 / 359.60) |

### `point:size`

| target sel. | actual sel. | seqscan | btree |
|---|---|---|---|
| 0.0001 | 0.0001 | 25,109.0 (1,064.52 / 1,069.37) | 91.4 (0.25 / 0.26) |
| 0.001 | 0.0001 | 25,109.0 (1,064.29 / 1,068.47) | 99.2 (0.29 / 0.29) |
| 0.010 | 0.0198 | 25,109.0 (1,071.11 / 1,074.93) | 13,839.0 (69.97 / 71.11) |
| 0.100 | 0.0198 | 25,109.0 (1,068.83 / 1,073.19) | 13,839.0 (69.82 / 71.02) |
| 0.500 | 0.0198 | 25,109.0 (1,068.77 / 1,075.19) | 13,839.0 (69.79 / 70.79) |

### `range:mtime`

| target sel. | actual sel. | seqscan | btree |
|---|---|---|---|
| 0.0001 | 0.0001 | 25,109.0 (1,071.56 / 1,072.56) | 103.0 (0.29 / 0.30) |
| 0.001 | 0.0010 | 25,109.0 (1,075.72 / 1,082.90) | 988.8 (3.94 / 4.02) |
| 0.010 | 0.0100 | 25,109.0 (1,079.60 / 1,082.54) | 8,325.8 (38.67 / 39.39) |
| 0.100 | 0.1000 | 25,109.0 (1,096.44 / 1,222.17) | 25,064.6 (289.53 / 405.08) |
| 0.500 | 0.5000 | 25,109.0 (1,367.71 / 1,408.45) | 26,824.0 (1,528.44 / 1,788.06) |

### `range:size`

| target sel. | actual sel. | seqscan | btree |
|---|---|---|---|
| 0.0001 | 0.0001 | 25,109.0 (1,073.79 / 1,080.24) | 124.0 (0.32 / 0.32) |
| 0.001 | 0.0086 | 25,109.0 (1,069.87 / 1,077.43) | 6,163.2 (4.35 / 4.52) |
| 0.010 | 0.0100 | 25,109.0 (1,081.64 / 1,085.51) | 8,320.2 (37.49 / 38.29) |
| 0.100 | 0.1000 | 25,109.0 (1,095.04 / 1,105.43) | 25,073.2 (278.84 / 396.65) |
| 0.500 | 0.5000 | 25,109.0 (1,367.03 / 1,408.00) | 26,824.6 (1,460.14 / 1,703.12) |

### `term`

| target sel. | actual sel. | seqscan | inverted | inverted-raw |
|---|---|---|---|---|
| 0.0001 | 0.0001 | 25,109.0 (1,794.67 / 1,798.63) | 100.8 (0.21 / 0.22) | 100.6 (0.20 / 0.20) |
| 0.001 | 0.0009 | 25,109.0 (1,802.11 / 1,809.00) | 923.4 (3.38 / 3.48) | 924.4 (3.25 / 3.32) |
| 0.010 | 0.0099 | 25,109.0 (1,784.22 / 1,787.79) | 8,240.4 (34.81 / 35.35) | 8,253.6 (33.93 / 34.34) |
| 0.100 | 0.0987 | 25,109.0 (1,805.63 / 1,813.54) | 24,720.0 (274.27 / 361.49) | 24,876.4 (268.21 / 394.83) |
| 0.500 | 0.2009 | 25,109.0 (1,833.54 / 1,967.21) | 25,167.0 (504.87 / 674.78) | 25,498.0 (496.47 / 710.17) |
