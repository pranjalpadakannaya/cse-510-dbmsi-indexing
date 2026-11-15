"""Slotted-page layout for variable-length records.

Layout of a ``PAGE_SIZE`` byte page (all integers little-endian)::

    +------------------------------------------------------------------+
    | header (8 B) | slot 0 | slot 1 | ... -->     free     <-- records |
    +------------------------------------------------------------------+

    header : next_page_id (int32) | num_slots (uint16) | free_end (uint16)
    slot   : offset (uint16) | length (uint16)        -- 4 bytes each

The slot directory grows forward from the header and record bytes grow
backward from the end of the page. ``free_end`` is the offset of the lowest
record byte. A slot whose ``offset`` is 0 is empty (deleted); offset 0 can
never hold a record because the header lives there. Slot ids are stable for
the life of a record -- compaction moves bytes but never renumbers slots --
so ``(page_id, slot_id)`` is a durable record address. Empty slots are reused
by later inserts.

``next_page_id`` lets heap files chain their pages together.
"""

from __future__ import annotations

import struct
from typing import Iterator, Optional

from .constants import INVALID_PAGE_ID, PAGE_SIZE

_HEADER = struct.Struct("<iHH")
_SLOT = struct.Struct("<HH")
HEADER_SIZE = _HEADER.size
SLOT_SIZE = _SLOT.size
MAX_RECORD_SIZE = PAGE_SIZE - HEADER_SIZE - SLOT_SIZE
_EMPTY = 0


class PageFullError(Exception):
    """Raised when a record does not fit in the page's free space."""


