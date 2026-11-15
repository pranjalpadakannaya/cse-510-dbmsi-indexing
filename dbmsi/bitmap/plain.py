"""Uncompressed bitmap: the baseline every compressed encoding is compared to.

Bit ``i`` is set when row ``i`` has the indexed value. The bits are held in a
single Python integer, so AND, OR and NOT are one big-integer operation each.
Serialized, a bitmap takes one bit per row whatever its contents, which is
exactly the cost that compression is meant to avoid.
"""

from __future__ import annotations

import struct
from typing import Iterable, Iterator

_HEADER = struct.Struct("<I")  # number of bits

# Positions of the set bits in each possible byte value.
_BYTE_BITS = tuple(tuple(k for k in range(8) if b >> k & 1) for b in range(256))


class Bitmap:
    """A fixed-length sequence of bits stored without compression."""

    __slots__ = ("num_bits", "_bits")

    encoding = "plain"

    def __init__(self, num_bits: int = 0, _bits: int = 0):
        if num_bits < 0:
            raise ValueError("num_bits must not be negative")
        self.num_bits = num_bits
        self._bits = _bits

    # -- construction -------------------------------------------------------

    @classmethod
    def zeros(cls, num_bits: int) -> "Bitmap":
        return cls(num_bits)

    @classmethod
    def ones(cls, num_bits: int) -> "Bitmap":
        return cls(num_bits, (1 << num_bits) - 1)

    @classmethod
    def from_positions(cls, positions: Iterable[int], num_bits: int) -> "Bitmap":
        """Build a bitmap with the bits at ``positions`` set."""
        buf = bytearray((num_bits + 7) // 8)
        for p in positions:
            if not 0 <= p < num_bits:
                raise ValueError(f"position {p} is outside [0, {num_bits})")
            buf[p >> 3] |= 1 << (p & 7)
        return cls(num_bits, int.from_bytes(buf, "little"))

    def append(self, bit: bool) -> None:
        """Add one bit at the end (the bit for a newly inserted row)."""
        if bit:
            self._bits |= 1 << self.num_bits
        self.num_bits += 1

    def resized(self, num_bits: int) -> "Bitmap":
        """A copy padded with zero bits up to ``num_bits``."""
        if num_bits < self.num_bits:
            raise ValueError("a bitmap cannot be shortened")
        return Bitmap(num_bits, self._bits)

    # -- inspection ---------------------------------------------------------

    def __len__(self) -> int:
        return self.num_bits

    def get(self, position: int) -> bool:
        if not 0 <= position < self.num_bits:
            raise IndexError(f"position {position} is outside [0, {self.num_bits})")
        return bool(self._bits >> position & 1)

    def count(self) -> int:
        """Number of set bits."""
        return self._bits.bit_count()

    def positions(self) -> Iterator[int]:
        """Yield the positions of the set bits in increasing order."""
        data = self._bits.to_bytes((self.num_bits + 7) // 8, "little")
        for i, byte in enumerate(data):
            if byte:
                base = i << 3
                for k in _BYTE_BITS[byte]:
                    yield base + k

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Bitmap):
            return NotImplemented
        return self.num_bits == other.num_bits and self._bits == other._bits

    def __repr__(self) -> str:
        return f"Bitmap(bits={self.num_bits}, set={self.count()})"

    # -- logical operations -------------------------------------------------
    # A shorter operand is treated as padded with zeros.

    def __and__(self, other: "Bitmap") -> "Bitmap":
        return Bitmap(max(self.num_bits, other.num_bits), self._bits & other._bits)

    def __or__(self, other: "Bitmap") -> "Bitmap":
        return Bitmap(max(self.num_bits, other.num_bits), self._bits | other._bits)

    def __xor__(self, other: "Bitmap") -> "Bitmap":
        return Bitmap(max(self.num_bits, other.num_bits), self._bits ^ other._bits)

    def __invert__(self) -> "Bitmap":
        return Bitmap(self.num_bits, self._bits ^ ((1 << self.num_bits) - 1))

    # -- serialization ------------------------------------------------------

    @property
    def size_bytes(self) -> int:
        """Size of :meth:`to_bytes` output."""
        return _HEADER.size + (self.num_bits + 7) // 8

    def to_bytes(self) -> bytes:
        return _HEADER.pack(self.num_bits) + self._bits.to_bytes(
            (self.num_bits + 7) // 8, "little"
        )

    @classmethod
    def from_bytes(cls, data: bytes | bytearray | memoryview) -> "Bitmap":
        (num_bits,) = _HEADER.unpack_from(data, 0)
        body = bytes(data[_HEADER.size : _HEADER.size + (num_bits + 7) // 8])
        if len(body) != (num_bits + 7) // 8:
            raise ValueError("bitmap data is truncated")
        return cls(num_bits, int.from_bytes(body, "little"))
