"""Run one workload against every access method and record what it costs.

A run is fully determined by a :class:`BenchmarkConfig`: the dataset (a
synthetic one from a seed, or a JSON Lines file), the query workload (from a
seed, or a file), the methods, and the buffer pool settings. The steps are:

1. load the dataset into a :class:`~dbmsi.storage.HeapFile`;
2. build every method from the heap's RIDs, measuring time and page I/O;
3. run every query on every method that supports it: once from a cold
   cache to count page I/O (the :class:`~dbmsi.storage.IOStats` deltas,
   next to the method's analytical estimate), then ``warmup`` untimed runs
   and up to ``repeat`` timed runs with the buffer pool kept warm, for the
   median and 95th percentile latency; check each result against the
   sequential scan;
4. insert extra rows into the heap and every method, measuring the cost per
   insert, then check every query again against the scan;
5. optionally, measure the throughput of concurrent B+ tree searches and
   inserts (see :mod:`dbmsi.bench.concurrency`).

Each step's numbers come from the storage layer's own counters, so they are
the same page I/O the index tests and the bitmap README report.
"""

from __future__ import annotations

import json
import platform
import statistics
import subprocess
import time
from dataclasses import asdict, dataclass, field, fields, replace
from datetime import datetime, timezone
from typing import Callable, Optional, Sequence

from dbmsi.storage import PAGE_SIZE, RID, BufferPoolManager, DiskManager, HeapFile, IOStats, measure
from dbmsi.workload import (
    FileMetadata, GeneratorConfig, PointQuery, Query, QueryGenerator, RangeQuery,
    SyntheticGenerator, TermQuery, read_queries, read_records,
)
from dbmsi.workload.queries import CATEGORICAL_ATTRIBUTES

from .calibrate import load_calibration
from .concurrency import run_throughput
from .methods import (
    AccessMethod, BitmapMethod, BTreeMethod, InvertedMethod, SequentialScan, pages_touched,
)

METHODS = ("seqscan", "btree", "bitmap-plain", "bitmap-wah", "bitmap-roaring", "inverted",
           "inverted-raw")
IO_FIELDS = ("page_reads", "sequential_reads", "random_reads", "page_writes",
             "page_requests", "buffer_hits")


@dataclass
class BenchmarkConfig:
    name: str = "benchmark"

    # Dataset: generated from a seed, or read from a JSON Lines file
    num_files: int = 10_000
    seed: int = 0
    generator: dict = field(default_factory=dict)  # GeneratorConfig overrides
    records: Optional[str] = None

    # Workload: generated from a seed, or read from a JSON Lines file
    query_seed: int = 0
    specs: list[str] = field(default_factory=lambda: [
        "point:extension", "point:uid", "range:size", "range:mtime", "term", "and", "or",
    ])
    selectivities: list[float] = field(default_factory=lambda: [0.001, 0.01, 0.1])
    per_level: int = 5
    queries: Optional[str] = None

    # Methods; attribute lists default to what the workload queries
    methods: list[str] = field(default_factory=lambda: [
        "seqscan", "btree", "bitmap-roaring", "inverted",
    ])
    btree_attributes: Optional[list[str]] = None
    bitmap_attributes: Optional[list[str]] = None
    btree_fill_factor: float = 1.0

    # Measurement. Page I/O comes from one cold-cache run per query; latency
    # from up to `repeat` warm runs after `warmup` more, stopping early (but
    # never before `min_repeat` runs) once `max_repeat_seconds` is spent.
    pool_size: int = 256
    db_path: Optional[str] = None  # None keeps pages in memory; I/O is counted the same
    repeat: int = 50
    warmup: int = 1
    min_repeat: int = 5
    max_repeat_seconds: float = 2.0
    fetch_records: bool = False  # also read each matching record, in RID order
    random_io_weights: list[float] = field(default_factory=lambda: [1.0, 4.0, 10.0])
    # Calibration file from `python -m dbmsi.bench calibrate`. It supplies the
    # measured k (random_io_weights) and random read latency
    # (throughput_read_latency_ms) unless the config sets those explicitly.
    io_calibration: Optional[str] = None
    io_calibration_data: Optional[dict] = None  # the file's contents, kept for the report
    num_updates: int = 100
    verify_after_updates: bool = True

    # Concurrent B+ tree searches and inserts (W7); no thread counts skips it
    throughput_threads: list[int] = field(default_factory=list)
    throughput_ops: int = 20_000
    throughput_insert_fraction: float = 0.2
    throughput_attribute: str = "size"
    throughput_latching: list[str] = field(default_factory=lambda: ["global", "crabbing"])
    throughput_read_latency_ms: list[float] = field(default_factory=lambda: [0.0])
    throughput_pool_size: Optional[int] = None  # defaults to pool_size

    def validate(self) -> None:
        unknown = [m for m in self.methods if m not in METHODS]
        if unknown:
            raise ValueError(f"unknown methods {unknown}; choose from {list(METHODS)}")
        if "seqscan" not in self.methods:
            raise ValueError("seqscan is the correctness baseline and must be included")
        if self.repeat < 1 or self.pool_size < 1 or self.num_updates < 0:
            raise ValueError("repeat and pool_size must be positive, num_updates non-negative")
        if self.warmup < 0 or not 1 <= self.min_repeat or not self.random_io_weights:
            raise ValueError("need warmup >= 0, min_repeat >= 1 and at least one random I/O weight")

    @classmethod
    def from_dict(cls, d: dict) -> "BenchmarkConfig":
        known = {f.name for f in fields(cls)}
        unknown = set(d) - known
        if unknown:
            raise ValueError(f"unknown config keys: {sorted(unknown)}")
        if d.get("io_calibration"):
            d = dict(d)
            cal = load_calibration(d["io_calibration"])
            d["io_calibration_data"] = cal
            d.setdefault("random_io_weights", [round(cal["k"], 2)])
            d.setdefault("throughput_read_latency_ms",
                         [0.0, round(cal["random"]["mean_ms"], 4)])
        return cls(**d)

    @classmethod
    def load(cls, path: str) -> "BenchmarkConfig":
        with open(path, encoding="utf-8") as f:
            return cls.from_dict(json.load(f))

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class BenchmarkResult:
    config: dict
    environment: dict
    dataset: dict
    build: list[dict]
    queries: list[dict]
    updates: list[dict]
    verification: dict
    throughput: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "BenchmarkResult":
        return cls(**d)

    @property
    def all_correct(self) -> bool:
        return all(q["correct"] for q in self.queries) and not self.verification.get("mismatches")