class SlottedPage:
    """A view that interprets a page buffer as a slotted page.

    The view does not copy: it reads and writes ``data`` in place, so it can
    wrap a buffer-pool frame directly. The caller is responsible for marking
    the frame dirty after mutating it.
    """

    __slots__ = ("data",)

    def __init__(self, data: bytearray):
        if len(data) != PAGE_SIZE:
            raise ValueError(f"page buffer must be {PAGE_SIZE} bytes")
        self.data = data

    @classmethod
    def init(cls, data: bytearray, next_page_id: int = INVALID_PAGE_ID) -> "SlottedPage":
        """Format ``data`` as an empty slotted page and return a view of it."""
        data[:] = bytes(PAGE_SIZE)
        _HEADER.pack_into(data, 0, next_page_id, 0, PAGE_SIZE)
        return cls(data)

    # -- header -------------------------------------------------------------

    @property
    def next_page_id(self) -> int:
        return _HEADER.unpack_from(self.data, 0)[0]

    @next_page_id.setter
    def next_page_id(self, page_id: int) -> None:
        _, n, fe = _HEADER.unpack_from(self.data, 0)
        _HEADER.pack_into(self.data, 0, page_id, n, fe)

    @property
    def num_slots(self) -> int:
        return _HEADER.unpack_from(self.data, 0)[1]

    @property
    def _free_end(self) -> int:
        return _HEADER.unpack_from(self.data, 0)[2]

    def _set_counts(self, num_slots: int, free_end: int) -> None:
        _HEADER.pack_into(self.data, 0, self.next_page_id, num_slots, free_end)

    # -- slot directory -----------------------------------------------------

    def _slot(self, slot_id: int) -> tuple[int, int]:
        return _SLOT.unpack_from(self.data, HEADER_SIZE + slot_id * SLOT_SIZE)

    def _set_slot(self, slot_id: int, offset: int, length: int) -> None:
        _SLOT.pack_into(self.data, HEADER_SIZE + slot_id * SLOT_SIZE, offset, length)

    def _check_slot(self, slot_id: int) -> tuple[int, int]:
        if not 0 <= slot_id < self.num_slots:
            raise KeyError(f"slot {slot_id} out of range")
        offset, length = self._slot(slot_id)
        if offset == _EMPTY:
            raise KeyError(f"slot {slot_id} is empty")
        return offset, length

    def _find_empty_slot(self) -> Optional[int]:
        for sid in range(self.num_slots):
            if self._slot(sid)[0] == _EMPTY:
                return sid
        return None

    # -- space accounting ---------------------------------------------------

    def contiguous_free_space(self) -> int:
        """Bytes between the end of the slot directory and the first record."""
        return self._free_end - (HEADER_SIZE + self.num_slots * SLOT_SIZE)

    def free_space(self) -> int:
        """Total reclaimable free bytes (including holes left by deletes)."""
        used = sum(length for _, (off, length) in self._live_slots())
        return PAGE_SIZE - HEADER_SIZE - self.num_slots * SLOT_SIZE - used

    def can_insert(self, length: int) -> bool:
        need = length if self._find_empty_slot() is not None else length + SLOT_SIZE
        return length <= MAX_RECORD_SIZE and need <= self.free_space()

    # -- record operations --------------------------------------------------

    def insert(self, record: bytes) -> int:
        """Store ``record`` and return its slot id."""
        length = len(record)
        if length > MAX_RECORD_SIZE:
            raise PageFullError(f"record of {length} B exceeds max {MAX_RECORD_SIZE} B")
        slot_id = self._find_empty_slot()
        new_slot = slot_id is None
        need = length + (SLOT_SIZE if new_slot else 0)
        if need > self.free_space():
            raise PageFullError(f"no room for {length} B record")
        if need > self.contiguous_free_space():
            self.compact()

        num_slots = self.num_slots
        if new_slot:
            slot_id = num_slots
            num_slots += 1
        offset = self._free_end - length
        self.data[offset : offset + length] = record
        self._set_counts(num_slots, offset)
        self._set_slot(slot_id, offset, length)
        return slot_id

    def get(self, slot_id: int) -> bytes:
        offset, length = self._check_slot(slot_id)
        return bytes(self.data[offset : offset + length])

    def delete(self, slot_id: int) -> None:
        self._check_slot(slot_id)
        self._set_slot(slot_id, _EMPTY, 0)
        # Trim trailing empty slots so the directory can shrink.
        n = self.num_slots
        while n > 0 and self._slot(n - 1)[0] == _EMPTY:
            n -= 1
        self._set_counts(n, self._free_end if n else PAGE_SIZE)

    def update(self, slot_id: int, record: bytes) -> None:
        """Replace a record in place, keeping its slot id.

        Raises :class:`PageFullError` if the page cannot hold the new version;
        the old record is left untouched in that case.
        """
        offset, length = self._check_slot(slot_id)
        new_len = len(record)
        if new_len <= length:
            self.data[offset : offset + new_len] = record
            self._set_slot(slot_id, offset, new_len)
            return
        if new_len - length > self.free_space():
            raise PageFullError(f"no room to grow slot {slot_id} to {new_len} B")
        # Free the old bytes, compact, then place the new version.
        self._set_slot(slot_id, _EMPTY, 0)
        if new_len > self.contiguous_free_space():
            self.compact()
        new_off = self._free_end - new_len
        self.data[new_off : new_off + new_len] = record
        self._set_counts(self.num_slots, new_off)
        self._set_slot(slot_id, new_off, new_len)

    def compact(self) -> None:
        """Pack live records against the end of the page, removing holes."""
        live = sorted(self._live_slots(), key=lambda s: s[1][0], reverse=True)
        end = PAGE_SIZE
        for sid, (off, length) in live:
            new_off = end - length
            if new_off != off:
                self.data[new_off : new_off + length] = self.data[off : off + length]
            self._set_slot(sid, new_off, length)
            end = new_off
        self._set_counts(self.num_slots, end)

    def _live_slots(self) -> Iterator[tuple[int, tuple[int, int]]]:
        for sid in range(self.num_slots):
            slot = self._slot(sid)
            if slot[0] != _EMPTY:
                yield sid, slot

    def records(self) -> Iterator[tuple[int, bytes]]:
        """Yield ``(slot_id, record)`` for every live record, in slot order."""
        for sid, (off, length) in self._live_slots():
            yield sid, bytes(self.data[off : off + length])

    def __len__(self) -> int:
        return sum(1 for _ in self._live_slots())
