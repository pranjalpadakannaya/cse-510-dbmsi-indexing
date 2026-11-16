# Benchmark: 1m

- Dataset: 1,000,000 rows (synthetic, seed 0), 25,109 heap pages
- Workload: 200 queries (query seed 0)
- Buffer pool: 256 pages. Page I/O is counted on one run per query from a cold cache; latency is the median and 95th percentile of up to 50 warm runs after 1 warm-up run(s) (at least 5, fewer than 50 once 2 s is spent)
- Index results include fetching the matching records from the heap (in RID order)
- I/O cost = sequential reads + k x random reads, for k in 1, 4, 10
- Run at 2026-11-16T08:53:16+00:00 on commit e08b05d, Python 3.13.9

## Correctness

625 of 625 query results match the sequential scan (and the workload's expected counts).
After 200 inserts, 425 results were checked again: 0 mismatches.

## Build and storage

| method | build time (s) | pages written | size (pages) | size |
|---|---|---|---|---|
| seqscan | 11.23 | 25,109 | 25,109 | 98.08 MB |
| btree | 3.94 | 17,776 | 17,772 | 69.42 MB |
| bitmap-plain | 1.70 | 2,682 | 2,680 | 10.38 MB |
| bitmap-wah | 1.87 | 2,468 | 2,466 | 9.56 MB |
| bitmap-roaring | 1.90 | 2,157 | 2,155 | 8.31 MB |
| inverted | 2.31 | 2,022 | 2,022 | 7.90 MB |
| inverted-raw | 1.66 | 6,225 | 6,225 | 24.31 MB |

## Inserts (200 rows)

The seqscan row is the heap insert alone; every index pays it too.

| method | ms / insert | page reads / insert | page writes / insert |
|---|---|---|---|
| seqscan | 0.011 | 0.005 | 0.03 |
| btree | 0.303 | 2.83 | 4.80 |
| bitmap-plain | 0.674 | 36.74 | 36.74 |
| bitmap-wah | 3.736 | 33.20 | 33.20 |
| bitmap-roaring | 1.788 | 23.91 | 23.91 |
| inverted | 130.805 | 45.13 | 48.73 |
| inverted-raw | 102.764 | 240.88 | 240.46 |

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
| global | 0 | 1 | 4,179 | 1.00x | 23,864 | 0 |
| global | 0 | 2 | 3,740 | 0.90x | 24,020 | 0 |
| global | 0 | 4 | 3,578 | 0.86x | 23,877 | 0 |
| global | 0 | 8 | 3,339 | 0.80x | 23,508 | 0 |
| global | 0 | 16 | 3,313 | 0.79x | 23,544 | 0 |
| crabbing | 0 | 1 | 3,891 | 1.00x | 23,864 | 0 |
| crabbing | 0 | 2 | 3,640 | 0.94x | 24,022 | 0 |
| crabbing | 0 | 4 | 3,569 | 0.92x | 23,754 | 0 |
| crabbing | 0 | 8 | 3,570 | 0.92x | 24,062 | 0 |
| crabbing | 0 | 16 | 3,564 | 0.92x | 24,024 | 0 |
| global | 0.20 | 1 | 1,534 | 1.00x | 23,864 | 0 |
| global | 0.20 | 2 | 1,350 | 0.88x | 23,790 | 0 |
| global | 0.20 | 4 | 1,062 | 0.69x | 23,971 | 0 |
| global | 0.20 | 8 | 984 | 0.64x | 23,247 | 0 |
| global | 0.20 | 16 | 999 | 0.65x | 23,458 | 0 |
| crabbing | 0.20 | 1 | 1,485 | 1.00x | 23,864 | 0 |
| crabbing | 0.20 | 2 | 2,532 | 1.70x | 23,724 | 0 |
| crabbing | 0.20 | 4 | 3,395 | 2.29x | 23,543 | 0 |
| crabbing | 0.20 | 8 | 3,441 | 2.32x | 23,241 | 0 |
| crabbing | 0.20 | 16 | 3,184 | 2.14x | 23,264 | 0 |
| global | 1.00 | 1 | 393 | 1.00x | 23,864 | 0 |
| global | 1.00 | 2 | 351 | 0.89x | 23,926 | 0 |
| global | 1.00 | 4 | 332 | 0.85x | 24,139 | 0 |
| global | 1.00 | 8 | 331 | 0.84x | 23,584 | 0 |
| global | 1.00 | 16 | 331 | 0.84x | 23,649 | 0 |
| crabbing | 1.00 | 1 | 383 | 1.00x | 23,864 | 0 |
| crabbing | 1.00 | 2 | 728 | 1.90x | 23,389 | 0 |
| crabbing | 1.00 | 4 | 1,311 | 3.42x | 23,436 | 0 |
| crabbing | 1.00 | 8 | 2,211 | 5.77x | 23,329 | 0 |
| crabbing | 1.00 | 16 | 2,989 | 7.80x | 22,979 | 0 |

Crabbing against the global latch at the same latency and thread count:

| read latency (ms) | threads | global ops / s | crabbing ops / s | crabbing / global |
|---|---|---|---|---|
| 0 | 1 | 4,179 | 3,891 | 0.93x |
| 0 | 2 | 3,740 | 3,640 | 0.97x |
| 0 | 4 | 3,578 | 3,569 | 1.00x |
| 0 | 8 | 3,339 | 3,570 | 1.07x |
| 0 | 16 | 3,313 | 3,564 | 1.08x |
| 0.20 | 1 | 1,534 | 1,485 | 0.97x |
| 0.20 | 2 | 1,350 | 2,532 | 1.88x |
| 0.20 | 4 | 1,062 | 3,395 | 3.20x |
| 0.20 | 8 | 984 | 3,441 | 3.50x |
| 0.20 | 16 | 999 | 3,184 | 3.19x |
| 1.00 | 1 | 393 | 383 | 0.98x |
| 1.00 | 2 | 351 | 728 | 2.08x |
| 1.00 | 4 | 332 | 1,311 | 3.95x |
| 1.00 | 8 | 331 | 2,211 | 6.69x |
| 1.00 | 16 | 331 | 2,989 | 9.03x |

## Posting list decoding

Median time spent decoding posting lists per query (warm runs), against the whole query.

| spec | target sel. | method | decode (ms) | query (ms) | decode share |
|---|---|---|---|---|---|
| and | 0.0001 | inverted | 26.045 | 27.935 | 93% |
| and | 0.0001 | inverted-raw | 20.450 | 22.501 | 91% |
| and | 0.001 | inverted | 39.684 | 47.651 | 83% |
| and | 0.001 | inverted-raw | 31.770 | 40.224 | 79% |
| and | 0.0100 | inverted | 115.485 | 175.062 | 66% |
| and | 0.0100 | inverted-raw | 97.118 | 158.076 | 61% |
| and | 0.1000 | inverted | 137.700 | 220.374 | 62% |
| and | 0.1000 | inverted-raw | 117.305 | 201.799 | 58% |
| and | 0.5000 | inverted | 127.275 | 204.058 | 62% |
| and | 0.5000 | inverted-raw | 107.715 | 185.874 | 58% |
| or | 0.0001 | inverted | 0.046 | 0.239 | 19% |
| or | 0.0001 | inverted-raw | 0.032 | 0.228 | 14% |
| or | 0.001 | inverted | 0.437 | 4.209 | 10% |
| or | 0.001 | inverted-raw | 0.308 | 4.082 | 8% |
| or | 0.0100 | inverted | 4.628 | 44.318 | 10% |
| or | 0.0100 | inverted-raw | 3.482 | 43.038 | 8% |
| or | 0.1000 | inverted | 45.095 | 300.151 | 15% |
| or | 0.1000 | inverted-raw | 35.578 | 291.595 | 12% |
| or | 0.5000 | inverted | 133.879 | 801.016 | 17% |
| or | 0.5000 | inverted-raw | 113.277 | 777.509 | 15% |
| term | 0.0001 | inverted | 0.046 | 0.238 | 19% |
| term | 0.0001 | inverted-raw | 0.032 | 0.223 | 14% |
| term | 0.001 | inverted | 0.407 | 3.940 | 10% |
| term | 0.001 | inverted-raw | 0.295 | 3.834 | 8% |
| term | 0.0100 | inverted | 4.168 | 40.296 | 10% |
| term | 0.0100 | inverted-raw | 3.174 | 39.160 | 8% |
| term | 0.1000 | inverted | 45.975 | 307.950 | 15% |
| term | 0.1000 | inverted-raw | 37.564 | 303.518 | 12% |
| term | 0.5000 | inverted | 87.382 | 549.418 | 16% |
| term | 0.5000 | inverted-raw | 74.249 | 540.035 | 14% |

## Queries

Mean physical page reads per query, cold cache (median and 95th percentile warm latency in ms in parentheses). A dash means the method does not support the query. Full I/O costs for each k are in `summary.csv`.

### `and`

| target sel. | actual sel. | seqscan | inverted | inverted-raw |
|---|---|---|---|---|
| 0.0001 | 0.0001 | 25,109.0 (1,917.25 / 1,922.24) | 127.6 (27.93 / 110.33) | 223.2 (22.50 / 105.57) |
| 0.001 | 0.0010 | 25,109.0 (1,918.19 / 1,918.77) | 1,041.2 (47.65 / 197.47) | 1,166.4 (40.22 / 190.28) |
| 0.010 | 0.0102 | 25,109.0 (1,927.19 / 1,934.45) | 8,458.2 (175.06 / 335.71) | 8,852.6 (158.08 / 318.60) |
| 0.100 | 0.0189 | 25,109.0 (1,924.06 / 1,930.07) | 13,439.8 (220.37 / 390.62) | 13,925.2 (201.80 / 379.33) |
| 0.500 | 0.0163 | 25,109.0 (1,929.32 / 1,931.73) | 12,060.2 (204.06 / 366.42) | 12,518.2 (185.87 / 354.09) |

### `or`

| target sel. | actual sel. | seqscan | inverted | inverted-raw |
|---|---|---|---|---|
| 0.0001 | 0.0001 | 25,109.0 (1,944.81 / 1,948.00) | 96.8 (0.24 / 0.24) | 97.2 (0.23 / 0.23) |
| 0.001 | 0.0009 | 25,109.0 (1,951.58 / 1,953.41) | 934.8 (4.21 / 4.30) | 935.6 (4.08 / 4.12) |
| 0.010 | 0.0100 | 25,109.0 (1,945.29 / 1,950.26) | 8,300.6 (44.32 / 44.75) | 8,313.8 (43.04 / 43.55) |
| 0.100 | 0.1018 | 25,109.0 (1,965.31 / 2,093.20) | 24,786.2 (300.15 / 415.54) | 24,941.8 (291.60 / 410.88) |
| 0.500 | 0.2815 | 25,109.0 (1,996.35 / 2,169.62) | 25,208.6 (801.02 / 977.02) | 25,697.8 (777.51 / 956.97) |

### `point:category`

| target sel. | actual sel. | seqscan | btree | bitmap-plain | bitmap-wah | bitmap-roaring |
|---|---|---|---|---|---|---|
| 0.0001 | 0.0112 | 25,109.0 (1,560.56 / 1,563.72) | 9,188.0 (48.31 / 48.70) | 10,622.0 (53.89 / 54.30) | 10,607.0 (53.48 / 54.50) | 10,597.0 (51.51 / 52.14) |
| 0.001 | 0.0112 | 25,109.0 (1,561.57 / 1,565.09) | 9,188.0 (48.25 / 48.86) | 10,622.0 (53.78 / 54.46) | 10,607.0 (53.42 / 54.25) | 10,597.0 (50.94 / 51.55) |
| 0.010 | 0.0112 | 25,109.0 (1,561.64 / 1,565.52) | 9,188.0 (48.14 / 48.85) | 10,622.0 (53.95 / 54.69) | 10,607.0 (53.35 / 54.04) | 10,597.0 (50.98 / 51.48) |
| 0.100 | 0.1181 | 25,109.0 (1,581.14 / 1,709.85) | 25,587.0 (355.35 / 485.76) | 26,444.0 (336.56 / 478.13) | 26,445.0 (341.17 / 482.71) | 26,445.0 (341.58 / 479.71) |
| 0.500 | 0.2743 | 25,109.0 (1,609.10 / 1,750.92) | 26,595.4 (791.22 / 972.22) | 26,607.0 (727.24 / 968.94) | 26,608.0 (740.41 / 969.08) | 26,609.0 (739.68 / 967.98) |

### `point:extension`

| target sel. | actual sel. | seqscan | btree | bitmap-plain | bitmap-wah | bitmap-roaring |
|---|---|---|---|---|---|---|
| 0.0001 | 0.0112 | 25,109.0 (1,506.10 / 1,510.91) | 9,134.0 (48.59 / 49.13) | 10,568.2 (53.93 / 54.46) | 10,553.2 (53.26 / 53.70) | 10,543.2 (51.47 / 52.07) |
| 0.001 | 0.0112 | 25,109.0 (1,507.95 / 1,510.80) | 9,126.4 (48.25 / 48.72) | 10,560.2 (53.88 / 54.45) | 10,545.2 (53.39 / 54.03) | 10,535.2 (51.18 / 51.71) |
| 0.010 | 0.0112 | 25,109.0 (1,505.02 / 1,508.64) | 9,123.8 (48.18 / 48.84) | 10,557.6 (53.70 / 54.26) | 10,542.6 (53.52 / 54.06) | 10,532.6 (50.93 / 51.64) |
| 0.100 | 0.0924 | 25,109.0 (1,513.33 / 1,533.01) | 25,042.0 (284.60 / 397.31) | 26,037.8 (271.86 / 392.17) | 26,038.8 (275.16 / 397.64) | 26,037.8 (273.59 / 395.21) |
| 0.500 | 0.0946 | 25,109.0 (1,514.66 / 1,520.18) | 25,110.0 (283.74 / 389.42) | 26,093.6 (271.52 / 394.18) | 26,094.6 (275.89 / 396.96) | 26,093.6 (276.17 / 398.98) |

### `point:size`

| target sel. | actual sel. | seqscan | btree |
|---|---|---|---|
| 0.0001 | 0.0001 | 25,109.0 (1,162.31 / 1,168.55) | 91.4 (0.29 / 0.30) |
| 0.001 | 0.0001 | 25,109.0 (1,149.17 / 1,155.55) | 99.2 (0.32 / 0.33) |
| 0.010 | 0.0198 | 25,109.0 (1,158.72 / 1,161.37) | 13,839.0 (80.12 / 80.91) |
| 0.100 | 0.0198 | 25,109.0 (1,161.64 / 1,166.82) | 13,839.0 (80.17 / 81.06) |
| 0.500 | 0.0198 | 25,109.0 (1,159.58 / 1,164.99) | 13,839.0 (80.03 / 81.01) |

### `range:mtime`

| target sel. | actual sel. | seqscan | btree |
|---|---|---|---|
| 0.0001 | 0.0001 | 25,109.0 (1,173.13 / 1,175.25) | 103.0 (0.32 / 0.33) |
| 0.001 | 0.0010 | 25,109.0 (1,175.50 / 1,181.93) | 988.8 (4.55 / 4.69) |
| 0.010 | 0.0100 | 25,109.0 (1,179.17 / 1,180.85) | 8,325.8 (44.64 / 45.27) |
| 0.100 | 0.1000 | 25,109.0 (1,184.39 / 1,189.84) | 25,064.6 (323.75 / 448.92) |
| 0.500 | 0.5000 | 25,109.0 (1,270.74 / 1,467.25) | 26,824.0 (1,626.24 / 1,851.23) |

### `range:size`

| target sel. | actual sel. | seqscan | btree |
|---|---|---|---|
| 0.0001 | 0.0001 | 25,109.0 (1,169.73 / 1,171.85) | 124.0 (0.35 / 0.36) |
| 0.001 | 0.0086 | 25,109.0 (1,179.19 / 1,179.76) | 6,163.2 (5.05 / 5.22) |
| 0.010 | 0.0100 | 25,109.0 (1,184.17 / 1,188.52) | 8,320.2 (43.83 / 44.68) |
| 0.100 | 0.1000 | 25,109.0 (1,204.97 / 1,320.37) | 25,073.2 (316.17 / 437.65) |
| 0.500 | 0.5000 | 25,109.0 (1,446.75 / 1,465.76) | 26,824.6 (1,557.49 / 1,779.08) |

### `term`

| target sel. | actual sel. | seqscan | inverted | inverted-raw |
|---|---|---|---|---|
| 0.0001 | 0.0001 | 25,109.0 (1,920.11 / 1,921.29) | 100.8 (0.24 / 0.24) | 100.6 (0.22 / 0.23) |
| 0.001 | 0.0009 | 25,109.0 (1,925.91 / 1,938.98) | 923.4 (3.94 / 4.06) | 924.4 (3.83 / 3.86) |
| 0.010 | 0.0099 | 25,109.0 (1,919.57 / 1,923.05) | 8,240.4 (40.30 / 40.65) | 8,253.6 (39.16 / 39.52) |
| 0.100 | 0.0987 | 25,109.0 (1,937.80 / 1,943.54) | 24,720.0 (307.95 / 393.64) | 24,876.4 (303.52 / 394.39) |
| 0.500 | 0.2009 | 25,109.0 (1,950.46 / 2,090.13) | 25,167.0 (549.42 / 713.02) | 25,498.0 (540.03 / 731.55) |
