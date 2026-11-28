# Benchmark: cardinality-generator.num_users=5000

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
| seqscan | 1.70 | 2,509 | 2,509 | 9.80 MB |
| bitmap-plain | 0.22 | 8,184 | 8,182 | 24.50 MB |
| bitmap-wah | 0.15 | 2,193 | 2,191 | 870.98 KB |
| bitmap-roaring | 0.15 | 2,182 | 2,180 | 740.84 KB |

## Inserts (200 rows)

The seqscan row is the heap insert alone; every index pays it too.

| method | ms / insert | page reads / insert | page writes / insert |
|---|---|---|---|
| seqscan | 0.021 | 0.005 | 0.03 |
| bitmap-plain | 0.068 | 0.98 | 0.98 |
| bitmap-wah | 0.279 | 0.39 | 0.39 |
| bitmap-roaring | 1.689 | 0.33 | 0.33 |

## Measured versus estimated page I/O

Each method's analytical model of its cold-cache page reads (see `dbmsi/bench/methods.py`) against the measured count. *error* compares the totals over all queries of a spec; *within 5%* is the share of single queries within 5% of their estimate. Over all queries: 55,934 measured, 55,929 estimated (+0.0%).

| method | spec | queries | measured | estimated | error | within 5% |
|---|---|---|---|---|---|---|
| seqscan | point:uid | 20 | 50,180 | 50,180.0 | +0.0% | 100% |
| bitmap-plain | point:uid | 20 | 1,946 | 1,944.2 | +0.1% | 85% |
| bitmap-wah | point:uid | 20 | 1,906 | 1,904.2 | +0.1% | 85% |
| bitmap-roaring | point:uid | 20 | 1,902 | 1,900.2 | +0.1% | 85% |

## Queries

Mean physical page reads per query, cold cache (median and 95th percentile warm latency in ms in parentheses). A dash means the method does not support the query. Full I/O costs for each k are in `summary.csv`.

### `point:uid`

| target sel. | actual sel. | seqscan | bitmap-plain | bitmap-wah | bitmap-roaring |
|---|---|---|---|---|---|
| 0.0001 | 0.0001 | 2,509.0 (186.44 / 198.34) | 14.4 (0.35 / 0.63) | 11.4 (0.09 / 0.11) | 11.4 (0.05 / 0.06) |
| 0.001 | 0.0009 | 2,509.0 (192.75 / 197.62) | 72.8 (0.53 / 0.88) | 69.8 (0.27 / 0.43) | 69.8 (0.32 / 0.35) |
| 0.010 | 0.0095 | 2,509.0 (195.91 / 200.29) | 151.0 (1.08 / 1.86) | 149.0 (0.97 / 1.67) | 148.0 (0.72 / 1.24) |
| 0.100 | 0.0865 | 2,509.0 (207.54 / 220.17) | 151.0 (4.88 / 7.25) | 151.0 (4.65 / 7.43) | 151.2 (4.34 / 7.02) |
