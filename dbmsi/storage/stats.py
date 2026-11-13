"""I/O accounting for the storage layer.

A single :class:`IOStats` instance is shared by a :class:`DiskManager` and the
:class:`BufferPoolManager` on top of it. The disk manager records *physical*
page I/O; the buffer pool records *logical* page requests and cache behavior.
Physical page reads/writes are the primary cost metric for all experiments.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from dataclasses import dataclass, field, fields
from typing import Iterator


@dataclass
class IOStats:
    # Physical I/O (DiskManager)
    page_reads: int = 0
    page_writes: int = 0
    sequential_reads: int = 0
    random_reads: int = 0
    pages_allocated: int = 0
    pages_deallocated: int = 0

    # Logical requests / cache behavior (BufferPoolManager)
    page_requests: int = 0
    buffer_hits: int = 0
    buffer_misses: int = 0
    evictions: int = 0
    dirty_writebacks: int = 0

    _lock: threading.Lock = field(
        default_factory=threading.Lock, repr=False, compare=False
    )

    def incr(self, name: str, amount: int = 1) -> None:
        with self._lock:
            setattr(self, name, getattr(self, name) + amount)

    def _counter_names(self) -> list[str]:
        return [f.name for f in fields(self) if not f.name.startswith("_")]

    def reset(self) -> None:
        with self._lock:
            for name in self._counter_names():
                setattr(self, name, 0)

    def snapshot(self) -> "IOStats":
        with self._lock:
            return IOStats(**{n: getattr(self, n) for n in self._counter_names()})

    def __sub__(self, other: "IOStats") -> "IOStats":
        return IOStats(
            **{
                n: getattr(self, n) - getattr(other, n)
                for n in self._counter_names()
            }
        )

    def as_dict(self) -> dict[str, int]:
        return {n: getattr(self, n) for n in self._counter_names()}

    @property
    def hit_rate(self) -> float:
        return self.buffer_hits / self.page_requests if self.page_requests else 0.0

    def io_cost(self, k: float = 1.0) -> float:
        """Modeled I/O cost: sequential reads + k * random reads."""
        return self.sequential_reads + k * self.random_reads


class IOMeasurement:
    """Result holder filled in when a :func:`measure` block exits."""

    def __init__(self) -> None:
        self.delta: IOStats = IOStats()


@contextmanager
def measure(stats: IOStats) -> Iterator[IOMeasurement]:
    """Capture the counter deltas produced inside a ``with`` block.

    >>> with measure(bpm.stats) as m:
    ...     run_query()
    >>> m.delta.page_reads
    """
    result = IOMeasurement()
    before = stats.snapshot()
    try:
        yield result
    finally:
        result.delta = stats.snapshot() - before
