# Benchmark: scale-num_files=100000

- Dataset: 100,000 rows (synthetic, seed 0), 2,509 heap pages
- Workload: 75 queries (query seed 0)
- Buffer pool: 256 pages. Page I/O is counted on one run per query from a cold cache; latency is the median and 95th percentile of up to 50 warm runs after 1 warm-up run(s) (at least 3, fewer than 50 once 1 s is spent)
- Index results include fetching the matching records from the heap (in RID order)
- I/O cost = sequential reads + k x random reads, for k in 1, 4, 10
- Run at 2026-11-16T06:58:47+00:00 on commit e08b05d, Python 3.13.9

## Correctness

210 of 210 query results match the sequential scan (and the workload's expected counts).
After 200 inserts, 135 results were checked again: 0 mismatches.

## Build and storage

| method | build time (s) | pages written | size (pages) | size |
|---|---|---|---|---|
| seqscan | 1.70 | 2,509 | 2,509 | 9.80 MB |
| btree | 0.24 | 896 | 894 | 3.49 MB |
| bitmap-wah | 0.19 | 236 | 236 | 868.78 KB |
| bitmap-roaring | 0.19 | 209 | 209 | 772.03 KB |
| inverted | 0.41 | 200 | 200 | 799.88 KB |
| inverted-raw | 0.31 | 617 | 617 | 2.41 MB |

## Inserts (200 rows)

The seqscan row is the heap insert alone; every index pays it too.

| method | ms / insert | page reads / insert | page writes / insert |
|---|---|---|---|
| seqscan | 0.014 | 0.005 | 0.03 |
| btree | 0.154 | 0.94 | 1.60 |
| bitmap-wah | 0.225 | 0.44 | 0.44 |
| bitmap-roaring | 0.325 | 0.30 | 0.30 |
| inverted | 12.724 | 2.43 | 5.04 |
| inverted-raw | 9.175 | 16.07 | 25.14 |

## Measured versus estimated page I/O

Each method's analytical model of its cold-cache page reads (see `dbmsi/bench/methods.py`) against the measured count. *error* compares the totals over all queries of a spec; *within 5%* is the share of single queries within 5% of their estimate. Over all queries: 351,266 measured, 351,236 estimated (+0.0%).

| method | spec | queries | measured | estimated | error | within 5% |
|---|---|---|---|---|---|---|
| seqscan | and | 15 | 37,635 | 37,635.0 | +0.0% | 100% |
| seqscan | point:extension | 15 | 37,635 | 37,635.0 | +0.0% | 100% |
| seqscan | point:size | 15 | 37,635 | 37,635.0 | +0.0% | 100% |
| seqscan | range:size | 15 | 37,635 | 37,635.0 | +0.0% | 100% |
| seqscan | term | 15 | 37,635 | 37,635.0 | +0.0% | 100% |
| btree | point:extension | 15 | 23,244 | 23,299.9 | -0.2% | 100% |
| btree | point:size | 15 | 12,661 | 12,578.6 | +0.7% | 100% |
| btree | range:size | 15 | 18,442 | 18,435.3 | +0.0% | 93% |
| bitmap-wah | point:extension | 15 | 25,090 | 25,144.8 | -0.2% | 100% |
| bitmap-roaring | point:extension | 15 | 25,081 | 25,135.8 | -0.2% | 100% |
| inverted | and | 15 | 11,222 | 11,166.4 | +0.5% | 93% |
| inverted | term | 15 | 17,750 | 17,759.8 | -0.1% | 100% |
| inverted-raw | and | 15 | 11,701 | 11,633.1 | +0.6% | 93% |
| inverted-raw | term | 15 | 17,900 | 17,907.6 | -0.0% | 87% |

## Concurrent searches and inserts (B+ tree)

20,000 operations (4,025 inserts) on a `size` tree, split across the threads, from a cold pool of 256 frames. `global` takes one tree-wide latch per operation; `crabbing` latches one root-to-leaf path. *read latency* is the modeled wait per physical page read; *speedup* is against the fewest threads of the same scheme and latency.

| latching | read latency (ms) | threads | ops / s | speedup | page reads | lost inserts |
|---|---|---|---|---|---|---|
| global | 0 | 1 | 11,927 | 1.00x | 12,081 | 0 |
| global | 0 | 2 | 11,746 | 0.98x | 12,183 | 0 |
| global | 0 | 4 | 11,677 | 0.98x | 12,126 | 0 |
| global | 0 | 8 | 11,416 | 0.96x | 12,146 | 0 |
| crabbing | 0 | 1 | 10,874 | 1.00x | 12,081 | 0 |
| crabbing | 0 | 2 | 10,854 | 1.00x | 12,069 | 0 |
| crabbing | 0 | 4 | 9,933 | 0.91x | 12,075 | 0 |
| crabbing | 0 | 8 | 9,492 | 0.87x | 12,144 | 0 |

Crabbing against the global latch at the same latency and thread count:

| read latency (ms) | threads | global ops / s | crabbing ops / s | crabbing / global |
|---|---|---|---|---|
| 0 | 1 | 11,927 | 10,874 | 0.91x |
| 0 | 2 | 11,746 | 10,854 | 0.92x |
| 0 | 4 | 11,677 | 9,933 | 0.85x |
| 0 | 8 | 11,416 | 9,492 | 0.83x |

## Posting list decoding

Median time spent decoding posting lists per query (warm runs), against the whole query.

| spec | target sel. | method | decode (ms) | query (ms) | decode share |
|---|---|---|---|---|---|
| and | 0.0001 | inverted | 2.839 | 3.091 | 92% |
| and | 0.0001 | inverted-raw | 2.314 | 2.598 | 89% |
| and | 0.001 | inverted | 4.225 | 4.818 | 88% |
| and | 0.001 | inverted-raw | 3.300 | 3.871 | 85% |
| and | 0.0100 | inverted | 13.231 | 18.664 | 71% |
| and | 0.0100 | inverted-raw | 10.767 | 16.546 | 65% |
| and | 0.1000 | inverted | 15.588 | 24.734 | 63% |
| and | 0.1000 | inverted-raw | 13.203 | 22.432 | 59% |
| and | 0.5000 | inverted | 16.203 | 26.036 | 62% |
| and | 0.5000 | inverted-raw | 13.625 | 23.631 | 58% |
| term | 0.0001 | inverted | 0.007 | 0.055 | 13% |
| term | 0.0001 | inverted-raw | 0.0055 | 0.057 | 10% |
| term | 0.001 | inverted | 0.057 | 0.312 | 18% |
| term | 0.001 | inverted-raw | 0.035 | 0.257 | 14% |
| term | 0.0100 | inverted | 0.540 | 5.178 | 10% |
| term | 0.0100 | inverted-raw | 0.406 | 4.989 | 8% |
| term | 0.1000 | inverted | 4.767 | 32.821 | 15% |
| term | 0.1000 | inverted-raw | 3.686 | 31.404 | 12% |
| term | 0.5000 | inverted | 10.377 | 65.583 | 16% |
| term | 0.5000 | inverted-raw | 8.852 | 64.363 | 14% |

## Queries

Mean physical page reads per query, cold cache (median and 95th percentile warm latency in ms in parentheses). A dash means the method does not support the query. Full I/O costs for each k are in `summary.csv`.

### `and`

| target sel. | actual sel. | seqscan | inverted | inverted-raw |
|---|---|---|---|---|
| 0.0001 | 0.0001 | 2,509.0 (233.73 / 241.91) | 15.0 (3.09 / 3.61) | 24.3 (2.60 / 3.35) |
| 0.001 | 0.0011 | 2,509.0 (236.04 / 239.33) | 115.7 (4.82 / 5.10) | 130.0 (3.87 / 4.26) |
| 0.010 | 0.0103 | 2,509.0 (236.76 / 239.01) | 867.0 (18.66 / 35.50) | 906.0 (16.55 / 26.72) |
| 0.100 | 0.0190 | 2,509.0 (239.89 / 243.82) | 1,352.3 (24.73 / 44.12) | 1,400.3 (22.43 / 40.90) |
| 0.500 | 0.0197 | 2,509.0 (238.02 / 239.94) | 1,390.7 (26.04 / 45.34) | 1,439.7 (23.63 / 41.59) |

### `point:extension`

| target sel. | actual sel. | seqscan | btree | bitmap-wah | bitmap-roaring |
|---|---|---|---|---|---|
| 0.0001 | 0.0112 | 2,509.0 (253.99 / 260.35) | 916.3 (7.92 / 10.28) | 1,056.3 (8.67 / 11.28) | 1,055.3 (8.39 / 10.73) |
| 0.001 | 0.0113 | 2,509.0 (260.78 / 289.15) | 925.7 (8.11 / 11.52) | 1,065.7 (8.65 / 11.53) | 1,064.7 (8.72 / 11.39) |
| 0.010 | 0.0111 | 2,509.0 (265.99 / 274.67) | 899.3 (8.63 / 12.59) | 1,039.3 (8.65 / 11.53) | 1,038.3 (8.34 / 11.57) |
| 0.100 | 0.0973 | 2,509.0 (263.60 / 268.51) | 2,516.7 (45.67 / 50.25) | 2,612.0 (44.14 / 56.88) | 2,612.0 (39.01 / 47.38) |
| 0.500 | 0.0888 | 2,509.0 (199.70 / 216.49) | 2,490.0 (36.37 / 42.01) | 2,590.0 (35.90 / 37.20) | 2,590.0 (36.76 / 44.33) |

### `point:size`

| target sel. | actual sel. | seqscan | btree |
|---|---|---|---|
| 0.0001 | 0.0001 | 2,509.0 (187.52 / 202.36) | 12.3 (0.12 / 0.26) |
| 0.001 | 0.0002 | 2,509.0 (189.78 / 196.59) | 20.0 (0.16 / 0.30) |
| 0.010 | 0.0198 | 2,509.0 (198.88 / 208.16) | 1,396.0 (13.18 / 15.56) |
| 0.100 | 0.0198 | 2,509.0 (197.52 / 200.66) | 1,396.0 (13.61 / 15.05) |
| 0.500 | 0.0198 | 2,509.0 (200.05 / 204.16) | 1,396.0 (13.18 / 15.16) |

### `range:size`

| target sel. | actual sel. | seqscan | btree |
|---|---|---|---|
| 0.0001 | 0.0001 | 2,509.0 (160.50 / 174.96) | 13.0 (0.14 / 0.14) |
| 0.001 | 0.0010 | 2,509.0 (154.49 / 155.69) | 103.3 (0.41 / 0.44) |
| 0.010 | 0.0100 | 2,509.0 (158.92 / 159.77) | 837.7 (5.54 / 5.97) |
| 0.100 | 0.1000 | 2,509.0 (149.04 / 156.26) | 2,510.0 (37.61 / 39.97) |
| 0.500 | 0.5001 | 2,509.0 (154.47 / 171.10) | 2,683.3 (160.92 / 182.50) |

### `term`

| target sel. | actual sel. | seqscan | inverted | inverted-raw |
|---|---|---|---|---|
| 0.0001 | 0.0001 | 2,509.0 (236.46 / 243.33) | 11.7 (0.05 / 0.06) | 12.3 (0.06 / 0.06) |
| 0.001 | 0.0010 | 2,509.0 (236.72 / 242.20) | 95.7 (0.31 / 0.36) | 95.3 (0.26 / 0.35) |
| 0.010 | 0.0101 | 2,509.0 (240.30 / 248.17) | 838.3 (5.18 / 5.63) | 840.0 (4.99 / 5.92) |
| 0.100 | 0.0930 | 2,509.0 (241.23 / 259.19) | 2,455.0 (32.82 / 36.53) | 2,469.0 (31.40 / 33.12) |
| 0.500 | 0.2010 | 2,509.0 (239.57 / 249.43) | 2,516.0 (65.58 / 75.85) | 2,550.0 (64.36 / 74.75) |
