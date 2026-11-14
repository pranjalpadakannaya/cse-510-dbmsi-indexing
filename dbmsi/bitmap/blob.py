"""Byte strings stored across a chain of pages.

A serialized bitmap is usually larger than one page, so it is cut into
pieces and each piece goes on its own page together with the id of the next
page. Pages are allocated one after another, so a blob written to a fresh
file occupies consecutive pages and reads back as sequential I/O.
"""

from __future__ import annotations

import struct
from typing import Optional

from dbmsi.storage import INVALID_PAGE_ID, PAGE_SIZE, BufferPoolManager

_HEADER = struct.Struct("<iH")  # next page id | payload bytes on this page
PAYLOAD_SIZE = PAGE_SIZE - _HEADER.size


def blob_pages(num_bytes: int) -> int:
    """Number of pages a blob of ``num_bytes`` occupies (at least one)."""
    return max(1, -(-num_bytes // PAYLOAD_SIZE))


def write_blob(
    bpm: BufferPoolManager,
    data: bytes | bytearray | memoryview,
    first_page_id: Optional[int] = None,
) -> int:
    """Store ``data`` and return the id of its first page.

    Pass ``first_page_id`` to overwrite an existing blob: its pages are
    reused in order, more are allocated if the new data is longer, and the
    ones left over are freed. The first page id never changes.
    """
    view = memoryview(data)
    pieces = blob_pages(len(view))
    if first_page_id is None:
        page, old_next = bpm.new_page(), INVALID_PAGE_ID
    else:
        page = bpm.fetch_page(first_page_id)
        old_next = _HEADER.unpack_from(page.data, 0)[0]
    first = page.page_id

    for i in range(pieces):
        piece = view[i * PAYLOAD_SIZE : (i + 1) * PAYLOAD_SIZE]
        following, following_old_next = None, INVALID_PAGE_ID
        try:
            if i < pieces - 1:
                if old_next != INVALID_PAGE_ID:
                    following = bpm.fetch_page(old_next)
                    following_old_next = _HEADER.unpack_from(following.data, 0)[0]
                else:
                    following = bpm.new_page()
            next_id = INVALID_PAGE_ID if following is None else following.page_id
            _HEADER.pack_into(page.data, 0, next_id, len(piece))
            page.data[_HEADER.size : _HEADER.size + len(piece)] = piece
        finally:
            bpm.unpin_page(page.page_id, is_dirty=True)
        if following is None:
            break
        page, old_next = following, following_old_next

    _free_chain(bpm, old_next)
    return first


def read_blob(bpm: BufferPoolManager, first_page_id: int) -> bytes:
    """Return the bytes stored by :func:`write_blob`."""
    parts = []
    page_id = first_page_id
    while page_id != INVALID_PAGE_ID:
        with bpm.fetch(page_id) as page:
            page_id, used = _HEADER.unpack_from(page.data, 0)
            parts.append(bytes(page.data[_HEADER.size : _HEADER.size + used]))
    return b"".join(parts)


def free_blob(bpm: BufferPoolManager, first_page_id: int) -> None:
    """Give every page of a blob back to the disk manager."""
    _free_chain(bpm, first_page_id)


def _free_chain(bpm: BufferPoolManager, page_id: int) -> None:
    while page_id != INVALID_PAGE_ID:
        with bpm.fetch(page_id) as page:
            following = _HEADER.unpack_from(page.data, 0)[0]
        bpm.delete_page(page_id)
        page_id = following
