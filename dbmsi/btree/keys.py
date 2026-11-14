"""Order-preserving key encodings for B+ tree entries.

Every B+ tree entry is stored as a fixed-width byte string::

    encoded key (codec.size bytes) | encoded RID (6 bytes)

Both halves are encoded so that comparing the raw bytes gives the same
order as comparing ``(key, rid)`` tuples. That lets the tree keep entries in
plain sorted ``bytes`` lists, use :mod:`bisect` directly, and treat duplicate
keys (many files with the same size or extension) as distinct entries that
differ only in their RID.
"""

from __future__ import annotations

import struct
from abc import ABC, abstractmethod
from typing import Any

from dbmsi.storage import RID

_RID_ORDERED = struct.Struct(">IH")
RID_SIZE = _RID_ORDERED.size  # 6 bytes
_INT32_BIAS = 1 << 31
_INT64_BIAS = 1 << 63

MIN_RID_BYTES = b"\x00" * RID_SIZE
MAX_RID_BYTES = b"\xff" * RID_SIZE


def encode_rid(rid: RID) -> bytes:
    """Encode a RID so byte order matches ``(page_id, slot_id)`` order."""
    return _RID_ORDERED.pack(rid.page_id + _INT32_BIAS, rid.slot_id)


def decode_rid(buf: bytes) -> RID:
    page, slot = _RID_ORDERED.unpack(buf)
    return RID(page - _INT32_BIAS, slot)


class KeyCodec(ABC):
    """Fixed-width, order-preserving encoding of one key type."""

    type_id: int
    """Identifier persisted in the tree's meta page."""

    size: int
    """Width of an encoded key in bytes."""

    @abstractmethod
    def encode(self, key: Any) -> bytes: ...

    @abstractmethod
    def decode(self, buf: bytes) -> Any: ...

    @property
    def param(self) -> int:
        """Codec parameter persisted alongside ``type_id`` (0 if unused)."""
        return 0

    @property
    def entry_size(self) -> int:
        return self.size + RID_SIZE

    def encode_entry(self, key: Any, rid: RID) -> bytes:
        return self.encode(key) + encode_rid(rid)

    def decode_entry(self, entry: bytes) -> tuple[Any, RID]:
        return self.decode(entry[: self.size]), decode_rid(entry[self.size :])

    def __eq__(self, other: object) -> bool:
        return (
            isinstance(other, KeyCodec)
            and (self.type_id, self.param) == (other.type_id, other.param)
        )

    def __hash__(self) -> int:
        return hash((self.type_id, self.param))


class IntKey(KeyCodec):
    """Signed 64-bit integers (file sizes, timestamps, inode numbers)."""

    type_id = 1
    size = 8

    def encode(self, key: int) -> bytes:
        if not isinstance(key, int) or isinstance(key, bool):
            raise TypeError(f"IntKey expects int, got {type(key).__name__}")
        if not -_INT64_BIAS <= key < _INT64_BIAS:
            raise OverflowError(f"key {key} does not fit in 64 bits")
        return (key + _INT64_BIAS).to_bytes(8, "big")

    def decode(self, buf: bytes) -> int:
        return int.from_bytes(buf, "big") - _INT64_BIAS

    def __repr__(self) -> str:
        return "IntKey()"


class StringKey(KeyCodec):
    """UTF-8 strings of at most ``max_len`` encoded bytes (names, extensions).

    Keys are NUL-padded to ``max_len``, so byte order matches the order of the
    UTF-8 encodings (i.e. code point order). Strings containing NUL are
    rejected because the padding would make them ambiguous.
    """

    type_id = 2

    def __init__(self, max_len: int):
        if not 1 <= max_len <= 1024:
            raise ValueError("max_len must be between 1 and 1024 bytes")
        self.size = max_len

    @property
    def param(self) -> int:
        return self.size

    def encode(self, key: str) -> bytes:
        if not isinstance(key, str):
            raise TypeError(f"StringKey expects str, got {type(key).__name__}")
        raw = key.encode("utf-8")
        if len(raw) > self.size:
            raise ValueError(f"key is {len(raw)} B, max is {self.size} B")
        if b"\x00" in raw:
            raise ValueError("string keys may not contain NUL")
        return raw.ljust(self.size, b"\x00")

    def decode(self, buf: bytes) -> str:
        return bytes(buf).rstrip(b"\x00").decode("utf-8")

    def __repr__(self) -> str:
        return f"StringKey({self.size})"


def codec_from_meta(type_id: int, param: int) -> KeyCodec:
    """Rebuild the codec recorded in a tree's meta page."""
    if type_id == IntKey.type_id:
        return IntKey()
    if type_id == StringKey.type_id:
        return StringKey(param)
    raise ValueError(f"unknown key codec type {type_id}")