def spec_of(query: Query) -> str:
    """The workload spec a query belongs to (``"range:size"``, ``"and"``, ...)."""
    if isinstance(query, (PointQuery, RangeQuery)):
        return f"{query.kind}:{query.attribute}"
    if len(query.terms) == 1:
        return "term"
    return query.op if len(query.terms) == 2 else f"{query.op}:{len(query.terms)}"


def describe_query(query: Query) -> str:
    if isinstance(query, PointQuery):
        return f"{query.attribute} = {query.value!r}"
    if isinstance(query, RangeQuery):
        return f"{query.low!r} <= {query.attribute} <= {query.high!r}"
    return f" {query.op.upper()} ".join(query.terms)


def load_dataset(config: BenchmarkConfig) -> tuple[list[FileMetadata], list[FileMetadata]]:
    """The rows to build from and the rows inserted afterwards.

    A generated dataset has ``num_files + num_updates`` rows, so the base
    rows are the same at every update count. From a file, the last
    ``num_updates`` rows are held back for the update phase.
    """
    if config.records:
        rows = read_records(config.records)
        split = max(0, len(rows) - config.num_updates)
    else:
        gen = GeneratorConfig(num_files=config.num_files + config.num_updates, seed=config.seed)
        rows = SyntheticGenerator(replace(gen, **config.generator)).generate()
        split = config.num_files
    if split == 0:
        raise ValueError("the dataset has no rows left to build from")
    return rows[:split], rows[split:]


def make_queries(config: BenchmarkConfig, records: Sequence[FileMetadata]) -> list[Query]:
    if config.queries:
        return read_queries(config.queries)
    gen = QueryGenerator(records, seed=config.query_seed)
    return [q for spec in config.specs
            for q in gen.sweep(spec, config.selectivities, config.per_level)]


def make_methods(
    config: BenchmarkConfig, bpm: BufferPoolManager, heap: HeapFile, queries: Sequence[Query]
) -> list[AccessMethod]:
    queried = sorted({q.attribute for q in queries if isinstance(q, (PointQuery, RangeQuery))})
    btree_attrs = config.btree_attributes or queried
    bitmap_attrs = config.bitmap_attributes or [a for a in queried if a in CATEGORICAL_ATTRIBUTES]
    methods: list[AccessMethod] = []
    # The scan goes first: every other result is checked against it.
    for name in sorted(config.methods, key=lambda m: m != "seqscan"):
        if name == "seqscan":
            methods.append(SequentialScan(bpm, heap))
        elif name == "btree":
            if btree_attrs:
                methods.append(BTreeMethod(bpm, heap, btree_attrs, config.btree_fill_factor))
        elif name.startswith("bitmap-"):
            if bitmap_attrs:
                methods.append(BitmapMethod(bpm, heap, bitmap_attrs, name.split("-", 1)[1]))
        elif name.startswith("inverted"):
            if any(isinstance(q, TermQuery) for q in queries):
                encoding = name.split("-", 1)[1] if "-" in name else "vb"
                methods.append(InvertedMethod(bpm, heap, encoding))
    return methods


