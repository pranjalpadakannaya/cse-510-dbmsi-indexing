# Benchmark: default

- Dataset: 100,000 rows (synthetic, seed 0), 2,509 heap pages
- Workload: 200 queries (query seed 0)
- Buffer pool: 256 pages. Page I/O is counted on one run per query from a cold cache; latency is the median and 95th percentile of up to 50 warm runs after 1 warm-up run(s) (at least 5, fewer than 50 once 2 s is spent)
- Index results include fetching the matching records from the heap (in RID order)
- I/O cost = sequential reads + k x random reads, for k in 1, 4, 10
- Run at 2026-11-16T07:12:59+00:00 on commit e08b05d, Python 3.13.9

## Correctness

625 of 625 query results match the sequential scan (and the workload's expected counts).
After 200 inserts, 425 results were checked again: 0 mismatches.

## Build and storage

| method | build time (s) | pages written | size (pages) | size |
|---|---|---|---|---|
| seqscan | 1.71 | 2,509 | 2,509 | 9.80 MB |
| btree | 0.53 | 1,792 | 1,788 | 6.98 MB |
| bitmap-plain | 0.30 | 307 | 305 | 1.04 MB |
| bitmap-wah | 0.32 | 272 | 270 | 980.02 KB |
| bitmap-roaring | 0.32 | 241 | 241 | 872.23 KB |
| inverted | 0.37 | 200 | 200 | 799.88 KB |
| inverted-raw | 0.29 | 617 | 617 | 2.41 MB |

## Inserts (200 rows)

The seqscan row is the heap insert alone; every index pays it too.

| method | ms / insert | page reads / insert | page writes / insert |
|---|---|---|---|
| seqscan | 0.012 | 0.005 | 0.03 |
| btree | 0.277 | 2.02 | 3.25 |
| bitmap-plain | 0.083 | 0.79 | 0.79 |
| bitmap-wah | 0.419 | 0.61 | 0.61 |
| bitmap-roaring | 1.482 | 0.47 | 0.47 |
| inverted | 11.848 | 2.47 | 5.04 |
| inverted-raw | 8.419 | 16.14 | 25.14 |

## Measured versus estimated page I/O

Each method's analytical model of its cold-cache page reads (see `dbmsi/bench/methods.py`) against the measured count. *error* compares the totals over all queries of a spec; *within 5%* is the share of single queries within 5% of their estimate. Over all queries: 1,072,490 measured, 1,072,403 estimated (+0.0%).

| method | spec | queries | measured | estimated | error | within 5% |
|---|---|---|---|---|---|---|
| seqscan | and | 25 | 62,725 | 62,725.0 | +0.0% | 100% |
| seqscan | or | 25 | 62,725 | 62,725.0 | +0.0% | 100% |
| seqscan | point:category | 25 | 62,725 | 62,725.0 | +0.0% | 100% |
| seqscan | point:extension | 25 | 62,725 | 62,725.0 | +0.0% | 100% |
| seqscan | point:size | 25 | 62,725 | 62,725.0 | +0.0% | 100% |
| seqscan | range:mtime | 25 | 62,725 | 62,725.0 | +0.0% | 100% |
| seqscan | range:size | 25 | 62,725 | 62,725.0 | +0.0% | 100% |
| seqscan | term | 25 | 62,725 | 62,725.0 | +0.0% | 100% |
| btree | point:category | 25 | 39,541 | 39,551.8 | -0.0% | 100% |
| btree | point:extension | 25 | 38,754 | 38,812.4 | -0.2% | 100% |
| btree | point:size | 25 | 21,104 | 20,965.0 | +0.7% | 100% |
| btree | range:mtime | 25 | 30,674 | 30,705.6 | -0.1% | 100% |
| btree | range:size | 25 | 32,047 | 32,030.0 | +0.1% | 100% |
| bitmap-plain | point:category | 25 | 42,085 | 42,093.8 | -0.0% | 100% |
| bitmap-plain | point:extension | 25 | 41,863 | 41,919.7 | -0.1% | 100% |
| bitmap-wah | point:category | 25 | 42,055 | 42,063.8 | -0.0% | 100% |
| bitmap-wah | point:extension | 25 | 41,833 | 41,889.7 | -0.1% | 100% |
| bitmap-roaring | point:category | 25 | 42,050 | 42,058.8 | -0.0% | 100% |
| bitmap-roaring | point:extension | 25 | 41,818 | 41,874.7 | -0.1% | 100% |
| inverted | and | 25 | 18,130 | 18,052.8 | +0.4% | 96% |
| inverted | or | 25 | 29,841 | 29,862.6 | -0.1% | 96% |
| inverted | term | 25 | 29,780 | 29,736.0 | +0.1% | 100% |
| inverted-raw | and | 25 | 18,912 | 18,813.7 | +0.5% | 96% |
| inverted-raw | or | 25 | 30,168 | 30,184.1 | -0.1% | 100% |
| inverted-raw | term | 25 | 30,035 | 29,988.1 | +0.2% | 100% |

## Concurrent searches and inserts (B+ tree)

20,000 operations (4,025 inserts) on a `size` tree, split across the threads, from a cold pool of 256 frames. `global` takes one tree-wide latch per operation; `crabbing` latches one root-to-leaf path. *read latency* is the modeled wait per physical page read; *speedup* is against the fewest threads of the same scheme and latency.

| latching | read latency (ms) | threads | ops / s | speedup | page reads | lost inserts |
|---|---|---|---|---|---|---|
| global | 0 | 1 | 12,818 | 1.00x | 12,081 | 0 |
| global | 0 | 2 | 13,108 | 1.02x | 12,186 | 0 |
| global | 0 | 4 | 12,759 | 1.00x | 12,270 | 0 |
| global | 0 | 8 | 12,216 | 0.95x | 12,155 | 0 |
| global | 0 | 16 | 11,980 | 0.93x | 12,048 | 0 |
| crabbing | 0 | 1 | 11,718 | 1.00x | 12,081 | 0 |
| crabbing | 0 | 2 | 11,849 | 1.01x | 12,123 | 0 |
| crabbing | 0 | 4 | 11,422 | 0.97x | 12,222 | 0 |
| crabbing | 0 | 8 | 10,417 | 0.89x | 12,123 | 0 |
| crabbing | 0 | 16 | 10,161 | 0.87x | 12,181 | 0 |
| global | 0.20 | 1 | 4,399 | 1.00x | 12,081 | 0 |
| global | 0.20 | 2 | 4,347 | 0.99x | 12,095 | 0 |
| global | 0.20 | 4 | 4,259 | 0.97x | 12,110 | 0 |
| global | 0.20 | 8 | 4,294 | 0.98x | 11,921 | 0 |
| global | 0.20 | 16 | 4,247 | 0.97x | 12,018 | 0 |
| crabbing | 0.20 | 1 | 4,307 | 1.00x | 12,081 | 0 |
| crabbing | 0.20 | 2 | 7,684 | 1.78x | 12,088 | 0 |
| crabbing | 0.20 | 4 | 11,293 | 2.62x | 12,154 | 0 |
| crabbing | 0.20 | 8 | 11,649 | 2.70x | 12,073 | 0 |
| crabbing | 0.20 | 16 | 11,376 | 2.64x | 12,037 | 0 |
| global | 1.00 | 1 | 1,201 | 1.00x | 12,081 | 0 |
| global | 1.00 | 2 | 1,065 | 0.89x | 12,048 | 0 |
| global | 1.00 | 4 | 1,100 | 0.92x | 12,136 | 0 |
| global | 1.00 | 8 | 1,187 | 0.99x | 12,089 | 0 |
| global | 1.00 | 16 | 1,182 | 0.98x | 12,175 | 0 |
| crabbing | 1.00 | 1 | 1,194 | 1.00x | 12,081 | 0 |
| crabbing | 1.00 | 2 | 2,335 | 1.96x | 12,096 | 0 |
| crabbing | 1.00 | 4 | 4,479 | 3.75x | 12,112 | 0 |
| crabbing | 1.00 | 8 | 8,156 | 6.83x | 12,072 | 0 |
| crabbing | 1.00 | 16 | 11,364 | 9.52x | 12,110 | 0 |

Crabbing against the global latch at the same latency and thread count:

| read latency (ms) | threads | global ops / s | crabbing ops / s | crabbing / global |
|---|---|---|---|---|
| 0 | 1 | 12,818 | 11,718 | 0.91x |
| 0 | 2 | 13,108 | 11,849 | 0.90x |
| 0 | 4 | 12,759 | 11,422 | 0.90x |
| 0 | 8 | 12,216 | 10,417 | 0.85x |
| 0 | 16 | 11,980 | 10,161 | 0.85x |
| 0.20 | 1 | 4,399 | 4,307 | 0.98x |
| 0.20 | 2 | 4,347 | 7,684 | 1.77x |
| 0.20 | 4 | 4,259 | 11,293 | 2.65x |
| 0.20 | 8 | 4,294 | 11,649 | 2.71x |
| 0.20 | 16 | 4,247 | 11,376 | 2.68x |
| 1.00 | 1 | 1,201 | 1,194 | 0.99x |
| 1.00 | 2 | 1,065 | 2,335 | 2.19x |
| 1.00 | 4 | 1,100 | 4,479 | 4.07x |
| 1.00 | 8 | 1,187 | 8,156 | 6.87x |
| 1.00 | 16 | 1,182 | 11,364 | 9.61x |

## Posting list decoding

Median time spent decoding posting lists per query (warm runs), against the whole query.

| spec | target sel. | method | decode (ms) | query (ms) | decode share |
|---|---|---|---|---|---|
| and | 0.0001 | inverted | 1.037 | 1.139 | 91% |
| and | 0.0001 | inverted-raw | 0.770 | 0.875 | 88% |
| and | 0.001 | inverted | 4.142 | 4.628 | 90% |
| and | 0.001 | inverted-raw | 3.427 | 3.942 | 87% |
| and | 0.0100 | inverted | 8.702 | 12.779 | 68% |
| and | 0.0100 | inverted-raw | 7.376 | 11.580 | 64% |
| and | 0.1000 | inverted | 13.919 | 22.157 | 63% |
| and | 0.1000 | inverted-raw | 11.649 | 19.953 | 58% |
| and | 0.5000 | inverted | 14.666 | 23.592 | 62% |
| and | 0.5000 | inverted-raw | 12.298 | 21.301 | 58% |
| or | 0.0001 | inverted | 0.0063 | 0.052 | 12% |
| or | 0.0001 | inverted-raw | 0.0048 | 0.053 | 9% |
| or | 0.001 | inverted | 0.054 | 0.288 | 19% |
| or | 0.001 | inverted-raw | 0.038 | 0.268 | 14% |
| or | 0.0100 | inverted | 0.530 | 4.945 | 11% |
| or | 0.0100 | inverted-raw | 0.396 | 4.801 | 8% |
| or | 0.1000 | inverted | 5.110 | 33.744 | 15% |
| or | 0.1000 | inverted-raw | 3.917 | 31.965 | 12% |
| or | 0.5000 | inverted | 14.751 | 85.296 | 17% |
| or | 0.5000 | inverted-raw | 12.378 | 82.183 | 15% |
| term | 0.0001 | inverted | 0.0051 | 0.043 | 12% |
| term | 0.0001 | inverted-raw | 0.004 | 0.047 | 8% |
| term | 0.001 | inverted | 0.050 | 0.266 | 19% |
| term | 0.001 | inverted-raw | 0.036 | 0.260 | 14% |
| term | 0.0100 | inverted | 0.473 | 4.566 | 10% |
| term | 0.0100 | inverted-raw | 0.359 | 4.610 | 8% |
| term | 0.1000 | inverted | 4.688 | 32.444 | 14% |
| term | 0.1000 | inverted-raw | 3.865 | 31.622 | 12% |
| term | 0.5000 | inverted | 9.348 | 58.002 | 16% |
| term | 0.5000 | inverted-raw | 7.836 | 57.108 | 14% |

## Queries

Mean physical page reads per query, cold cache (median and 95th percentile warm latency in ms in parentheses). A dash means the method does not support the query. Full I/O costs for each k are in `summary.csv`.

### `and`

| target sel. | actual sel. | seqscan | inverted | inverted-raw |
|---|---|---|---|---|
| 0.0001 | 0.0001 | 2,509.0 (209.04 / 222.38) | 14.2 (1.14 / 1.28) | 26.0 (0.87 / 1.07) |
| 0.001 | 0.0011 | 2,509.0 (209.29 / 219.31) | 113.0 (4.63 / 6.01) | 127.6 (3.94 / 4.77) |
| 0.010 | 0.0092 | 2,509.0 (213.57 / 220.45) | 779.6 (12.78 / 23.39) | 812.4 (11.58 / 29.87) |
| 0.100 | 0.0191 | 2,509.0 (213.24 / 221.54) | 1,360.0 (22.16 / 40.79) | 1,408.2 (19.95 / 38.37) |
| 0.500 | 0.0190 | 2,509.0 (213.75 / 224.77) | 1,359.2 (23.59 / 42.32) | 1,408.2 (21.30 / 39.93) |

### `or`

| target sel. | actual sel. | seqscan | inverted | inverted-raw |
|---|---|---|---|---|
| 0.0001 | 0.0001 | 2,509.0 (222.64 / 228.67) | 11.4 (0.05 / 0.05) | 11.6 (0.05 / 0.05) |
| 0.001 | 0.0010 | 2,509.0 (221.90 / 225.03) | 99.2 (0.29 / 0.30) | 99.0 (0.27 / 0.27) |
| 0.010 | 0.0105 | 2,509.0 (225.67 / 231.47) | 861.8 (4.94 / 5.02) | 863.2 (4.80 / 4.84) |
| 0.100 | 0.1021 | 2,509.0 (228.19 / 239.26) | 2,474.8 (33.74 / 35.44) | 2,490.0 (31.96 / 32.67) |
| 0.500 | 0.2791 | 2,509.0 (223.68 / 237.63) | 2,521.0 (85.30 / 102.91) | 2,569.8 (82.18 / 104.43) |

### `point:category`

| target sel. | actual sel. | seqscan | btree | bitmap-plain | bitmap-wah | bitmap-roaring |
|---|---|---|---|---|---|---|
| 0.0001 | 0.0109 | 2,509.0 (191.84 / 194.21) | 894.0 (5.44 / 5.61) | 1,036.0 (6.04 / 6.08) | 1,034.0 (6.04 / 6.11) | 1,033.0 (5.79 / 5.90) |
| 0.001 | 0.0109 | 2,509.0 (177.72 / 186.45) | 894.0 (5.09 / 5.47) | 1,036.0 (5.59 / 6.06) | 1,034.0 (5.47 / 5.78) | 1,033.0 (5.31 / 5.91) |
| 0.010 | 0.0109 | 2,509.0 (175.57 / 187.48) | 894.0 (4.85 / 5.34) | 1,036.0 (5.46 / 5.93) | 1,034.0 (5.46 / 6.02) | 1,033.0 (5.32 / 5.72) |
| 0.100 | 0.1192 | 2,509.0 (177.65 / 189.29) | 2,565.0 (38.73 / 42.18) | 2,649.0 (36.31 / 39.03) | 2,649.0 (36.82 / 39.36) | 2,650.0 (36.66 / 39.22) |
| 0.500 | 0.2758 | 2,509.0 (179.79 / 195.97) | 2,661.2 (83.98 / 103.63) | 2,660.0 (76.85 / 100.89) | 2,660.0 (78.80 / 99.78) | 2,661.0 (78.89 / 101.96) |

### `point:extension`

| target sel. | actual sel. | seqscan | btree | bitmap-plain | bitmap-wah | bitmap-roaring |
|---|---|---|---|---|---|---|
| 0.0001 | 0.0112 | 2,509.0 (205.06 / 223.67) | 919.2 (6.34 / 7.48) | 1,061.2 (6.73 / 7.43) | 1,059.2 (6.64 / 7.10) | 1,058.2 (6.52 / 6.87) |
| 0.001 | 0.0112 | 2,509.0 (190.70 / 196.22) | 912.8 (5.85 / 6.47) | 1,054.8 (6.45 / 6.86) | 1,052.8 (6.40 / 7.12) | 1,051.8 (5.99 / 6.40) |
| 0.010 | 0.0112 | 2,509.0 (187.96 / 193.41) | 914.8 (5.49 / 6.05) | 1,056.8 (6.27 / 6.79) | 1,054.8 (6.14 / 6.53) | 1,053.8 (5.92 / 6.75) |
| 0.100 | 0.0913 | 2,509.0 (186.02 / 194.86) | 2,498.0 (33.01 / 34.59) | 2,596.6 (31.97 / 33.84) | 2,596.6 (32.34 / 33.84) | 2,596.6 (31.79 / 33.52) |
| 0.500 | 0.0939 | 2,509.0 (183.62 / 192.19) | 2,506.0 (33.17 / 35.20) | 2,603.2 (31.78 / 33.07) | 2,603.2 (32.06 / 33.35) | 2,603.2 (32.00 / 33.84) |

### `point:size`

| target sel. | actual sel. | seqscan | btree |
|---|---|---|---|
| 0.0001 | 0.0001 | 2,509.0 (191.78 / 196.28) | 12.2 (0.20 / 0.27) |
| 0.001 | 0.0002 | 2,509.0 (195.87 / 201.71) | 20.6 (0.17 / 0.30) |
| 0.010 | 0.0198 | 2,509.0 (200.68 / 205.19) | 1,396.0 (13.29 / 15.11) |
| 0.100 | 0.0198 | 2,509.0 (209.57 / 220.43) | 1,396.0 (13.81 / 18.40) |
| 0.500 | 0.0198 | 2,509.0 (155.76 / 159.38) | 1,396.0 (10.21 / 10.63) |

### `range:mtime`

| target sel. | actual sel. | seqscan | btree |
|---|---|---|---|
| 0.0001 | 0.0001 | 2,509.0 (130.57 / 139.69) | 13.0 (0.11 / 0.13) |
| 0.001 | 0.0010 | 2,509.0 (129.11 / 135.85) | 101.4 (0.35 / 0.37) |
| 0.010 | 0.0100 | 2,509.0 (128.76 / 136.18) | 831.8 (4.64 / 5.06) |
| 0.100 | 0.1000 | 2,509.0 (131.84 / 140.67) | 2,505.0 (34.21 / 36.41) |
| 0.500 | 0.5000 | 2,509.0 (138.18 / 159.52) | 2,683.6 (148.94 / 174.13) |

### `range:size`

| target sel. | actual sel. | seqscan | btree |
|---|---|---|---|
| 0.0001 | 0.0001 | 2,509.0 (132.66 / 140.52) | 16.6 (0.13 / 0.15) |
| 0.001 | 0.0048 | 2,509.0 (132.69 / 140.93) | 364.6 (0.34 / 0.41) |
| 0.010 | 0.0100 | 2,509.0 (133.40 / 138.16) | 836.8 (4.63 / 5.07) |
| 0.100 | 0.1000 | 2,509.0 (133.54 / 146.20) | 2,507.8 (33.34 / 35.88) |
| 0.500 | 0.5000 | 2,509.0 (139.92 / 162.88) | 2,683.6 (149.55 / 173.73) |

### `term`

| target sel. | actual sel. | seqscan | inverted | inverted-raw |
|---|---|---|---|---|
| 0.0001 | 0.0001 | 2,509.0 (211.52 / 222.89) | 10.4 (0.04 / 0.05) | 10.4 (0.05 / 0.05) |
| 0.001 | 0.0010 | 2,509.0 (211.64 / 223.79) | 102.6 (0.27 / 0.28) | 102.8 (0.26 / 0.27) |
| 0.010 | 0.0102 | 2,509.0 (210.65 / 219.50) | 851.8 (4.57 / 4.88) | 853.2 (4.61 / 5.07) |
| 0.100 | 0.0997 | 2,509.0 (215.23 / 228.00) | 2,475.2 (32.44 / 34.56) | 2,490.6 (31.62 / 33.33) |
| 0.500 | 0.2010 | 2,509.0 (215.52 / 225.74) | 2,516.0 (58.00 / 68.72) | 2,550.0 (57.11 / 77.39) |
