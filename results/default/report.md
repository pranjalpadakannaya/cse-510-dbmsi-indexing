# Benchmark: default

- Dataset: 100,000 rows (synthetic, seed 0), 2,509 heap pages
- Workload: 200 queries (query seed 0)
- Buffer pool: 256 pages. Page I/O is counted on one run per query from a cold cache; latency is the median and 95th percentile of up to 50 warm runs after 1 warm-up run(s) (at least 5, fewer than 50 once 2 s is spent)
- Index results include fetching the matching records from the heap (in RID order)
- I/O cost = sequential reads + k x random reads, for k in 3.08 (measured: a random read takes 90 us and a sequential read 29 us on macOS-26.6.2-arm64-arm-64bit-Mach-O)
- Run at 2026-11-25T05:15:45+00:00 on commit 90d7512, Python 3.15.2

## Correctness

625 of 625 query results match the sequential scan (and the workload's expected counts).
After 200 inserts, 425 results were checked again: 0 mismatches.

## Build and storage

| method | build time (s) | pages written | size (pages) | size |
|---|---|---|---|---|
| seqscan | 4.14 | 2,509 | 2,509 | 9.80 MB |
| btree | 1.60 | 1,792 | 1,788 | 6.98 MB |
| bitmap-plain | 0.71 | 307 | 305 | 1.04 MB |
| bitmap-wah | 0.77 | 272 | 270 | 980.02 KB |
| bitmap-roaring | 0.80 | 241 | 241 | 872.23 KB |
| inverted | 1.06 | 200 | 200 | 799.88 KB |
| inverted-raw | 0.72 | 617 | 617 | 2.41 MB |

## Inserts (200 rows)

The seqscan row is the heap insert alone; every index pays it too.

| method | ms / insert | page reads / insert | page writes / insert |
|---|---|---|---|
| seqscan | 0.0099 | 0.005 | 0.03 |
| btree | 0.237 | 2.02 | 3.25 |
| bitmap-plain | 0.069 | 0.79 | 0.79 |
| bitmap-wah | 0.383 | 0.61 | 0.61 |
| bitmap-roaring | 1.251 | 0.47 | 0.47 |
| inverted | 10.121 | 2.46 | 5.04 |
| inverted-raw | 7.851 | 15.98 | 25.14 |

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
| global | 0 | 1 | 15,789 | 1.00x | 12,081 | 0 |
| global | 0 | 2 | 16,199 | 1.03x | 12,177 | 0 |
| global | 0 | 4 | 16,016 | 1.01x | 12,125 | 0 |
| global | 0 | 8 | 15,710 | 0.99x | 12,140 | 0 |
| global | 0 | 16 | 14,988 | 0.95x | 12,130 | 0 |
| crabbing | 0 | 1 | 14,746 | 1.00x | 12,081 | 0 |
| crabbing | 0 | 2 | 14,965 | 1.01x | 12,184 | 0 |
| crabbing | 0 | 4 | 14,707 | 1.00x | 12,092 | 0 |
| crabbing | 0 | 8 | 14,981 | 1.02x | 12,169 | 0 |
| crabbing | 0 | 16 | 14,884 | 1.01x | 12,180 | 0 |
| global | 0.09 | 1 | 7,360 | 1.00x | 12,081 | 0 |
| global | 0.09 | 2 | 7,144 | 0.97x | 12,139 | 0 |
| global | 0.09 | 4 | 7,042 | 0.96x | 12,088 | 0 |
| global | 0.09 | 8 | 7,013 | 0.95x | 12,077 | 0 |
| global | 0.09 | 16 | 6,950 | 0.94x | 12,048 | 0 |
| crabbing | 0.09 | 1 | 7,077 | 1.00x | 12,081 | 0 |
| crabbing | 0.09 | 2 | 11,663 | 1.65x | 12,097 | 0 |
| crabbing | 0.09 | 4 | 13,609 | 1.92x | 12,137 | 0 |
| crabbing | 0.09 | 8 | 13,445 | 1.90x | 12,116 | 0 |
| crabbing | 0.09 | 16 | 13,242 | 1.87x | 12,124 | 0 |

Crabbing against the global latch at the same latency and thread count:

| read latency (ms) | threads | global ops / s | crabbing ops / s | crabbing / global |
|---|---|---|---|---|
| 0 | 1 | 15,789 | 14,746 | 0.93x |
| 0 | 2 | 16,199 | 14,965 | 0.92x |
| 0 | 4 | 16,016 | 14,707 | 0.92x |
| 0 | 8 | 15,710 | 14,981 | 0.95x |
| 0 | 16 | 14,988 | 14,884 | 0.99x |
| 0.09 | 1 | 7,360 | 7,077 | 0.96x |
| 0.09 | 2 | 7,144 | 11,663 | 1.63x |
| 0.09 | 4 | 7,042 | 13,609 | 1.93x |
| 0.09 | 8 | 7,013 | 13,445 | 1.92x |
| 0.09 | 16 | 6,950 | 13,242 | 1.91x |

## Posting list decoding

Median time spent decoding posting lists per query (warm runs), against the whole query.

| spec | target sel. | method | decode (ms) | query (ms) | decode share |
|---|---|---|---|---|---|
| and | 0.0001 | inverted | 4.334 | 4.914 | 88% |
| and | 0.0001 | inverted-raw | 3.595 | 4.001 | 90% |
| and | 0.001 | inverted | 17.147 | 19.373 | 89% |
| and | 0.001 | inverted-raw | 14.297 | 16.310 | 88% |
| and | 0.0100 | inverted | 38.506 | 56.133 | 69% |
| and | 0.0100 | inverted-raw | 32.752 | 50.382 | 65% |
| and | 0.1000 | inverted | 63.730 | 99.317 | 64% |
| and | 0.1000 | inverted-raw | 51.966 | 89.257 | 58% |
| and | 0.5000 | inverted | 63.572 | 99.792 | 64% |
| and | 0.5000 | inverted-raw | 53.807 | 90.618 | 59% |
| or | 0.0001 | inverted | 0.029 | 0.251 | 12% |
| or | 0.0001 | inverted-raw | 0.013 | 0.148 | 9% |
| or | 0.001 | inverted | 0.232 | 1.053 | 22% |
| or | 0.001 | inverted-raw | 0.175 | 0.969 | 18% |
| or | 0.0100 | inverted | 2.022 | 17.928 | 11% |
| or | 0.0100 | inverted-raw | 1.595 | 18.202 | 9% |
| or | 0.1000 | inverted | 19.483 | 122.107 | 16% |
| or | 0.1000 | inverted-raw | 17.755 | 129.604 | 14% |
| or | 0.5000 | inverted | 65.731 | 306.403 | 21% |
| or | 0.5000 | inverted-raw | 48.358 | 286.761 | 17% |
| term | 0.0001 | inverted | 0.014 | 0.124 | 11% |
| term | 0.0001 | inverted-raw | 0.011 | 0.119 | 9% |
| term | 0.001 | inverted | 0.189 | 1.033 | 18% |
| term | 0.001 | inverted-raw | 0.131 | 0.872 | 15% |
| term | 0.0100 | inverted | 1.692 | 16.074 | 11% |
| term | 0.0100 | inverted-raw | 1.337 | 15.332 | 9% |
| term | 0.1000 | inverted | 17.242 | 106.444 | 16% |
| term | 0.1000 | inverted-raw | 13.974 | 100.328 | 14% |
| term | 0.5000 | inverted | 37.212 | 204.504 | 18% |
| term | 0.5000 | inverted-raw | 32.707 | 213.065 | 15% |

## Queries

Mean physical page reads per query, cold cache (median and 95th percentile warm latency in ms in parentheses). A dash means the method does not support the query. Full I/O costs for each k are in `summary.csv`.

### `and`

| target sel. | actual sel. | seqscan | inverted | inverted-raw |
|---|---|---|---|---|
| 0.0001 | 0.0001 | 2,509.0 (795.71 / 861.15) | 14.2 (4.91 / 6.25) | 26.0 (4.00 / 5.39) |
| 0.001 | 0.0011 | 2,509.0 (691.25 / 696.82) | 113.0 (19.37 / 24.48) | 127.6 (16.31 / 18.12) |
| 0.010 | 0.0092 | 2,509.0 (734.18 / 837.56) | 779.6 (56.13 / 109.85) | 812.4 (50.38 / 102.34) |
| 0.100 | 0.0191 | 2,509.0 (830.61 / 862.97) | 1,360.0 (99.32 / 173.03) | 1,408.2 (89.26 / 151.15) |
| 0.500 | 0.0190 | 2,509.0 (814.98 / 824.93) | 1,359.2 (99.79 / 167.65) | 1,408.2 (90.62 / 164.99) |

### `or`

| target sel. | actual sel. | seqscan | inverted | inverted-raw |
|---|---|---|---|---|
| 0.0001 | 0.0001 | 2,509.0 (854.26 / 871.07) | 11.4 (0.25 / 0.30) | 11.6 (0.15 / 0.25) |
| 0.001 | 0.0010 | 2,509.0 (838.20 / 869.80) | 99.2 (1.05 / 1.36) | 99.0 (0.97 / 1.29) |
| 0.010 | 0.0105 | 2,509.0 (859.67 / 892.12) | 861.8 (17.93 / 21.67) | 863.2 (18.20 / 21.73) |
| 0.100 | 0.1021 | 2,509.0 (843.68 / 893.26) | 2,474.8 (122.11 / 133.41) | 2,490.0 (129.60 / 137.52) |
| 0.500 | 0.2791 | 2,509.0 (862.24 / 884.98) | 2,521.0 (306.40 / 351.29) | 2,569.8 (286.76 / 335.84) |

### `point:category`

| target sel. | actual sel. | seqscan | btree | bitmap-plain | bitmap-wah | bitmap-roaring |
|---|---|---|---|---|---|---|
| 0.0001 | 0.0109 | 2,509.0 (521.42 / 561.49) | 894.0 (15.99 / 19.33) | 1,036.0 (17.59 / 20.47) | 1,034.0 (18.36 / 21.51) | 1,033.0 (16.88 / 19.91) |
| 0.001 | 0.0109 | 2,509.0 (509.29 / 538.18) | 894.0 (16.00 / 17.49) | 1,036.0 (17.30 / 19.90) | 1,034.0 (17.41 / 18.77) | 1,033.0 (16.57 / 18.02) |
| 0.010 | 0.0109 | 2,509.0 (508.48 / 539.69) | 894.0 (15.83 / 16.88) | 1,036.0 (17.12 / 18.31) | 1,034.0 (17.23 / 18.81) | 1,033.0 (16.48 / 17.66) |
| 0.100 | 0.1192 | 2,509.0 (517.52 / 534.34) | 2,565.0 (117.92 / 144.39) | 2,649.0 (109.56 / 126.59) | 2,649.0 (111.08 / 120.84) | 2,650.0 (109.52 / 125.36) |
| 0.500 | 0.2758 | 2,509.0 (528.07 / 582.13) | 2,661.2 (267.63 / 327.14) | 2,660.0 (243.69 / 312.30) | 2,660.0 (248.04 / 320.41) | 2,661.0 (243.20 / 324.82) |

### `point:extension`

| target sel. | actual sel. | seqscan | btree | bitmap-plain | bitmap-wah | bitmap-roaring |
|---|---|---|---|---|---|---|
| 0.0001 | 0.0112 | 2,509.0 (692.85 / 704.39) | 919.2 (22.25 / 24.97) | 1,061.2 (24.77 / 26.82) | 1,059.2 (24.62 / 26.63) | 1,058.2 (23.55 / 25.58) |
| 0.001 | 0.0112 | 2,509.0 (665.66 / 692.22) | 912.8 (21.21 / 23.72) | 1,054.8 (22.77 / 25.46) | 1,052.8 (22.93 / 25.45) | 1,051.8 (21.66 / 24.28) |
| 0.010 | 0.0112 | 2,509.0 (652.25 / 661.58) | 914.8 (20.65 / 23.26) | 1,056.8 (22.31 / 25.35) | 1,054.8 (22.44 / 25.24) | 1,053.8 (20.97 / 24.15) |
| 0.100 | 0.0913 | 2,509.0 (642.86 / 666.97) | 2,498.0 (117.39 / 123.38) | 2,596.6 (109.98 / 125.26) | 2,596.6 (109.10 / 117.34) | 2,596.6 (106.83 / 112.73) |
| 0.500 | 0.0939 | 2,509.0 (540.61 / 647.74) | 2,506.0 (115.32 / 208.02) | 2,603.2 (106.55 / 166.32) | 2,603.2 (110.79 / 157.97) | 2,603.2 (99.72 / 176.36) |

### `point:size`

| target sel. | actual sel. | seqscan | btree |
|---|---|---|---|
| 0.0001 | 0.0001 | 2,509.0 (469.41 / 477.57) | 12.2 (0.37 / 0.63) |
| 0.001 | 0.0002 | 2,509.0 (432.96 / 444.16) | 20.6 (0.39 / 0.72) |
| 0.010 | 0.0198 | 2,509.0 (463.71 / 507.22) | 1,396.0 (34.41 / 42.07) |
| 0.100 | 0.0198 | 2,509.0 (518.90 / 535.82) | 1,396.0 (33.94 / 37.48) |
| 0.500 | 0.0198 | 2,509.0 (555.08 / 555.44) | 1,396.0 (36.74 / 39.25) |

### `range:mtime`

| target sel. | actual sel. | seqscan | btree |
|---|---|---|---|
| 0.0001 | 0.0001 | 2,509.0 (410.91 / 423.07) | 13.0 (0.34 / 0.57) |
| 0.001 | 0.0010 | 2,509.0 (410.89 / 440.05) | 101.4 (1.05 / 1.55) |
| 0.010 | 0.0100 | 2,509.0 (419.93 / 429.29) | 831.8 (15.77 / 19.22) |
| 0.100 | 0.1000 | 2,509.0 (451.04 / 500.47) | 2,505.0 (116.10 / 125.56) |
| 0.500 | 0.5000 | 2,509.0 (476.68 / 524.56) | 2,683.6 (605.11 / 704.01) |

### `range:size`

| target sel. | actual sel. | seqscan | btree |
|---|---|---|---|
| 0.0001 | 0.0001 | 2,509.0 (395.56 / 410.87) | 16.6 (0.35 / 0.63) |
| 0.001 | 0.0048 | 2,509.0 (398.17 / 409.53) | 364.6 (1.04 / 1.58) |
| 0.010 | 0.0100 | 2,509.0 (398.97 / 411.08) | 836.8 (15.05 / 16.83) |
| 0.100 | 0.1000 | 2,509.0 (403.69 / 418.64) | 2,507.8 (103.97 / 116.09) |
| 0.500 | 0.5000 | 2,509.0 (441.91 / 496.64) | 2,683.6 (512.81 / 595.07) |

### `term`

| target sel. | actual sel. | seqscan | inverted | inverted-raw |
|---|---|---|---|---|
| 0.0001 | 0.0001 | 2,509.0 (711.45 / 766.02) | 10.4 (0.12 / 0.25) | 10.4 (0.12 / 0.24) |
| 0.001 | 0.0010 | 2,509.0 (827.33 / 843.02) | 102.6 (1.03 / 1.51) | 102.8 (0.87 / 1.32) |
| 0.010 | 0.0102 | 2,509.0 (808.54 / 856.84) | 851.8 (16.07 / 18.08) | 853.2 (15.33 / 18.76) |
| 0.100 | 0.0997 | 2,509.0 (697.72 / 782.38) | 2,475.2 (106.44 / 119.64) | 2,490.6 (100.33 / 112.04) |
| 0.500 | 0.2010 | 2,509.0 (722.14 / 762.29) | 2,516.0 (204.50 / 244.62) | 2,550.0 (213.07 / 274.27) |
