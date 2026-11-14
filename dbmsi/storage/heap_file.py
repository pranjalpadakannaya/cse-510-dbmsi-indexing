"""Unordered table storage: a linked chain of slotted pages.

Records are opaque byte strings (serialization is the caller's concern) and
are addressed by :class:`RID`. Inserts append to the last page in the chain,
so a bulk load lays pages out contiguously on disk and a full :meth:`scan`
is (almost entirely) sequential page reads. Space freed by deletes inside
earlier pages is reused only by in-place updates, which keeps inserts O(1)
page accesses.

A heap file is identified by its first page id; pass it back to the
constructor to reopen the file.
"""

from __future__ import annotations

from typing import Iterator, Optional

from .buffer_pool import BufferPoolManager
from .constants import INVALID_PAGE_ID
from .rid import RID
from .slotted_page import MAX_RECORD_SIZE, PageFullError, SlottedPage


class HeapFile:
    def __init__(self, bpm: BufferPoolManager, first_page_id: Optional[int] = None):
        self.bpm = bpm
        if first_page_id is None:
            with bpm.create() as page:
                SlottedPage.init(page.data)
                self.first_page_id = page.page_id
            self.last_page_id = self.first_page_id
            self.num_pages = 1
        else:
            self.first_page_id = first_page_id
            self.last_page_id, self.num_pages = self._walk_chain()

    def _walk_chain(self) -> tuple[int, int]:
        pid, count = self.first_page_id, 0
        while True:
            with self.bpm.fetch(pid) as page:
                nxt = SlottedPage(page.data).next_page_id
            count += 1
            if nxt == INVALID_PAGE_ID:
                return pid, count
            pid = nxt

    # -- record operations --------------------------------------------------

    def insert(self, record: bytes) -> RID:
        if len(record) > MAX_RECORD_SIZE:
            raise PageFullError(
                f"record of {len(record)} B exceeds max {MAX_RECORD_SIZE} B"
            )
        with self.bpm.fetch(self.last_page_id) as page:
            sp = SlottedPage(page.data)
            if sp.can_insert(len(record)):
                slot = sp.insert(record)
                page.mark_dirty()
                return RID(page.page_id, slot)

            with self.bpm.create() as new:
                new_sp = SlottedPage.init(new.data)
                slot = new_sp.insert(record)
                sp.next_page_id = new.page_id
                page.mark_dirty()
                self.last_page_id = new.page_id
                self.num_pages += 1
                return RID(new.page_id, slot)

    def get(self, rid: RID) -> bytes:
        with self.bpm.fetch(rid.page_id) as page:
            return SlottedPage(page.data).get(rid.slot_id)

    def delete(self, rid: RID) -> None:
        with self.bpm.fetch(rid.page_id) as page:
            SlottedPage(page.data).delete(rid.slot_id)
            page.mark_dirty()

    def update(self, rid: RID, record: bytes) -> None:
        """Replace a record in place; the RID never changes.

        Raises :class:`PageFullError` if the record's page cannot hold the
        new version.
        """
        with self.bpm.fetch(rid.page_id) as page:
            SlottedPage(page.data).update(rid.slot_id, record)
            page.mark_dirty()

    # -- scanning -----------------------------------------------------------

    def page_ids(self) -> Iterator[int]:
        """Yield the ids of the pages in chain order."""
        pid = self.first_page_id
        while pid != INVALID_PAGE_ID:
            with self.bpm.fetch(pid) as page:
                nxt = SlottedPage(page.data).next_page_id
            yield pid
            pid = nxt

    def scan(self) -> Iterator[tuple[RID, bytes]]:
        """Yield ``(rid, record)`` for every live record, page by page.

        Each page is pinned only while its records are copied out, so an
        abandoned iterator never leaves a page pinned.
        """
        pid = self.first_page_id
        while pid != INVALID_PAGE_ID:
            with self.bpm.fetch(pid) as page:
                sp = SlottedPage(page.data)
                batch = [(RID(pid, sid), rec) for sid, rec in sp.records()]
                pid = sp.next_page_id
            yield from batch
