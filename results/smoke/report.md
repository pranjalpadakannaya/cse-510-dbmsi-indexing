# Benchmark: smoke

- Dataset: 3,000 rows (synthetic, seed 0), 76 heap pages
- Workload: 72 queries (query seed 0)
- Buffer pool: 64 pages. Page I/O is counted on one run per query from a cold cache; latency is the median and 95th percentile of up to 3 warm runs after 1 warm-up run(s) (at least 1, fewer than 3 once 2 s is spent)
- Index results include fetching the matching records from the heap (in RID order)
- I/O cost = sequential reads + k x random reads, for k in 1, 4, 10
- Run at 2026-11-16T06:54:31+00:00 on commit e08b05d, Python 3.13.9

## Correctness

240 of 240 query results match the sequential scan (and the workload's expected counts).
After 30 inserts, 168 results were checked again: 0 mismatches.

## Build and storage

| method | build time (s) | pages written | size (pages) | size |
|---|---|---|---|---|
| seqscan | 0.03 | 76 | 76 | 304.00 KB |
| btree | 0.0094 | 71 | 71 | 284.00 KB |
| bitmap-plain | 0.0052 | 49 | 49 | 35.54 KB |
| bitmap-wah | 0.0056 | 49 | 49 | 33.24 KB |
| bitmap-roaring | 0.0058 | 49 | 49 | 37.52 KB |
| inverted | 0.01 | 6 | 6 | 22.72 KB |
| inverted-raw | 0.0099 | 19 | 19 | 73.16 KB |

## Inserts (30 rows)

The seqscan row is the heap insert alone; every index pays it too.

| method | ms / insert | page reads / insert | page writes / insert |
|---|---|---|---|
| seqscan | 0.013 | 0.03 | 0.07 |
| btree | 0.182 | 1.67 | 1.50 |
| bitmap-plain | 0.022 | 1.00 | 1.00 |
| bitmap-wah | 0.041 | 1.00 | 1.00 |
| bitmap-roaring | 0.265 | 1.00 | 1.00 |
| inverted | 0.319 | 0.20 | 0.20 |
| inverted-raw | 0.210 | 0.60 | 0.83 |

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
| global | 0 | 1 | 32,596 | 1.00x | 12 | 0 |
| global | 0 | 4 | 32,285 | 0.99x | 12 | 0 |
| crabbing | 0 | 1 | 29,670 | 1.00x | 12 | 0 |
| crabbing | 0 | 4 | 29,383 | 0.99x | 12 | 0 |
| global | 0.20 | 1 | 31,239 | 1.00x | 12 | 0 |
| global | 0.20 | 4 | 31,570 | 1.01x | 12 | 0 |
| crabbing | 0.20 | 1 | 28,047 | 1.00x | 12 | 0 |
| crabbing | 0.20 | 4 | 25,557 | 0.91x | 12 | 0 |

Crabbing against the global latch at the same latency and thread count:

| read latency (ms) | threads | global ops / s | crabbing ops / s | crabbing / global |
|---|---|---|---|---|
| 0 | 1 | 32,596 | 29,670 | 0.91x |
| 0 | 4 | 32,285 | 29,383 | 0.91x |
| 0.20 | 1 | 31,239 | 28,047 | 0.90x |
| 0.20 | 4 | 31,570 | 25,557 | 0.81x |

## Posting list decoding

Median time spent decoding posting lists per query (warm runs), against the whole query.

| spec | target sel. | method | decode (ms) | query (ms) | decode share |
|---|---|---|---|---|---|
| and | 0.001 | inverted | 0.099 | 0.136 | 73% |
| and | 0.001 | inverted-raw | 0.080 | 0.115 | 69% |
| and | 0.0100 | inverted | 0.261 | 0.362 | 72% |
| and | 0.0100 | inverted-raw | 0.210 | 0.310 | 68% |
| and | 0.1000 | inverted | 0.348 | 0.525 | 66% |
| and | 0.1000 | inverted-raw | 0.288 | 0.460 | 63% |
| and | 0.5000 | inverted | 0.344 | 0.531 | 65% |
| and | 0.5000 | inverted-raw | 0.292 | 0.469 | 62% |
| or | 0.001 | inverted | 0.0019 | 0.030 | 6% |
| or | 0.001 | inverted-raw | 0.0013 | 0.030 | 4% |
| or | 0.0100 | inverted | 0.014 | 0.091 | 15% |
| or | 0.0100 | inverted-raw | 0.010 | 0.087 | 12% |
| or | 0.1000 | inverted | 0.125 | 0.858 | 15% |
| or | 0.1000 | inverted-raw | 0.095 | 0.840 | 11% |
| or | 0.5000 | inverted | 0.342 | 2.107 | 16% |
| or | 0.5000 | inverted-raw | 0.286 | 2.029 | 14% |
| term | 0.001 | inverted | 0.0022 | 0.030 | 7% |
| term | 0.001 | inverted-raw | 0.0015 | 0.030 | 5% |
| term | 0.0100 | inverted | 0.013 | 0.089 | 14% |
| term | 0.0100 | inverted-raw | 0.0095 | 0.084 | 11% |
| term | 0.1000 | inverted | 0.115 | 0.817 | 14% |
| term | 0.1000 | inverted-raw | 0.090 | 0.797 | 11% |
| term | 0.5000 | inverted | 0.231 | 1.500 | 15% |
| term | 0.5000 | inverted-raw | 0.198 | 1.500 | 13% |

## Queries

Mean physical page reads per query, cold cache (median and 95th percentile warm latency in ms in parentheses). A dash means the method does not support the query. Full I/O costs for each k are in `summary.csv`.

### `and`

| target sel. | actual sel. | seqscan | inverted | inverted-raw |
|---|---|---|---|---|
| 0.001 | 0.0010 | 76.0 (5.73 / 5.74) | 5.0 (0.14 / 0.14) | 6.0 (0.12 / 0.12) |
| 0.010 | 0.0117 | 76.0 (5.74 / 5.76) | 31.5 (0.36 / 0.37) | 33.0 (0.31 / 0.32) |
| 0.100 | 0.0240 | 76.0 (5.78 / 5.79) | 51.0 (0.53 / 0.54) | 52.0 (0.46 / 0.46) |
| 0.500 | 0.0240 | 76.0 (5.78 / 5.81) | 51.0 (0.53 / 0.53) | 52.0 (0.47 / 0.47) |

### `or`

| target sel. | actual sel. | seqscan | inverted | inverted-raw |
|---|---|---|---|---|
| 0.001 | 0.0007 | 76.0 (5.84 / 5.86) | 3.5 (0.03 / 0.03) | 3.5 (0.03 / 0.03) |
| 0.010 | 0.0098 | 76.0 (5.86 / 5.87) | 26.0 (0.09 / 0.09) | 26.0 (0.09 / 0.09) |
| 0.100 | 0.0988 | 76.0 (5.95 / 5.96) | 77.0 (0.86 / 0.87) | 77.0 (0.84 / 0.85) |
| 0.500 | 0.2770 | 76.0 (6.00 / 6.01) | 78.0 (2.11 / 2.12) | 80.0 (2.03 / 2.04) |

### `point:extension`

| target sel. | actual sel. | seqscan | btree | bitmap-plain | bitmap-wah | bitmap-roaring |
|---|---|---|---|---|---|---|
| 0.001 | 0.0087 | 76.0 (4.53 / 4.54) | 23.0 (0.11 / 0.11) | 27.0 (0.09 / 0.09) | 27.0 (0.09 / 0.09) | 27.0 (0.08 / 0.09) |
| 0.010 | 0.0113 | 76.0 (4.51 / 4.51) | 28.5 (0.12 / 0.12) | 32.5 (0.11 / 0.11) | 32.5 (0.11 / 0.11) | 32.5 (0.11 / 0.11) |
| 0.100 | 0.0907 | 76.0 (4.57 / 4.58) | 77.5 (0.83 / 0.83) | 80.5 (0.77 / 0.78) | 80.5 (0.79 / 0.79) | 80.5 (0.77 / 0.78) |
| 0.500 | 0.1013 | 76.0 (4.57 / 4.68) | 78.0 (0.88 / 0.91) | 81.0 (0.85 / 0.86) | 81.0 (0.86 / 0.89) | 81.0 (0.86 / 0.87) |

### `point:size`

| target sel. | actual sel. | seqscan | btree |
|---|---|---|---|
| 0.001 | 0.0010 | 76.0 (3.59 / 3.64) | 5.0 (0.07 / 0.07) |
| 0.010 | 0.0243 | 76.0 (3.54 / 3.56) | 52.0 (0.22 / 0.23) |
| 0.100 | 0.0243 | 76.0 (3.55 / 3.56) | 52.0 (0.21 / 0.22) |
| 0.500 | 0.0243 | 76.0 (3.49 / 3.49) | 52.0 (0.22 / 0.23) |

### `point:uid`

| target sel. | actual sel. | seqscan | btree | bitmap-plain | bitmap-wah | bitmap-roaring |
|---|---|---|---|---|---|---|
| 0.001 | 0.0827 | 76.0 (3.48 / 3.58) | 76.0 (0.74 / 0.75) | 80.0 (0.75 / 0.76) | 80.0 (0.75 / 0.77) | 80.0 (0.74 / 0.76) |
| 0.010 | 0.0827 | 76.0 (3.51 / 3.55) | 76.0 (0.74 / 0.75) | 80.0 (0.73 / 0.76) | 80.0 (0.71 / 0.73) | 80.0 (0.72 / 0.73) |
| 0.100 | 0.0827 | 76.0 (3.59 / 3.62) | 76.0 (0.76 / 0.76) | 80.0 (0.73 / 0.73) | 80.0 (0.74 / 0.74) | 80.0 (0.71 / 0.72) |
| 0.500 | 0.5913 | 76.0 (3.61 / 3.64) | 84.0 (4.27 / 4.29) | 82.0 (3.92 / 3.94) | 82.0 (4.02 / 4.05) | 82.0 (4.01 / 4.03) |

### `range:depth`

| target sel. | actual sel. | seqscan | btree | bitmap-plain | bitmap-wah | bitmap-roaring |
|---|---|---|---|---|---|---|
| 0.001 | 0.1140 | 76.0 (3.77 / 3.81) | 72.5 (1.00 / 1.01) | 75.5 (0.92 / 0.92) | 75.5 (0.97 / 0.99) | 75.5 (0.89 / 0.89) |
| 0.010 | 0.1108 | 76.0 (3.77 / 3.79) | 74.5 (0.99 / 0.99) | 77.5 (0.90 / 0.92) | 77.5 (0.97 / 0.97) | 77.5 (0.89 / 0.90) |
| 0.100 | 0.2648 | 76.0 (3.81 / 3.82) | 81.0 (2.07 / 2.07) | 82.5 (1.89 / 1.90) | 82.5 (2.00 / 2.01) | 82.5 (1.93 / 1.95) |
| 0.500 | 0.6472 | 76.0 (3.92 / 3.95) | 85.0 (4.85 / 4.85) | 85.0 (4.30 / 4.44) | 85.0 (4.60 / 4.62) | 85.0 (5.04 / 5.13) |

### `range:mtime`

| target sel. | actual sel. | seqscan | btree |
|---|---|---|---|
| 0.001 | 0.0010 | 76.0 (3.55 / 3.58) | 5.0 (0.07 / 0.07) |
| 0.010 | 0.0100 | 76.0 (3.58 / 3.59) | 26.5 (0.13 / 0.13) |
| 0.100 | 0.1000 | 76.0 (3.59 / 3.61) | 76.5 (0.95 / 0.95) |
| 0.500 | 0.5000 | 76.0 (3.64 / 3.65) | 83.5 (3.89 / 3.91) |

### `range:size`

| target sel. | actual sel. | seqscan | btree |
|---|---|---|---|
| 0.001 | 0.0127 | 76.0 (3.54 / 3.59) | 28.5 (0.14 / 0.15) |
| 0.010 | 0.0100 | 76.0 (3.56 / 3.57) | 27.0 (0.12 / 0.13) |
| 0.100 | 0.1002 | 76.0 (3.56 / 3.59) | 77.0 (0.90 / 0.90) |
| 0.500 | 0.5002 | 76.0 (3.68 / 3.70) | 84.0 (3.73 / 3.75) |

### `term`

| target sel. | actual sel. | seqscan | inverted | inverted-raw |
|---|---|---|---|---|
| 0.001 | 0.0010 | 76.0 (5.85 / 5.86) | 4.0 (0.03 / 0.03) | 4.0 (0.03 / 0.03) |
| 0.010 | 0.0090 | 76.0 (5.80 / 5.84) | 24.5 (0.09 / 0.09) | 24.5 (0.08 / 0.09) |
| 0.100 | 0.0932 | 76.0 (5.85 / 5.88) | 75.0 (0.82 / 0.82) | 76.0 (0.80 / 0.81) |
| 0.500 | 0.1993 | 76.0 (5.76 / 5.87) | 77.0 (1.50 / 1.51) | 78.0 (1.50 / 1.51) |
