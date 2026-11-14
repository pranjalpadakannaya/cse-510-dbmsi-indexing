"""Physical page I/O against a single database file (or an in-memory store).

Every call to :meth:`DiskManager.read_page` / :meth:`DiskManager.write_page`
is one physical page I/O and is recorded in the shared :class:`IOStats`.
Reads are classified as *sequential* when they target the page immediately
after the previously read page, and *random* otherwise; this feeds the
``sequential + k * random`` cost model used by the benchmark harness.
"""

from __future__ import annotations

import os
import threading
from typing import Optional

from .constants import PAGE_SIZE
from .stats import IOStats


class DiskManager:
    def __init__(self, path: Optional[str] = None, stats: Optional[IOStats] = None):
        """Open a page store.

        :param path: database file to use. ``None`` keeps pages in memory,
            which is convenient for tests; I/O is still counted identically.
            An existing file is reopened and its pages are preserved.
        :param stats: counters to update; a fresh :class:`IOStats` by default.
        """
        self.path = path
        self.stats = stats if stats is not None else IOStats()
        self._lock = threading.Lock()
        self._free_pages: list[int] = []
        self._freed: set[int] = set()
        self._last_read_page: Optional[int] = None  # None => next read is a seek

        self._mem: Optional[dict[int, bytes]] = None
        self._file = None
        if path is None:
            self._mem = {}
            self._num_pages = 0
        else:
            mode = "r+b" if os.path.exists(path) else "w+b"
            self._file = open(path, mode)
            self._file.seek(0, os.SEEK_END)
            self._num_pages = self._file.tell() // PAGE_SIZE

    # -- allocation ---------------------------------------------------------

    @property
    def num_pages(self) -> int:
        """Number of page ids ever allocated (the high-water mark)."""
        return self._num_pages

    def allocate_page(self) -> int:
        """Reserve a page id; freed ids are reused before growing the file.

        Allocation does not perform I/O: the page reads back as zeros until
        it is first written.
        """
        with self._lock:
            if self._free_pages:
                page_id = self._free_pages.pop()
                self._freed.discard(page_id)
            else:
                page_id = self._num_pages
                self._num_pages += 1
        self.stats.incr("pages_allocated")
        return page_id

    def deallocate_page(self, page_id: int) -> None:
        """Return a page id to the free list (the free list is not persisted)."""
        with self._lock:
            self._check_page_id(page_id)
            self._freed.add(page_id)
            self._free_pages.append(page_id)
            if self._mem is not None:
                self._mem.pop(page_id, None)
        self.stats.incr("pages_deallocated")

    # -- I/O ----------------------------------------------------------------

    def read_page(self, page_id: int, buf: bytearray) -> None:
        """Read one page into ``buf`` (which must be ``PAGE_SIZE`` bytes)."""
        if len(buf) != PAGE_SIZE:
            raise ValueError(f"buffer must be {PAGE_SIZE} bytes, got {len(buf)}")
        with self._lock:
            self._check_page_id(page_id)
            if self._mem is not None:
                data = self._mem.get(page_id)
                if data is None:
                    buf[:] = bytes(PAGE_SIZE)
                else:
                    buf[:] = data
            else:
                self._file.seek(page_id * PAGE_SIZE)
                n = self._file.readinto(buf)
                if n < PAGE_SIZE:  # allocated but never written past EOF
                    buf[n:] = bytes(PAGE_SIZE - n)
            sequential = (
                self._last_read_page is not None
                and page_id == self._last_read_page + 1
            )
            self._last_read_page = page_id
        self.stats.incr("page_reads")
        self.stats.incr("sequential_reads" if sequential else "random_reads")

    def write_page(self, page_id: int, data: bytes | bytearray | memoryview) -> None:
        """Write one full page."""
        if len(data) != PAGE_SIZE:
            raise ValueError(f"page data must be {PAGE_SIZE} bytes, got {len(data)}")
        with self._lock:
            self._check_page_id(page_id)
            if self._mem is not None:
                self._mem[page_id] = bytes(data)
            else:
                self._file.seek(page_id * PAGE_SIZE)
                self._file.write(data)
        self.stats.incr("page_writes")

    # -- lifecycle ----------------------------------------------------------

    def sync(self) -> None:
        if self._file is not None:
            self._file.flush()
            os.fsync(self._file.fileno())

    def close(self) -> None:
        if self._file is not None:
            self._file.flush()
            self._file.close()
            self._file = None

    def __enter__(self) -> "DiskManager":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _check_page_id(self, page_id: int) -> None:
        if not 0 <= page_id < self._num_pages:
            raise IndexError(f"page id {page_id} out of range [0, {self._num_pages})")
        if page_id in self._freed:
            raise ValueError(f"page id {page_id} has been deallocated")