def environment() -> dict:
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, timeout=5,
        ).stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        commit = None
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "git_commit": commit,
    }


def io_cost_field(k: float) -> str:
    return f"io_cost_k{k:g}"


def _io(delta: IOStats, weights: Sequence[float]) -> dict:
    out = {name: getattr(delta, name) for name in IO_FIELDS}
    for k in weights:
        out[io_cost_field(k)] = delta.io_cost(k)
    return out


def percentile(values: Sequence[float], p: float) -> float:
    """The ``p``-th percentile (0-100), interpolating between ranks."""
    xs = sorted(values)
    pos = (len(xs) - 1) * p / 100
    lo = int(pos)
    hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (pos - lo)


class Benchmark:
    def __init__(self, config: BenchmarkConfig, log: Callable[[str], None] = lambda _: None):
        config.validate()
        self.config = config
        self.log = log

    def _timed(self, fn: Callable[[], object], flush: bool = False) -> tuple[object, float, IOStats]:
        """Run ``fn`` and return its result, seconds taken and I/O done.

        With ``flush`` the dirty pages are written out inside the
        measurement, so build and insert costs include their writes.
        """
        with measure(self.bpm.stats) as m:
            start = time.perf_counter()
            result = fn()
            if flush:
                self.bpm.flush_all()
            seconds = time.perf_counter() - start
        return result, seconds, m.delta

    def run(self) -> BenchmarkResult:
        c = self.config
        base, extra = load_dataset(c)
        queries = make_queries(c, base)
        self.log(f"dataset: {len(base)} rows (+{len(extra)} for updates), {len(queries)} queries")

        self.disk = DiskManager(c.db_path)
        self.bpm = BufferPoolManager(self.disk, c.pool_size)
        heap = HeapFile(self.bpm)
        try:
            rows, load_s, load_io = self._timed(
                lambda: [(heap.insert(r.pack()), r) for r in base], flush=True
            )
            methods = make_methods(c, self.bpm, heap, queries)
            build = [self._build(m, rows, load_s, load_io) for m in methods]
            dataset = {"num_rows": len(base), "heap_pages": heap.num_pages,
                       "num_queries": len(queries), "num_updates": len(extra)}

            query_rows = []
            for i, q in enumerate(queries):
                query_rows.extend(self._query(i, q, methods, heap, len(base)))
                if (i + 1) % 10 == 0 or i + 1 == len(queries):
                    self.log(f"queries: {i + 1}/{len(queries)}")

            updates = self._updates(methods, heap, extra) if extra else []
            verification = {}
            if extra and c.verify_after_updates:
                verification = self._verify(queries, methods)
            throughput = []
            if c.throughput_threads:
                throughput = run_throughput(
                    rows, c.throughput_attribute, c.throughput_threads,
                    c.throughput_ops, c.throughput_insert_fraction, c.query_seed,
                    latching=c.throughput_latching,
                    pool_size=c.throughput_pool_size or c.pool_size,
                    read_latencies_ms=c.throughput_read_latency_ms, log=self.log,
                )
        finally:
            self.disk.close()

        return BenchmarkResult(
            config=c.to_dict(), environment=environment(), dataset=dataset,
            build=build, queries=query_rows, updates=updates, verification=verification,
            throughput=throughput,
        )

    def _build(
        self, method: AccessMethod, rows: list, load_s: float, load_io: IOStats
    ) -> dict:
        self.bpm.clear()
        if isinstance(method, SequentialScan):
            seconds, delta = load_s, load_io  # the heap load is the scan's build
        else:
            _, seconds, delta = self._timed(lambda: method.build(rows), flush=True)
        pages = method.size_pages()
        size_bytes = method.size_bytes()
        self.log(f"built {method.name}: {pages} pages in {seconds:.2f} s")
        return {
            "method": method.name,
            "build_seconds": seconds,
            "build_page_writes": delta.page_writes,
            "build_page_reads": delta.page_reads,
            "size_pages": pages,
            "size_bytes": size_bytes if size_bytes is not None else pages * PAGE_SIZE,
            "details": method.describe(),
        }

    def _answer(
        self, method: AccessMethod, q: Query, heap: HeapFile
    ) -> tuple[set, IOStats, IOStats, IOStats]:
        """Answer ``q``; returns the RIDs and the I/O in total, of the index,
        and of fetching the matching records (sorted by RID, so each heap
        page is read once, as in a bitmap heap scan)."""
        with measure(self.bpm.stats) as total_io:
            with measure(self.bpm.stats) as index_io:
                rids = method.query(q)
            with measure(self.bpm.stats) as fetch_io:
                if self.config.fetch_records and not isinstance(method, SequentialScan):
                    for rid in sorted(rids):
                        heap.get(rid)
        return rids, total_io.delta, index_io.delta, fetch_io.delta

    def _run_query(self, method: AccessMethod, q: Query, heap: HeapFile) -> dict:
        c = self.config
        self.bpm.clear()

        start = time.perf_counter()
        rids, total_io, index_io, fetch_io = self._answer(method, q, heap)
        cold_s = time.perf_counter() - start

        for _ in range(c.warmup):
            self._answer(method, q, heap)

        latencies, decodes, rankings = [], [], []
        spent = 0.0
        warm_reads = 0

        while len(latencies) < c.repeat and (
            len(latencies) < c.min_repeat or spent < c.max_repeat_seconds
        ):
            method.decode_seconds = 0.0
            method.ranking_seconds = 0.0

            with measure(self.bpm.stats) as m:
                t = time.perf_counter()
                self._answer(method, q, heap)
                seconds = time.perf_counter() - t

            latencies.append(seconds)
            decodes.append(method.decode_seconds)
            rankings.append(method.ranking_seconds)

            warm_reads = m.delta.page_reads
            spent += seconds

        return {
            "rids": rids,
            "total_io": total_io,
            "index_io": index_io,
            "fetch_io": fetch_io,
            "cold_s": cold_s,
            "latencies": latencies,
            "decodes": decodes,
            "rankings": rankings,
            "warm_reads": warm_reads,
        }

    def _query(
        self, i: int, q: Query, methods: list[AccessMethod], heap: HeapFile, n: int
    ) -> list[dict]:
        c = self.config
        expected = None
        out = []

        for method in methods:  # seqscan comes first, so it sets the baseline
            if not method.supports(q):
                continue

            r = self._run_query(method, q, heap)
            rids = r["rids"]

            if expected is None:
                expected = rids

            estimate = method.estimate_reads(q, len(expected))

            if estimate is not None and c.fetch_records and not isinstance(method, SequentialScan):
                estimate += pages_touched(heap.num_pages, n, len(expected))

            lat = [s * 1000 for s in r["latencies"]]

            out.append({
                "query_id": i,
                "spec": spec_of(q),
                "query": describe_query(q),
                "target_selectivity": q.target_selectivity,
                "selectivity": len(expected) / n,
                "method": method.name,
                "result_count": len(rids),
                "expected_count": q.expected_count,
                "correct": rids == expected and (
                    q.expected_count < 0 or len(expected) == q.expected_count
                ),
                **_io(r["total_io"], c.random_io_weights),
                "index_page_reads": r["index_io"].page_reads,
                "fetch_page_reads": r["fetch_io"].page_reads,
                "estimated_page_reads": estimate,
                "cold_latency_ms": r["cold_s"] * 1000,
                "latency_ms": statistics.median(lat),
                "latency_p95_ms": percentile(lat, 95),
                "runs": len(lat),
                "decode_ms": statistics.median(r["decodes"]) * 1000,
                "ranking_ms": statistics.median(r["rankings"]) * 1000,
                "warm_page_reads": r["warm_reads"],
            })

        return out
    def _updates(self, methods: list[AccessMethod], heap: HeapFile, extra: list) -> list[dict]:
        self.bpm.clear()
        rids, heap_s, heap_io = self._timed(
            lambda: [heap.insert(r.pack()) for r in extra], flush=True
        )
        out = []
        for method in methods:
            if isinstance(method, SequentialScan):
                seconds, delta = heap_s, heap_io
            else:
                self.bpm.clear()
                _, seconds, delta = self._timed(
                    lambda: [method.insert(rid, r) for rid, r in zip(rids, extra)], flush=True
                )
            out.append({
                "method": method.name,
                "inserts": len(extra),
                "total_seconds": seconds,
                "ms_per_insert": seconds * 1000 / len(extra),
                "page_reads_per_insert": delta.page_reads / len(extra),
                "page_writes_per_insert": delta.page_writes / len(extra),
            })
            self.log(f"inserted {len(extra)} rows into {method.name} in {seconds:.2f} s")
        return out

    def _verify(self, queries: Sequence[Query], methods: list[AccessMethod]) -> dict:
        """Re-run every query after the inserts and compare against the scan."""
        mismatches = []
        checked = 0
        for i, q in enumerate(queries):
            expected = methods[0].query(q)
            for method in methods[1:]:
                if method.supports(q):
                    checked += 1
                    got = method.query(q)
                    if got != expected:
                        mismatches.append({"query_id": i, "method": method.name,
                                           "expected": len(expected), "got": len(got)})
        self.log(f"verified {checked} results after updates: {len(mismatches)} mismatches")
        return {"checked": checked, "mismatches": mismatches}


def run_benchmark(config: BenchmarkConfig, log: Callable[[str], None] = lambda _: None) -> BenchmarkResult:
    return Benchmark(config, log).run()
