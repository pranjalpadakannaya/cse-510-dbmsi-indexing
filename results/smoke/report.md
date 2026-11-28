# Benchmark: smoke

- Dataset: 3,000 rows (synthetic, seed 0), 76 heap pages
- Workload: 72 queries (query seed 0)
- Buffer pool: 64 pages. Page I/O is counted on one run per query from a cold cache; latency is the median and 95th percentile of up to 3 warm runs after 1 warm-up run(s) (at least 1, fewer than 3 once 2 s is spent)
- Index results include fetching the matching records from the heap (in RID order)
- I/O cost = sequential reads + k x random reads, for k in 1, 4, 10 (assumed, not measured)
- Run at 2026-11-28T05:16:07+00:00 on commit a2ca9b5, Python 3.14.2

## Correctness

240 of 240 query results match the sequential scan (and the workload's expected counts).
After 30 inserts, 168 results were checked again: 0 mismatches.

## Build and storage

| method | build time (s) | pages written | size (pages) | size |
|---|---|---|---|---|
| seqscan | 0.07 | 76 | 76 | 304.00 KB |
| btree | 0.02 | 71 | 71 | 284.00 KB |
| bitmap-plain | 0.01 | 49 | 49 | 35.54 KB |
| bitmap-wah | 0.01 | 49 | 49 | 33.24 KB |
| bitmap-roaring | 0.01 | 49 | 49 | 37.52 KB |
| inverted | 0.07 | 6 | 6 | 22.72 KB |
| inverted-raw | 0.04 | 19 | 19 | 73.16 KB |

## Inserts (30 rows)

The seqscan row is the heap insert alone; every index pays it too.

| method | ms / insert | page reads / insert | page writes / insert |
|---|---|---|---|
| seqscan | 0.027 | 0.03 | 0.07 |
| btree | 0.438 | 1.67 | 1.50 |
| bitmap-plain | 0.065 | 1.00 | 1.00 |
| bitmap-wah | 0.107 | 1.00 | 1.00 |
| bitmap-roaring | 0.680 | 1.00 | 1.00 |
| inverted | 0.711 | 0.20 | 0.20 |
| inverted-raw | 0.502 | 0.60 | 0.83 |

## Measured versus estimated page I/O

Each method's analytical model of its cold-cache page reads (see `dbmsi/bench/methods.py`) against the measured count. *error* compares the totals over all queries of a spec; *within 5%* is the share of single queries within 5% of their estimate. Over all queries: 15,486 measured, 15,456 estimated (+0.2%).

| method | spec | queries | measured | estimated | error | within 5% |
|---|---|---|---|---|---|---|
| seqscan | and | 8 | 608 | 608.0 | +0.0% | 100% |
| seqscan | or | 8 | 608 | 608.0 | +0.0% | 100% |
| seqscan | point:extension | 8 | 608 | 608.0 | +0.0% | 100% |
| seqscan | point:size | 8 | 608 | 608.0 | +0.0% | 100% |
| seqscan | point:uid | 8 | 608 | 608.0 | +0.0% | 100% |
| seqscan | range:depth | 8 | 608 | 608.0 | +0.0% | 100% |
| seqscan | range:mtime | 8 | 608 | 608.0 | +0.0% | 100% |
| seqscan | range:size | 8 | 608 | 608.0 | +0.0% | 100% |
| seqscan | term | 8 | 608 | 608.0 | +0.0% | 100% |
| btree | point:extension | 8 | 414 | 420.2 | -1.5% | 62% |
| btree | point:size | 8 | 322 | 307.0 | +4.9% | 25% |
| btree | point:uid | 8 | 624 | 627.3 | -0.5% | 100% |
| btree | range:depth | 8 | 626 | 625.8 | +0.0% | 75% |
| btree | range:mtime | 8 | 383 | 386.7 | -1.0% | 100% |
| btree | range:size | 8 | 433 | 431.3 | +0.4% | 88% |
| bitmap-plain | point:extension | 8 | 442 | 444.9 | -0.7% | 100% |
| bitmap-plain | point:uid | 8 | 644 | 640.9 | +0.5% | 100% |
| bitmap-plain | range:depth | 8 | 641 | 639.9 | +0.2% | 88% |
| bitmap-wah | point:extension | 8 | 442 | 444.9 | -0.7% | 100% |
| bitmap-wah | point:uid | 8 | 644 | 640.9 | +0.5% | 100% |
| bitmap-wah | range:depth | 8 | 641 | 639.9 | +0.2% | 88% |
| bitmap-roaring | point:extension | 8 | 442 | 444.9 | -0.7% | 100% |
| bitmap-roaring | point:uid | 8 | 644 | 640.9 | +0.5% | 100% |
| bitmap-roaring | range:depth | 8 | 641 | 639.9 | +0.2% | 88% |
| inverted | and | 8 | 277 | 265.6 | +4.3% | 88% |
| inverted | or | 8 | 369 | 369.9 | -0.2% | 62% |
| inverted | term | 8 | 361 | 360.6 | +0.1% | 88% |
| inverted-raw | and | 8 | 286 | 274.8 | +4.1% | 50% |
| inverted-raw | or | 8 | 373 | 374.4 | -0.4% | 75% |
| inverted-raw | term | 8 | 365 | 363.6 | +0.4% | 88% |

## Concurrent searches and inserts (B+ tree)

2,000 operations (414 inserts) on a `size` tree, split across the threads, from a cold pool of 64 frames. `global` takes one tree-wide latch per operation; `crabbing` latches one root-to-leaf path. *read latency* is the modeled wait per physical page read; *speedup* is against the fewest threads of the same scheme and latency.

| latching | read latency (ms) | threads | ops / s | speedup | page reads | lost inserts |
|---|---|---|---|---|---|---|
| global | 0 | 1 | 13,592 | 1.00x | 12 | 0 |
| global | 0 | 4 | 13,527 | 1.00x | 12 | 0 |
| crabbing | 0 | 1 | 12,280 | 1.00x | 12 | 0 |
| crabbing | 0 | 4 | 12,009 | 0.98x | 12 | 0 |
| global | 0.20 | 1 | 12,987 | 1.00x | 12 | 0 |
| global | 0.20 | 4 | 12,776 | 0.98x | 12 | 0 |
| crabbing | 0.20 | 1 | 12,093 | 1.00x | 12 | 0 |
| crabbing | 0.20 | 4 | 12,072 | 1.00x | 12 | 0 |

Crabbing against the global latch at the same latency and thread count:

| read latency (ms) | threads | global ops / s | crabbing ops / s | crabbing / global |
|---|---|---|---|---|
| 0 | 1 | 13,592 | 12,280 | 0.90x |
| 0 | 4 | 13,527 | 12,009 | 0.89x |
| 0.20 | 1 | 12,987 | 12,093 | 0.93x |
| 0.20 | 4 | 12,776 | 12,072 | 0.94x |

## Posting list decoding

Median time spent decoding posting lists per query (warm runs), against the whole query.

| spec | target sel. | method | decode (ms) | query (ms) | decode share |
|---|---|---|---|---|---|
| and | 0.001 | inverted | 0.223 | 0.350 | 64% |
| and | 0.001 | inverted-raw | 0.173 | 0.279 | 62% |
| and | 0.0100 | inverted | 0.726 | 1.346 | 54% |
| and | 0.0100 | inverted-raw | 0.506 | 0.927 | 55% |
| and | 0.1000 | inverted | 0.775 | 1.531 | 51% |
| and | 0.1000 | inverted-raw | 0.673 | 1.322 | 51% |
| and | 0.5000 | inverted | 0.852 | 1.714 | 50% |
| and | 0.5000 | inverted-raw | 0.679 | 1.345 | 50% |
| or | 0.001 | inverted | 0.0071 | 0.088 | 8% |
| or | 0.001 | inverted-raw | 0.0055 | 0.085 | 7% |
| or | 0.0100 | inverted | 0.041 | 0.371 | 11% |
| or | 0.0100 | inverted-raw | 0.029 | 0.334 | 9% |
| or | 0.1000 | inverted | 0.296 | 3.850 | 8% |
| or | 0.1000 | inverted-raw | 0.231 | 3.033 | 8% |
| or | 0.5000 | inverted | 0.755 | 7.396 | 10% |
| or | 0.5000 | inverted-raw | 0.665 | 7.682 | 9% |
| term | 0.001 | inverted | 0.0069 | 0.086 | 8% |
| term | 0.001 | inverted-raw | 0.0051 | 0.083 | 6% |
| term | 0.0100 | inverted | 0.036 | 0.296 | 12% |
| term | 0.0100 | inverted-raw | 0.023 | 0.247 | 9% |
| term | 0.1000 | inverted | 0.244 | 2.600 | 9% |
| term | 0.1000 | inverted-raw | 0.209 | 2.416 | 9% |
| term | 0.5000 | inverted | 0.519 | 4.974 | 10% |
| term | 0.5000 | inverted-raw | 0.444 | 5.141 | 9% |

## BM25 ranking

Median time spent ranking retrieved candidates with BM25 per query (warm runs), compared with the whole query.

| spec | target sel. | method | ranking (ms) | query (ms) | ranking share |
|---|---|---|---|---|---|
| and | 0.001 | inverted | 0.021 | 0.350 | 6% |
| and | 0.001 | inverted-raw | 0.015 | 0.279 | 5% |
| and | 0.0100 | inverted | 0.179 | 1.346 | 13% |
| and | 0.0100 | inverted-raw | 0.130 | 0.927 | 14% |
| and | 0.1000 | inverted | 0.266 | 1.531 | 17% |
| and | 0.1000 | inverted-raw | 0.246 | 1.322 | 19% |
| and | 0.5000 | inverted | 0.308 | 1.714 | 18% |
| and | 0.5000 | inverted-raw | 0.257 | 1.345 | 19% |
| or | 0.001 | inverted | 0.011 | 0.088 | 12% |
| or | 0.001 | inverted-raw | 0.010 | 0.085 | 12% |
| or | 0.0100 | inverted | 0.106 | 0.371 | 28% |
| or | 0.0100 | inverted-raw | 0.089 | 0.334 | 27% |
| or | 0.1000 | inverted | 1.216 | 3.850 | 32% |
| or | 0.1000 | inverted-raw | 1.026 | 3.033 | 34% |
| or | 0.5000 | inverted | 2.596 | 7.396 | 35% |
| or | 0.5000 | inverted-raw | 3.009 | 7.682 | 39% |
| term | 0.001 | inverted | 0.011 | 0.086 | 13% |
| term | 0.001 | inverted-raw | 0.011 | 0.083 | 13% |
| term | 0.0100 | inverted | 0.072 | 0.296 | 24% |
| term | 0.0100 | inverted-raw | 0.061 | 0.247 | 25% |
| term | 0.1000 | inverted | 0.679 | 2.600 | 26% |
| term | 0.1000 | inverted-raw | 0.639 | 2.416 | 26% |
| term | 0.5000 | inverted | 1.591 | 4.974 | 32% |
| term | 0.5000 | inverted-raw | 1.542 | 5.141 | 30% |

## Queries

Mean physical page reads per query, cold cache (median and 95th percentile warm latency in ms in parentheses). A dash means the method does not support the query. Full I/O costs for each k are in `summary.csv`.

### `and`

| target sel. | actual sel. | seqscan | inverted | inverted-raw |
|---|---|---|---|---|
| 0.001 | 0.0010 | 76.0 (16.55 / 16.79) | 5.0 (0.35 / 0.37) | 6.0 (0.28 / 0.29) |
| 0.010 | 0.0117 | 76.0 (17.87 / 18.20) | 31.5 (1.35 / 1.65) | 33.0 (0.93 / 1.00) |
| 0.100 | 0.0240 | 76.0 (16.63 / 16.98) | 51.0 (1.53 / 1.68) | 52.0 (1.32 / 1.50) |
| 0.500 | 0.0240 | 76.0 (16.64 / 18.08) | 51.0 (1.71 / 1.86) | 52.0 (1.35 / 1.38) |

### `or`

| target sel. | actual sel. | seqscan | inverted | inverted-raw |
|---|---|---|---|---|
| 0.001 | 0.0007 | 76.0 (17.00 / 17.45) | 3.5 (0.09 / 0.09) | 3.5 (0.08 / 0.09) |
| 0.010 | 0.0098 | 76.0 (19.28 / 21.17) | 26.0 (0.37 / 0.42) | 26.0 (0.33 / 0.54) |
| 0.100 | 0.0988 | 76.0 (18.90 / 19.26) | 77.0 (3.85 / 4.41) | 77.0 (3.03 / 3.50) |
| 0.500 | 0.2770 | 76.0 (16.39 / 27.03) | 78.0 (7.40 / 7.50) | 80.0 (7.68 / 7.98) |

### `point:extension`

| target sel. | actual sel. | seqscan | btree | bitmap-plain | bitmap-wah | bitmap-roaring |
|---|---|---|---|---|---|---|
| 0.001 | 0.0087 | 76.0 (10.39 / 10.44) | 23.0 (0.24 / 0.24) | 27.0 (0.20 / 0.20) | 27.0 (0.21 / 0.21) | 27.0 (0.19 / 0.19) |
| 0.010 | 0.0113 | 76.0 (10.48 / 10.64) | 28.5 (0.29 / 0.30) | 32.5 (0.26 / 0.26) | 32.5 (0.26 / 0.26) | 32.5 (0.25 / 0.25) |
| 0.100 | 0.0907 | 76.0 (10.42 / 10.61) | 77.5 (2.00 / 2.01) | 80.5 (1.84 / 1.93) | 80.5 (1.89 / 1.91) | 80.5 (1.74 / 1.85) |
| 0.500 | 0.1013 | 76.0 (10.90 / 10.98) | 78.0 (2.01 / 2.17) | 81.0 (2.02 / 2.07) | 81.0 (2.04 / 2.24) | 81.0 (2.00 / 2.01) |

### `point:size`

| target sel. | actual sel. | seqscan | btree |
|---|---|---|---|
| 0.001 | 0.0010 | 76.0 (8.27 / 8.29) | 5.0 (0.15 / 0.15) |
| 0.010 | 0.0243 | 76.0 (7.80 / 7.97) | 52.0 (0.46 / 0.47) |
| 0.100 | 0.0243 | 76.0 (7.91 / 7.99) | 52.0 (0.46 / 0.47) |
| 0.500 | 0.0243 | 76.0 (8.16 / 8.34) | 52.0 (0.49 / 0.50) |

### `point:uid`

| target sel. | actual sel. | seqscan | btree | bitmap-plain | bitmap-wah | bitmap-roaring |
|---|---|---|---|---|---|---|
| 0.001 | 0.0827 | 76.0 (7.82 / 7.97) | 76.0 (1.81 / 1.81) | 80.0 (1.62 / 1.70) | 80.0 (1.77 / 1.78) | 80.0 (1.73 / 1.82) |
| 0.010 | 0.0827 | 76.0 (8.20 / 8.28) | 76.0 (1.81 / 1.88) | 80.0 (1.75 / 1.89) | 80.0 (1.64 / 1.65) | 80.0 (1.62 / 1.68) |
| 0.100 | 0.0827 | 76.0 (8.19 / 8.42) | 76.0 (1.70 / 1.74) | 80.0 (1.73 / 1.80) | 80.0 (1.94 / 2.02) | 80.0 (2.03 / 2.17) |
| 0.500 | 0.5913 | 76.0 (9.27 / 9.47) | 84.0 (9.88 / 10.72) | 82.0 (8.57 / 9.02) | 82.0 (9.31 / 10.79) | 82.0 (8.90 / 9.08) |

### `range:depth`

| target sel. | actual sel. | seqscan | btree | bitmap-plain | bitmap-wah | bitmap-roaring |
|---|---|---|---|---|---|---|
| 0.001 | 0.1140 | 76.0 (8.83 / 8.97) | 72.5 (2.30 / 2.33) | 75.5 (2.12 / 2.42) | 75.5 (2.34 / 2.46) | 75.5 (2.06 / 2.40) |
| 0.010 | 0.1108 | 76.0 (8.81 / 8.89) | 74.5 (2.23 / 2.48) | 77.5 (2.18 / 2.27) | 77.5 (2.30 / 2.30) | 77.5 (1.99 / 2.15) |
| 0.100 | 0.2648 | 76.0 (8.73 / 8.88) | 81.0 (4.77 / 5.19) | 82.5 (4.07 / 4.35) | 82.5 (4.44 / 4.49) | 82.5 (4.51 / 4.62) |
| 0.500 | 0.6472 | 76.0 (8.72 / 9.15) | 85.0 (10.68 / 11.58) | 85.0 (10.28 / 10.53) | 85.0 (10.95 / 11.64) | 85.0 (11.38 / 11.72) |

### `range:mtime`

| target sel. | actual sel. | seqscan | btree |
|---|---|---|---|
| 0.001 | 0.0010 | 76.0 (8.09 / 8.19) | 5.0 (0.16 / 0.16) |
| 0.010 | 0.0100 | 76.0 (8.39 / 8.62) | 26.5 (0.32 / 0.32) |
| 0.100 | 0.1000 | 76.0 (8.29 / 8.46) | 76.5 (2.23 / 2.33) |
| 0.500 | 0.5000 | 76.0 (8.64 / 8.91) | 83.5 (9.21 / 9.64) |

### `range:size`

| target sel. | actual sel. | seqscan | btree |
|---|---|---|---|
| 0.001 | 0.0127 | 76.0 (8.81 / 9.12) | 28.5 (0.32 / 0.34) |
| 0.010 | 0.0100 | 76.0 (9.28 / 10.02) | 27.0 (0.31 / 0.32) |
| 0.100 | 0.1002 | 76.0 (8.50 / 8.59) | 77.0 (2.12 / 2.26) |
| 0.500 | 0.5002 | 76.0 (8.39 / 8.56) | 84.0 (8.66 / 9.13) |

### `term`

| target sel. | actual sel. | seqscan | inverted | inverted-raw |
|---|---|---|---|---|
| 0.001 | 0.0010 | 76.0 (15.73 / 15.92) | 4.0 (0.09 / 0.09) | 4.0 (0.08 / 0.09) |
| 0.010 | 0.0090 | 76.0 (16.24 / 16.61) | 24.5 (0.30 / 0.40) | 24.5 (0.25 / 0.32) |
| 0.100 | 0.0932 | 76.0 (16.42 / 16.49) | 75.0 (2.60 / 2.65) | 76.0 (2.42 / 2.73) |
| 0.500 | 0.1993 | 76.0 (16.47 / 17.25) | 77.0 (4.97 / 5.19) | 78.0 (5.14 / 5.23) |
