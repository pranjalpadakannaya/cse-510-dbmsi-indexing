"""Measure real sequential and random page-read cost on this machine.

The benchmark's I/O cost is ``sequential reads + k * random reads``. Instead
of assuming ``k`` (the original runs used 1, 4 and 10), this module times
page reads of ``PAGE_SIZE`` bytes against a real file and derives ``k`` as

    k = mean random read time / mean sequential read time.

Reads bypass the operating system page cache (``F_NOCACHE`` on macOS,
``posix_fadvise(DONTNEED)`` before each read on Linux), and the test file is
filled with random bytes so it cannot be sparse or compressed. Sequential
reads walk consecutive pages, exactly what ``DiskManager`` classifies as
sequential; random reads draw uniformly random pages.

The result is written as JSON and referenced from a benchmark config with
``"io_calibration"``; see :func:`load_calibration`.
"""

from __future__ import annotations

import json
import os
import platform
import random
import statistics
import tempfile
import time
from typing import Callable, Optional

from ..storage.constants import PAGE_SIZE

DEFAULT_FILE_MB = 512
DEFAULT_READS = 20_000
CHUNK_PAGES = 256


def _disable_cache(fd: int) -> str:
    """Ask the OS not to cache this file; returns how it was done."""
    import fcntl

    if hasattr(fcntl, "F_NOCACHE"):
        fcntl.fcntl(fd, fcntl.F_NOCACHE, 1)
        return "F_NOCACHE"
    if hasattr(os, "posix_fadvise"):
        return "posix_fadvise(DONTNEED) before each read"
    return "none available"


def _flush_cache(fd: int) -> None:
    """Evict what the writes left in the page cache (Linux; macOS never cached it)."""
    if hasattr(os, "posix_fadvise") and hasattr(os, "POSIX_FADV_DONTNEED"):
        os.fsync(fd)
        os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)


def _drop(fd: int, offset: int) -> None:
    if hasattr(os, "posix_fadvise") and hasattr(os, "POSIX_FADV_DONTNEED"):
        os.posix_fadvise(fd, offset, PAGE_SIZE, os.POSIX_FADV_DONTNEED)


def _write_file(path: str, num_pages: int) -> None:
    rng = random.Random(0)
    with open(path, "wb") as f:
        # Without this the freshly written pages stay cached and reads of them
        # come back at memory speed, which would inflate k.
        _disable_cache(f.fileno())
        for _ in range(0, num_pages, CHUNK_PAGES):
            f.write(rng.randbytes(PAGE_SIZE * min(CHUNK_PAGES, num_pages)))
        f.flush()
        os.fsync(f.fileno())
        _flush_cache(f.fileno())


def _time_reads(fd: int, pages: list[int]) -> list[float]:
    """Seconds per ``pread`` of one page, in the given order."""
    pread, clock = os.pread, time.perf_counter
    drop = _drop
    out = []
    for page in pages:
        offset = page * PAGE_SIZE
        drop(fd, offset)
        start = clock()
        data = pread(fd, PAGE_SIZE, offset)
        out.append(clock() - start)
        if len(data) != PAGE_SIZE:
            raise OSError(f"short read at page {page}")
    return out


def _summary(samples: list[float]) -> dict:
    ordered = sorted(samples)
    return {
        "reads": len(ordered),
        "mean_ms": statistics.fmean(ordered) * 1000,
        "median_ms": statistics.median(ordered) * 1000,
        "p95_ms": ordered[int(0.95 * (len(ordered) - 1))] * 1000,
    }


def calibrate(
    file_mb: int = DEFAULT_FILE_MB,
    reads: int = DEFAULT_READS,
    directory: Optional[str] = None,
    seed: int = 0,
    log: Callable[[str], None] = lambda _: None,
) -> dict:
    """Time sequential and random page reads and return the measurements.

    :param file_mb: size of the test file; keep it well above the RAM the OS
        would spend caching it (cache bypass is requested regardless).
    :param reads: timed reads per access pattern.
    :param directory: where to create the file; use the disk the database
        file would live on (default: the system temp directory).
    """
    num_pages = file_mb * 1024 * 1024 // PAGE_SIZE
    reads = min(reads, num_pages - 1)
    rng = random.Random(seed)
    with tempfile.TemporaryDirectory(dir=directory, prefix="dbmsi-calibrate-") as tmp:
        path = os.path.join(tmp, "calibration.bin")
        log(f"calibrate: writing {file_mb} MB test file in {tmp}")
        _write_file(path, num_pages)
        fd = os.open(path, os.O_RDONLY)
        try:
            cache = _disable_cache(fd)
            log(f"calibrate: page cache bypass: {cache}")
            start = rng.randrange(0, num_pages - reads)
            log(f"calibrate: {reads} sequential reads")
            sequential = _time_reads(fd, list(range(start, start + reads)))
            log(f"calibrate: {reads} random reads")
            random_pages = [rng.randrange(num_pages) for _ in range(reads)]
            random_ = _time_reads(fd, random_pages)
        finally:
            os.close(fd)

    seq, rnd = _summary(sequential), _summary(random_)
    raw_k = rnd["mean_ms"] / seq["mean_ms"]
    return {
        "page_size": PAGE_SIZE,
        "sequential": seq,
        "random": rnd,
        "raw_k": raw_k,
        # A random read cannot really be cheaper than a sequential one; a
        # ratio below 1 is measurement noise, so the cost model floors it.
        "k": max(1.0, raw_k),
        "method": {
            "file_mb": file_mb,
            "cache_bypass": cache,
            "k": "mean random read time / mean sequential read time",
            "random_read_latency_ms": "mean random read time",
        },
        "machine": {
            "platform": platform.platform(),
            "processor": platform.processor() or platform.machine(),
            "python": platform.python_version(),
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        },
    }


def load_calibration(path: str) -> dict:
    """Read a calibration file written by ``python -m dbmsi.bench calibrate``."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    for key in ("k", "random", "sequential"):
        if key not in data:
            raise ValueError(f"{path}: not a calibration file (missing {key!r})")
    return data


def write_calibration(data: dict, path: str) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")


def format_calibration(data: dict) -> str:
    s, r = data["sequential"], data["random"]
    return (
        f"sequential read: mean {s['mean_ms'] * 1000:.1f} us, median {s['median_ms'] * 1000:.1f} us\n"
        f"random read:     mean {r['mean_ms'] * 1000:.1f} us, median {r['median_ms'] * 1000:.1f} us\n"
        f"k = random / sequential = {data['raw_k']:.2f}"
        + ("" if data["k"] == data["raw_k"] else f" (floored to {data['k']:g})")
    )
