"""LRU buffer pool with pin/unpin semantics.

Every page access in the system goes through :class:`BufferPoolManager`.
A request for a resident page is a *hit*; otherwise it is a *miss* and costs
one physical read (plus one write if the evicted victim was dirty). All of
this is recorded in the :class:`IOStats` shared with the disk manager.

Typical use::

    with bpm.fetch(page_id) as page:
        sp = SlottedPage(page.data)
        sp.insert(b"...")
        page.mark_dirty()

The manager is guarded by a single re-entrant lock, so it is safe to share
between threads; finer-grained page latches are left to the index layers.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Iterator

from .disk_manager import DiskManager
from .lru_replacer import LRUReplacer
from .page import Page
from .stats import IOStats


class BufferPoolFullError(Exception):
    """Raised when every frame is pinned and no page can be brought in."""


class BufferPoolManager:
    def __init__(self, disk: DiskManager, pool_size: int):
        if pool_size <= 0:
            raise ValueError("pool_size must be positive")
        self.disk = disk
        self.pool_size = pool_size
        self._frames = [Page() for _ in range(pool_size)]
        self._page_table: dict[int, int] = {}  # page_id -> frame_id
        self._free_frames = list(range(pool_size - 1, -1, -1))
        self._replacer = LRUReplacer()
        self._lock = threading.RLock()

    @property
    def stats(self) -> IOStats:
        return self.disk.stats

    # -- core API -----------------------------------------------------------

    def fetch_page(self, page_id: int) -> Page:
        """Return ``page_id`` pinned, reading it from disk on a miss."""
        with self._lock:
            self.stats.incr("page_requests")
            frame_id = self._page_table.get(page_id)
            if frame_id is not None:
                self.stats.incr("buffer_hits")
                page = self._frames[frame_id]
                self._pin(frame_id, page)
                return page

            self.stats.incr("buffer_misses")
            frame_id = self._acquire_frame()
            page = self._frames[frame_id]
            try:
                self.disk.read_page(page_id, page.data)
            except BaseException:
                self._free_frames.append(frame_id)
                raise
            page._reset(page_id)
            self._page_table[page_id] = frame_id
            self._pin(frame_id, page)
            return page

    def new_page(self) -> Page:
        """Allocate a fresh zeroed page on disk and return it pinned.

        The page is marked dirty so it reaches disk when flushed or evicted.
        """
        with self._lock:
            frame_id = self._acquire_frame()
            page_id = self.disk.allocate_page()
            page = self._frames[frame_id]
            page.data[:] = bytes(len(page.data))
            page._reset(page_id)
            page.is_dirty = True
            self._page_table[page_id] = frame_id
            self._pin(frame_id, page)
            return page

    def unpin_page(self, page_id: int, is_dirty: bool = False) -> None:
        """Release one pin on ``page_id``; ``is_dirty`` ORs into its dirty bit."""
        with self._lock:
            frame_id = self._page_table.get(page_id)
            if frame_id is None:
                raise KeyError(f"page {page_id} is not in the buffer pool")
            page = self._frames[frame_id]
            if page.pin_count <= 0:
                raise RuntimeError(f"page {page_id} is not pinned")
            page.is_dirty = page.is_dirty or is_dirty
            page.pin_count -= 1
            if page.pin_count == 0:
                self._replacer.unpin(frame_id)

    def flush_page(self, page_id: int) -> bool:
        """Write ``page_id`` to disk if it is resident and dirty.

        Returns ``True`` if the page was resident.
        """
        with self._lock:
            frame_id = self._page_table.get(page_id)
            if frame_id is None:
                return False
            self._flush_frame(self._frames[frame_id])
            return True

    def flush_all(self) -> None:
        with self._lock:
            for frame_id in self._page_table.values():
                self._flush_frame(self._frames[frame_id])

    def delete_page(self, page_id: int) -> None:
        """Drop ``page_id`` from the pool (without writing it) and free it on disk."""
        with self._lock:
            frame_id = self._page_table.get(page_id)
            if frame_id is not None:
                page = self._frames[frame_id]
                if page.pin_count > 0:
                    raise RuntimeError(f"cannot delete pinned page {page_id}")
                self._replacer.pin(frame_id)
                del self._page_table[page_id]
                page._reset()
                self._free_frames.append(frame_id)
            self.disk.deallocate_page(page_id)

    def clear(self) -> None:
        """Flush and evict every unpinned page, giving a cold cache.

        Benchmarks call this between runs so each run starts from the same
        buffer state. Pinned pages stay resident.
        """
        with self._lock:
            self.flush_all()
            for page_id, frame_id in list(self._page_table.items()):
                page = self._frames[frame_id]
                if page.pin_count == 0:
                    self._replacer.pin(frame_id)
                    del self._page_table[page_id]
                    page._reset()
                    self._free_frames.append(frame_id)

    # -- convenience --------------------------------------------------------

    @contextmanager
    def fetch(self, page_id: int) -> Iterator[Page]:
        """Pin ``page_id`` for the duration of a ``with`` block."""
        page = self.fetch_page(page_id)
        try:
            yield page
        finally:
            self.unpin_page(page.page_id)

    @contextmanager
    def create(self) -> Iterator[Page]:
        """Allocate a new page pinned for the duration of a ``with`` block."""
        page = self.new_page()
        try:
            yield page
        finally:
            self.unpin_page(page.page_id)

    # -- introspection ------------------------------------------------------

    def is_resident(self, page_id: int) -> bool:
        with self._lock:
            return page_id in self._page_table

    def pin_count(self, page_id: int) -> int:
        with self._lock:
            frame_id = self._page_table.get(page_id)
            return 0 if frame_id is None else self._frames[frame_id].pin_count

    @property
    def num_resident(self) -> int:
        with self._lock:
            return len(self._page_table)

    # -- internals ----------------------------------------------------------

    def _pin(self, frame_id: int, page: Page) -> None:
        page.pin_count += 1
        self._replacer.pin(frame_id)

    def _acquire_frame(self) -> int:
        if self._free_frames:
            return self._free_frames.pop()
        frame_id = self._replacer.victim()
        if frame_id is None:
            raise BufferPoolFullError(
                f"all {self.pool_size} frames are pinned; cannot load another page"
            )
        victim = self._frames[frame_id]
        self.stats.incr("evictions")
        if victim.is_dirty:
            self.stats.incr("dirty_writebacks")
            self.disk.write_page(victim.page_id, victim.data)
        del self._page_table[victim.page_id]
        victim._reset()
        return frame_id

    def _flush_frame(self, page: Page) -> None:
        if page.is_dirty:
            self.disk.write_page(page.page_id, page.data)
            page.is_dirty = False
