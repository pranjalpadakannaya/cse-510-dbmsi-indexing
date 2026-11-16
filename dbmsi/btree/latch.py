"""Reader-writer latches for B+ tree pages.

A :class:`RWLatch` is held in *shared* mode by any number of readers or in
*exclusive* mode by one writer. Waiting writers block new readers, so a
stream of searches cannot starve an insert. Latches are not re-entrant: a
thread must not take a latch it already holds.

A :class:`LatchTable` hands out one latch per page id, created on first use.
Latches are separate from buffer-pool pins: a page may be evicted and read
back while a thread holds its latch, because the latch belongs to the page
id, not to the frame.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Iterator


class RWLatch:
    __slots__ = ("_cond", "_readers", "_writer", "_waiting_writers")

    def __init__(self) -> None:
        self._cond = threading.Condition(threading.Lock())
        self._readers = 0
        self._writer = False
        self._waiting_writers = 0

    def acquire_shared(self) -> None:
        with self._cond:
            while self._writer or self._waiting_writers:
                self._cond.wait()
            self._readers += 1

    def release_shared(self) -> None:
        with self._cond:
            if self._readers <= 0:
                raise RuntimeError("release_shared without a shared hold")
            self._readers -= 1
            if self._readers == 0:
                self._cond.notify_all()

    def acquire_exclusive(self) -> None:
        with self._cond:
            self._waiting_writers += 1
            try:
                while self._writer or self._readers:
                    self._cond.wait()
            finally:
                self._waiting_writers -= 1
            self._writer = True

    def release_exclusive(self) -> None:
        with self._cond:
            if not self._writer:
                raise RuntimeError("release_exclusive without an exclusive hold")
            self._writer = False
            self._cond.notify_all()

    def acquire(self, exclusive: bool) -> None:
        if exclusive:
            self.acquire_exclusive()
        else:
            self.acquire_shared()

    def release(self, exclusive: bool) -> None:
        if exclusive:
            self.release_exclusive()
        else:
            self.release_shared()

    @contextmanager
    def shared(self) -> Iterator[None]:
        self.acquire_shared()
        try:
            yield
        finally:
            self.release_shared()

    @contextmanager
    def exclusive(self) -> Iterator[None]:
        self.acquire_exclusive()
        try:
            yield
        finally:
            self.release_exclusive()

    @property
    def is_free(self) -> bool:
        """True if nobody holds or waits for the latch (for tests)."""
        with self._cond:
            return not (self._readers or self._writer or self._waiting_writers)


class LatchTable:
    """One :class:`RWLatch` per page id, created lazily."""

    def __init__(self) -> None:
        self._latches: dict[int, RWLatch] = {}
        self._lock = threading.Lock()

    def get(self, page_id: int) -> RWLatch:
        latch = self._latches.get(page_id)
        if latch is None:
            with self._lock:
                latch = self._latches.setdefault(page_id, RWLatch())
        return latch

    def all_free(self) -> bool:
        """True if no page latch is held or awaited (for tests)."""
        with self._lock:
            latches = list(self._latches.values())
        return all(latch.is_free for latch in latches)
