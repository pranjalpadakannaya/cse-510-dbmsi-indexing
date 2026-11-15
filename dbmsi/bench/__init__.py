"""Shared benchmark harness for every access method.

Runs one query workload against the sequential scan, the B+ tree, the
bitmap index and the inverted index over the same heap file and buffer
pool; measures page I/O (from the storage layer's :class:`IOStats`),
latency, size, build and insert cost; compares the page I/O with each
method's analytical estimate; and checks every result against the
sequential scan. :mod:`dbmsi.bench.sweep` repeats a run over one parameter
and :mod:`dbmsi.bench.concurrency` measures concurrent B+ tree throughput. See :mod:`dbmsi.bench.harness` for the steps and
``python -m dbmsi.bench --help`` for the command line.
"""

from .concurrency import run_throughput
from .harness import METHODS, Benchmark, BenchmarkConfig, BenchmarkResult, run_benchmark, spec_of
from .methods import (
    AccessMethod, BitmapMethod, BTreeMethod, InvertedMethod, SequentialScan,
)
from .report import estimate_check, load_result, markdown, summarize, write_results
from .sweep import run_sweep, set_param

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
    "estimate_check",
    "load_result",
    "markdown",
    "run_benchmark",
    "run_sweep",
    "run_throughput",
    "set_param",
    "spec_of",
    "summarize",
    "write_results",
]
