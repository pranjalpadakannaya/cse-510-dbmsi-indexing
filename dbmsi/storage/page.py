"""In-memory page frames held by the buffer pool."""

from __future__ import annotations

from .constants import INVALID_PAGE_ID, PAGE_SIZE


class Page:
    """A buffer-pool frame holding one ``PAGE_SIZE`` byte page.

    Callers obtain pages from :class:`BufferPoolManager` already pinned,
    read or modify ``data`` in place, call :meth:`mark_dirty` after any
    modification, and unpin the page when done. Never keep a reference to
    ``data`` after unpinning: the frame may be reused for another page.
    """

    __slots__ = ("page_id", "data", "pin_count", "is_dirty")

    def __init__(self) -> None:
        self.page_id = INVALID_PAGE_ID
        self.data = bytearray(PAGE_SIZE)
        self.pin_count = 0
        self.is_dirty = False

    def mark_dirty(self) -> None:
        self.is_dirty = True

    def _reset(self, page_id: int = INVALID_PAGE_ID) -> None:
        self.page_id = page_id
        self.pin_count = 0
        self.is_dirty = False

    def __repr__(self) -> str:
        return (
            f"Page(id={self.page_id}, pins={self.pin_count}, dirty={self.is_dirty})"
        )
