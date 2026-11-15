"""Record (tuple) identifiers."""

from __future__ import annotations

import struct
from typing import NamedTuple

_RID_STRUCT = struct.Struct("<iH")


class RID(NamedTuple):
    """Tuple identifier ``(page_id, slot_id)`` addressing a record on disk.

    Index structures store RIDs rather than memory pointers. RIDs order
    lexicographically, i.e. in physical (page, slot) order.
    """

    page_id: int
    slot_id: int

    SIZE = _RID_STRUCT.size  # 6 bytes when serialized

    def pack(self) -> bytes:
        return _RID_STRUCT.pack(self.page_id, self.slot_id)

    @classmethod
    def unpack(cls, buf: bytes | bytearray | memoryview, offset: int = 0) -> "RID":
        return cls(*_RID_STRUCT.unpack_from(buf, offset))
