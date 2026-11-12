# cse-510-dbmsi-indexing
A repository for CSE 510's Course Project
## Xinyan / Li — Synthetic Data + Bitmap Index

### Priority 1: Synthetic metadata generator

Build a reproducible Python generator for file-metadata records using the schema from our approved proposal:

- `size_bytes`
- `modified_time`
- `extension`
- `category`
- `semester`
- `course`
- `filename`

Requirements:
- Fixed random seed so results are reproducible.
- Support at least 10K, 100K, and 1M records.
- Filename terms should have skew so common and rare search terms exist.
- Output must be usable by the sequential scan and all three index implementations.
- Include tests and simple run instructions.

### Priority 2: Bitmap index

After the generator works, implement the bitmap-index core for categorical attributes such as:

- extension
- category
- semester
- course

Core target:
- uncompressed bitmap baseline
- WAH-compressed bitmap
- equality/filter lookup
- AND/OR operations
- correctness tests against sequential scan

Roaring bitmap is an extension only if the core artifact is complete.

### Dependencies

The storage/page layer is already available from Pranjal's implementation.

Your outputs feed:

Synthetic data
→ sequential-scan correctness baseline
→ B+ tree / bitmap / inverted index
→ benchmark harness
→ final results

Please work and commit on `dev/li`.
