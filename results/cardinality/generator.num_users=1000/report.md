# Benchmark: cardinality-generator.num_users=1000

- Dataset: 100,000 rows (synthetic, seed 0), 2,509 heap pages
- Workload: 20 queries (query seed 0)
- Buffer pool: 256 pages. Page I/O is counted on one run per query from a cold cache; latency is the median and 95th percentile of up to 50 warm runs after 1 warm-up run(s) (at least 5, fewer than 50 once 2 s is spent)
- Index results exclude fetching the matching records from the heap
- I/O cost = sequential reads + k x random reads, for k in 1, 4, 10
- Run at 2026-11-16T06:55:48+00:00 on commit e08b05d, Python 3.13.9

## Correctness

80 of 80 query results match the sequential scan (and the workload's expected counts).
After 200 inserts, 60 results were checked again: 0 mismatches.

## Build and storage

| method | build time (s) | pages written | size (pages) | size |
|---|---|---|---|---|
| seqscan | 1.65 | 2,509 | 2,509 | 9.80 MB |
| bitmap-plain | 0.18 | 3,831 | 3,829 | 11.53 MB |
| bitmap-wah | 0.14 | 1,101 | 1,099 | 854.04 KB |
| bitmap-roaring | 0.13 | 1,090 | 1,088 | 725.04 KB |

## Inserts (200 rows)

The seqscan row is the heap insert alone; every index pays it too.

| method | ms / insert | page reads / insert | page writes / insert |
|---|---|---|---|
| seqscan | 0.024 | 0.005 | 0.03 |
| bitmap-plain | 0.083 | 0.89 | 0.89 |
| bitmap-wah | 0.281 | 0.36 | 0.36 |
| bitmap-roaring | 1.894 | 0.30 | 0.30 |

## Measured versus estimated page I/O

Each method's analytical model of its cold-cache page reads (see `dbmsi/bench/methods.py`) against the measured count. *error* compares the totals over all queries of a spec; *within 5%* is the share of single queries within 5% of their estimate. Over all queries: 56,044 measured, 56,047 estimated (-0.0%).

| method | spec | queries | measured | estimated | error | within 5% |
|---|---|---|---|---|---|---|
| seqscan | point:uid | 20 | 50,180 | 50,180.0 | +0.0% | 100% |
| bitmap-plain | point:uid | 20 | 1,983 | 1,984.0 | -0.0% | 90% |
| bitmap-wah | point:uid | 20 | 1,943 | 1,944.0 | -0.1% | 90% |
| bitmap-roaring | point:uid | 20 | 1,938 | 1,939.0 | -0.1% | 90% |

## Queries

Mean physical page reads per query, cold cache (median and 95th percentile warm latency in ms in parentheses). A dash means the method does not support the query. Full I/O costs for each k are in `summary.csv`.

### `point:uid`

| target sel. | actual sel. | seqscan | bitmap-plain | bitmap-wah | bitmap-roaring |
|---|---|---|---|---|---|
| 0.0001 | 0.0001 | 2,509.0 (185.29 / 197.70) | 13.4 (0.34 / 0.63) | 10.4 (0.08 / 0.10) | 10.4 (0.04 / 0.06) |
| 0.001 | 0.0011 | 2,509.0 (193.50 / 199.00) | 81.4 (0.56 / 0.94) | 78.4 (0.30 / 0.49) | 78.4 (0.25 / 0.41) |
| 0.010 | 0.0097 | 2,509.0 (196.08 / 200.58) | 150.8 (1.09 / 1.83) | 148.8 (1.02 / 1.66) | 147.8 (0.71 / 1.23) |
| 0.100 | 0.0746 | 2,509.0 (206.73 / 237.03) | 151.0 (4.68 / 7.18) | 151.0 (4.81 / 7.83) | 151.0 (4.66 / 6.96) |
