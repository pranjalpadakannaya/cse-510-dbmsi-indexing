# cse-510-dbmsi-indexing
A repository for CSE 510's Course Project

## Matthew — Benchmark Harness + Integration/Testing

### Priority 1: Shared benchmark harness

Build the common framework that can run the same workload against:

- sequential scan
- B+ tree
- bitmap index
- inverted index

For each run, collect where available:

- page I/O
- sequential/random reads
- query latency
- index/storage size
- build/update time

Use the storage layer's existing I/O statistics rather than creating a separate I/O model.

### Priority 2: Correctness + integration

The benchmark should verify that each index returns the same result set as the sequential-scan baseline.

Provide a common interface/output format so results from each index can be compared and used directly in our presentation/report.

Include:
- reproducible benchmark commands
- fixed configuration/seeds
- tests
- README/run instructions

### Presentation

Continue leading the final presentation. Once benchmark results are available, use the actual measured results/figures rather than placeholder claims.

### Dependencies

Synthetic data + storage
→ sequential scan
→ indexes
→ your benchmark harness
→ measured results
→ presentation + technical report

Please work and commit on `dev/matthew`.
