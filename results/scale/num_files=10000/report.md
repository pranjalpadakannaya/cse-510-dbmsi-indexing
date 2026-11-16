# Benchmark: scale-num_files=10000

- Dataset: 10,000 rows (synthetic, seed 0), 251 heap pages
- Workload: 75 queries (query seed 0)
- Buffer pool: 256 pages. Page I/O is counted on one run per query from a cold cache; latency is the median and 95th percentile of up to 50 warm runs after 1 warm-up run(s) (at least 3, fewer than 50 once 1 s is spent)
- Index results include fetching the matching records from the heap (in RID order)
- I/O cost = sequential reads + k x random reads, for k in 1, 4, 10
- Run at 2026-11-16T06:56:28+00:00 on commit e08b05d, Python 3.13.9

## Correctness

210 of 210 query results match the sequential scan (and the workload's expected counts).
After 200 inserts, 135 results were checked again: 0 mismatches.

## Build and storage

| method | build time (s) | pages written | size (pages) | size |
|---|---|---|---|---|
| seqscan | 0.17 | 251 | 251 | 1004.00 KB |
| btree | 0.03 | 94 | 94 | 376.00 KB |
| bitmap-wah | 0.01 | 46 | 46 | 88.37 KB |
| bitmap-roaring | 0.02 | 46 | 46 | 79.90 KB |
| inverted | 0.04 | 20 | 20 | 78.81 KB |
| inverted-raw | 0.04 | 62 | 62 | 245.92 KB |

## Inserts (200 rows)

The seqscan row is the heap insert alone; every index pays it too.

| method | ms / insert | page reads / insert | page writes / insert |
|---|---|---|---|
| seqscan | 0.014 | 0.005 | 0.03 |
| btree | 0.104 | 0.30 | 0.45 |
| bitmap-wah | 0.035 | 0.15 | 0.15 |
| bitmap-roaring | 0.101 | 0.15 | 0.15 |
| inverted | 1.101 | 0.10 | 0.46 |
| inverted-raw | 0.841 | 0.39 | 2.21 |

## Measured versus estimated page I/O

Each method's analytical model of its cold-cache page reads (see `dbmsi/bench/methods.py`) against the measured count. *error* compares the totals over all queries of a spec; *within 5%* is the share of single queries within 5% of their estimate. Over all queries: 35,415 measured, 35,310 estimated (+0.3%).

| method | spec | queries | measured | estimated | error | within 5% |
|---|---|---|---|---|---|---|
| seqscan | and | 15 | 3,765 | 3,765.0 | +0.0% | 100% |
| seqscan | point:extension | 15 | 3,765 | 3,765.0 | +0.0% | 100% |
| seqscan | point:size | 15 | 3,765 | 3,765.0 | +0.0% | 100% |
| seqscan | range:size | 15 | 3,765 | 3,765.0 | +0.0% | 100% |
| seqscan | term | 15 | 3,765 | 3,765.0 | +0.0% | 100% |
| btree | point:extension | 15 | 2,401 | 2,379.0 | +0.9% | 100% |
| btree | point:size | 15 | 1,394 | 1,348.6 | +3.4% | 93% |
| btree | range:size | 15 | 1,864 | 1,871.6 | -0.4% | 93% |
| bitmap-wah | point:extension | 15 | 2,574 | 2,550.7 | +0.9% | 100% |
| bitmap-roaring | point:extension | 15 | 2,574 | 2,550.7 | +0.9% | 100% |
| inverted | and | 15 | 1,088 | 1,095.7 | -0.7% | 87% |
| inverted | term | 15 | 1,770 | 1,767.8 | +0.1% | 80% |
| inverted-raw | and | 15 | 1,139 | 1,138.0 | +0.1% | 87% |
| inverted-raw | term | 15 | 1,786 | 1,782.4 | +0.2% | 80% |

## Concurrent searches and inserts (B+ tree)

20,000 operations (3,949 inserts) on a `size` tree, split across the threads, from a cold pool of 256 frames. `global` takes one tree-wide latch per operation; `crabbing` latches one root-to-leaf path. *read latency* is the modeled wait per physical page read; *speedup* is against the fewest threads of the same scheme and latency.

| latching | read latency (ms) | threads | ops / s | speedup | page reads | lost inserts |
|---|---|---|---|---|---|---|
| global | 0 | 1 | 21,635 | 1.00x | 36 | 0 |
| global | 0 | 2 | 21,190 | 0.98x | 36 | 0 |
| global | 0 | 4 | 21,502 | 0.99x | 36 | 0 |
| global | 0 | 8 | 21,117 | 0.98x | 36 | 0 |
| crabbing | 0 | 1 | 19,520 | 1.00x | 36 | 0 |
| crabbing | 0 | 2 | 19,167 | 0.98x | 36 | 0 |
| crabbing | 0 | 4 | 18,499 | 0.95x | 36 | 0 |
| crabbing | 0 | 8 | 18,939 | 0.97x | 36 | 0 |

Crabbing against the global latch at the same latency and thread count:

| read latency (ms) | threads | global ops / s | crabbing ops / s | crabbing / global |
|---|---|---|---|---|
| 0 | 1 | 21,635 | 19,520 | 0.90x |
| 0 | 2 | 21,190 | 19,167 | 0.90x |
| 0 | 4 | 21,502 | 18,499 | 0.86x |
| 0 | 8 | 21,117 | 18,939 | 0.90x |

## Posting list decoding

Median time spent decoding posting lists per query (warm runs), against the whole query.

| spec | target sel. | method | decode (ms) | query (ms) | decode share |
|---|---|---|---|---|---|
| and | 0.0001 | inverted | 0.059 | 0.093 | 64% |
| and | 0.0001 | inverted-raw | 0.042 | 0.073 | 57% |
| and | 0.001 | inverted | 0.438 | 0.508 | 86% |
| and | 0.001 | inverted-raw | 0.396 | 0.471 | 84% |
| and | 0.0100 | inverted | 0.937 | 1.227 | 76% |
| and | 0.0100 | inverted-raw | 0.741 | 1.050 | 71% |
| and | 0.1000 | inverted | 1.681 | 2.405 | 70% |
| and | 0.1000 | inverted-raw | 1.404 | 2.026 | 69% |
| and | 0.5000 | inverted | 1.562 | 2.222 | 70% |
| and | 0.5000 | inverted-raw | 1.313 | 1.922 | 68% |
| term | 0.0001 | inverted | 0.0022 | 0.055 | 4% |
| term | 0.0001 | inverted-raw | 0.0015 | 0.054 | 3% |
| term | 0.001 | inverted | 0.0088 | 0.069 | 13% |
| term | 0.001 | inverted-raw | 0.0076 | 0.092 | 8% |
| term | 0.0100 | inverted | 0.054 | 0.302 | 18% |
| term | 0.0100 | inverted-raw | 0.046 | 0.322 | 14% |
| term | 0.1000 | inverted | 0.484 | 2.851 | 17% |
| term | 0.1000 | inverted-raw | 0.387 | 2.649 | 15% |
| term | 0.5000 | inverted | 1.058 | 6.027 | 18% |
| term | 0.5000 | inverted-raw | 0.898 | 5.945 | 15% |

## Queries

Mean physical page reads per query, cold cache (median and 95th percentile warm latency in ms in parentheses). A dash means the method does not support the query. Full I/O costs for each k are in `summary.csv`.

### `and`

| target sel. | actual sel. | seqscan | inverted | inverted-raw |
|---|---|---|---|---|
| 0.0001 | 0.0001 | 251.0 (24.24 / 24.87) | 3.0 (0.09 / 0.11) | 3.0 (0.07 / 0.09) |
| 0.001 | 0.0010 | 251.0 (23.84 / 24.91) | 11.3 (0.51 / 0.68) | 13.3 (0.47 / 0.56) |
| 0.010 | 0.0080 | 251.0 (25.50 / 28.13) | 71.7 (1.23 / 1.36) | 75.3 (1.05 / 1.49) |
| 0.100 | 0.0196 | 251.0 (28.83 / 36.65) | 138.3 (2.41 / 2.61) | 144.0 (2.03 / 2.70) |
| 0.500 | 0.0196 | 251.0 (27.15 / 32.08) | 138.3 (2.22 / 3.55) | 144.0 (1.92 / 3.11) |

### `point:extension`

| target sel. | actual sel. | seqscan | btree | bitmap-wah | bitmap-roaring |
|---|---|---|---|---|---|
| 0.0001 | 0.0115 | 251.0 (23.04 / 25.42) | 95.7 (0.44 / 0.70) | 109.7 (0.44 / 0.72) | 109.7 (0.39 / 0.64) |
| 0.001 | 0.0118 | 251.0 (23.37 / 26.15) | 98.0 (0.40 / 0.77) | 112.0 (0.44 / 0.79) | 112.0 (0.41 / 0.75) |
| 0.010 | 0.0111 | 251.0 (24.19 / 27.79) | 94.3 (0.46 / 0.74) | 107.7 (0.40 / 0.66) | 107.7 (0.41 / 0.63) |
| 0.100 | 0.0948 | 251.0 (24.04 / 26.26) | 255.3 (4.42 / 6.89) | 263.7 (4.33 / 6.56) | 263.7 (4.32 / 6.33) |
| 0.500 | 0.1031 | 251.0 (24.20 / 26.19) | 257.0 (4.77 / 6.91) | 265.0 (4.80 / 6.59) | 265.0 (4.42 / 6.43) |

### `point:size`

| target sel. | actual sel. | seqscan | btree |
|---|---|---|---|
| 0.0001 | 0.0001 | 251.0 (17.12 / 22.99) | 3.0 (0.07 / 0.15) |
| 0.001 | 0.0004 | 251.0 (17.71 / 21.67) | 5.7 (0.08 / 0.08) |
| 0.010 | 0.0212 | 251.0 (17.94 / 22.13) | 152.0 (0.62 / 1.24) |
| 0.100 | 0.0212 | 251.0 (18.29 / 20.00) | 152.0 (0.66 / 1.21) |
| 0.500 | 0.0212 | 251.0 (18.49 / 20.12) | 152.0 (0.66 / 1.22) |

### `range:size`

| target sel. | actual sel. | seqscan | btree |
|---|---|---|---|
| 0.0001 | 0.0001 | 251.0 (19.11 / 19.63) | 3.0 (0.08 / 0.15) |
| 0.001 | 0.0011 | 251.0 (19.08 / 19.65) | 12.7 (0.12 / 0.19) |
| 0.010 | 0.0100 | 251.0 (19.22 / 21.83) | 83.3 (0.41 / 0.64) |
| 0.100 | 0.1000 | 251.0 (19.71 / 23.56) | 251.7 (3.95 / 5.57) |
| 0.500 | 0.5000 | 251.0 (21.73 / 34.27) | 270.7 (21.41 / 25.98) |

### `term`

| target sel. | actual sel. | seqscan | inverted | inverted-raw |
|---|---|---|---|---|
| 0.0001 | 0.0001 | 251.0 (32.77 / 36.50) | 2.0 (0.06 / 0.06) | 2.0 (0.05 / 0.06) |
| 0.001 | 0.0010 | 251.0 (33.28 / 37.11) | 10.0 (0.07 / 0.10) | 10.0 (0.09 / 0.11) |
| 0.010 | 0.0092 | 251.0 (33.28 / 40.55) | 79.0 (0.30 / 0.63) | 79.0 (0.32 / 0.56) |
| 0.100 | 0.0894 | 251.0 (29.82 / 37.11) | 247.0 (2.85 / 4.97) | 248.3 (2.65 / 3.97) |
| 0.500 | 0.2007 | 251.0 (24.90 / 26.05) | 252.0 (6.03 / 6.51) | 256.0 (5.95 / 6.79) |
