# Benchmark: cardinality-generator.num_users=100

- Dataset: 100,000 rows (synthetic, seed 0), 2,509 heap pages
- Workload: 20 queries (query seed 0)
- Buffer pool: 256 pages. Page I/O is counted on one run per query from a cold cache; latency is the median and 95th percentile of up to 50 warm runs after 1 warm-up run(s) (at least 5, fewer than 50 once 2 s is spent)
- Index results exclude fetching the matching records from the heap
- I/O cost = sequential reads + k x random reads, for k in 1, 4, 10
- Run at 2026-11-16T06:55:50+00:00 on commit e08b05d, Python 3.13.9

## Correctness

80 of 80 query results match the sequential scan (and the workload's expected counts).
After 200 inserts, 60 results were checked again: 0 mismatches.

## Build and storage

| method | build time (s) | pages written | size (pages) | size |
|---|---|---|---|---|
| seqscan | 1.77 | 2,509 | 2,509 | 9.80 MB |
| bitmap-plain | 0.13 | 551 | 549 | 1.77 MB |
| bitmap-wah | 0.13 | 280 | 278 | 813.58 KB |
| bitmap-roaring | 0.12 | 267 | 265 | 705.70 KB |

## Inserts (200 rows)

The seqscan row is the heap insert alone; every index pays it too.

| method | ms / insert | page reads / insert | page writes / insert |
|---|---|---|---|
| seqscan | 0.013 | 0.005 | 0.03 |
| bitmap-plain | 0.074 | 0.67 | 0.67 |
| bitmap-wah | 0.287 | 0.32 | 0.32 |
| bitmap-roaring | 1.898 | 0.25 | 0.25 |

## Measured versus estimated page I/O

Each method's analytical model of its cold-cache page reads (see `dbmsi/bench/methods.py`) against the measured count. *error* compares the totals over all queries of a spec; *within 5%* is the share of single queries within 5% of their estimate. Over all queries: 56,319 measured, 56,282 estimated (+0.1%).

| method | spec | queries | measured | estimated | error | within 5% |
|---|---|---|---|---|---|---|
| seqscan | point:uid | 20 | 50,180 | 50,180.0 | +0.0% | 100% |
| bitmap-plain | point:uid | 20 | 2,074 | 2,061.6 | +0.6% | 90% |
| bitmap-wah | point:uid | 20 | 2,034 | 2,021.6 | +0.6% | 90% |
| bitmap-roaring | point:uid | 20 | 2,031 | 2,018.6 | +0.6% | 90% |

## Queries

Mean physical page reads per query, cold cache (median and 95th percentile warm latency in ms in parentheses). A dash means the method does not support the query. Full I/O costs for each k are in `summary.csv`.

### `point:uid`

| target sel. | actual sel. | seqscan | bitmap-plain | bitmap-wah | bitmap-roaring |
|---|---|---|---|---|---|
| 0.0001 | 0.0003 | 2,509.0 (189.68 / 201.96) | 33.0 (0.70 / 0.79) | 30.0 (0.10 / 0.24) | 30.0 (0.10 / 0.10) |
| 0.001 | 0.0010 | 2,509.0 (195.31 / 197.28) | 80.2 (0.59 / 0.93) | 77.2 (0.27 / 0.45) | 77.2 (0.26 / 0.39) |
| 0.010 | 0.0094 | 2,509.0 (196.73 / 203.48) | 150.6 (1.06 / 1.84) | 148.6 (0.95 / 1.71) | 147.6 (0.73 / 1.26) |
| 0.100 | 0.1060 | 2,509.0 (203.29 / 216.34) | 151.0 (5.00 / 7.54) | 151.0 (5.58 / 8.31) | 151.4 (5.49 / 7.84) |
