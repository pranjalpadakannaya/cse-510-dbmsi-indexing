# Benchmark: cardinality-generator.num_users=10

- Dataset: 100,000 rows (synthetic, seed 0), 2,509 heap pages
- Workload: 20 queries (query seed 0)
- Buffer pool: 256 pages. Page I/O is counted on one run per query from a cold cache; latency is the median and 95th percentile of up to 50 warm runs after 1 warm-up run(s) (at least 5, fewer than 50 once 2 s is spent)
- Index results exclude fetching the matching records from the heap
- I/O cost = sequential reads + k x random reads, for k in 1, 4, 10
- Run at 2026-11-16T06:55:51+00:00 on commit e08b05d, Python 3.13.9

## Correctness

80 of 80 query results match the sequential scan (and the workload's expected counts).
After 200 inserts, 60 results were checked again: 0 mismatches.

## Build and storage

| method | build time (s) | pages written | size (pages) | size |
|---|---|---|---|---|
| seqscan | 1.73 | 2,509 | 2,509 | 9.80 MB |
| bitmap-plain | 0.12 | 189 | 189 | 710.11 KB |
| bitmap-wah | 0.13 | 182 | 182 | 698.05 KB |
| bitmap-roaring | 0.12 | 177 | 177 | 678.69 KB |

## Inserts (200 rows)

The seqscan row is the heap insert alone; every index pays it too.

| method | ms / insert | page reads / insert | page writes / insert |
|---|---|---|---|
| seqscan | 0.015 | 0.005 | 0.03 |
| bitmap-plain | 0.059 | 0.20 | 0.20 |
| bitmap-wah | 0.348 | 0.17 | 0.17 |
| bitmap-roaring | 2.657 | 0.14 | 0.14 |

## Measured versus estimated page I/O

Each method's analytical model of its cold-cache page reads (see `dbmsi/bench/methods.py`) against the measured count. *error* compares the totals over all queries of a spec; *within 5%* is the share of single queries within 5% of their estimate. Over all queries: 59,169 measured, 59,169 estimated (+0.0%).

| method | spec | queries | measured | estimated | error | within 5% |
|---|---|---|---|---|---|---|
| seqscan | point:uid | 20 | 50,180 | 50,180.0 | +0.0% | 100% |
| bitmap-plain | point:uid | 20 | 3,020 | 3,020.0 | +0.0% | 100% |
| bitmap-wah | point:uid | 20 | 2,994 | 2,994.0 | +0.0% | 100% |
| bitmap-roaring | point:uid | 20 | 2,975 | 2,975.0 | +0.0% | 100% |

## Queries

Mean physical page reads per query, cold cache (median and 95th percentile warm latency in ms in parentheses). A dash means the method does not support the query. Full I/O costs for each k are in `summary.csv`.

### `point:uid`

| target sel. | actual sel. | seqscan | bitmap-plain | bitmap-wah | bitmap-roaring |
|---|---|---|---|---|---|
| 0.0001 | 0.0163 | 2,509.0 (190.49 / 195.05) | 151.0 (1.23 / 2.54) | 149.2 (1.33 / 2.44) | 148.0 (0.91 / 1.84) |
| 0.001 | 0.0168 | 2,509.0 (197.04 / 206.74) | 151.0 (1.39 / 2.35) | 149.4 (1.49 / 2.35) | 148.0 (1.04 / 1.78) |
| 0.010 | 0.0163 | 2,509.0 (199.50 / 204.49) | 151.0 (1.33 / 2.37) | 149.2 (1.42 / 2.36) | 148.0 (1.05 / 1.75) |
| 0.100 | 0.0978 | 2,509.0 (211.42 / 234.25) | 151.0 (5.62 / 8.83) | 151.0 (6.38 / 9.67) | 151.0 (5.46 / 8.94) |
