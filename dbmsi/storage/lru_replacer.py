"""Least-recently-used victim selection over unpinned buffer frames."""

from __future__ import annotations

from collections import OrderedDict
from typing import Optional


class LRUReplacer:
    """Tracks evictable (pin count 0) frames in least-recently-unpinned order.

    A frame becomes evictable when its last pin is released and stops being
    evictable as soon as it is pinned again. :meth:`victim` returns the frame
    that has been evictable the longest.
    """

    def __init__(self) -> None:
        self._frames: OrderedDict[int, None] = OrderedDict()

    def unpin(self, frame_id: int) -> None:
        """Mark ``frame_id`` evictable and most recently used."""
        self._frames[frame_id] = None
        self._frames.move_to_end(frame_id)

    def pin(self, frame_id: int) -> None:
        """Mark ``frame_id`` not evictable."""
        self._frames.pop(frame_id, None)

    def victim(self) -> Optional[int]:
        if not self._frames:
            return None
        frame_id, _ = self._frames.popitem(last=False)
        return frame_id

    def __len__(self) -> int:
        return len(self._frames)
