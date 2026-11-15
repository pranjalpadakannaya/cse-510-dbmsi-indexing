"""Shared benchmark harness for every access method.

Runs one query workload against the sequential scan, the B+ tree, the
bitmap index and the inverted index over the same heap file and buffer
pool; measures page I/O (from the storage layer's :class:`IOStats`),
latency, size, build and insert cost; and checks every result against the
sequential scan. See :mod:`dbmsi.bench.harness` for the steps and
``python -m dbmsi.bench --help`` for the command line.
"""

from .harness import METHODS, Benchmark, BenchmarkConfig, BenchmarkResult, run_benchmark, spec_of
from .methods import (
    AccessMethod, BitmapMethod, BTreeMethod, InvertedMethod, SequentialScan,
)
from .report import load_result, markdown, summarize, write_results

__all__ = [
    "AccessMethod",
    "BTreeMethod",
    "Benchmark",
    "BenchmarkConfig",
    "BenchmarkResult",
    "BitmapMethod",
    "InvertedMethod",
    "METHODS",
    "SequentialScan",
    "load_result",
    "markdown",
    "run_benchmark",
    "spec_of",
    "summarize",
    "write_results",
]
