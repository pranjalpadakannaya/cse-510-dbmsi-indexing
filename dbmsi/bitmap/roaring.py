"""Roaring bitmap (Lemire et al., 2018).

Row numbers are split into chunks of 65,536. A chunk that contains at least
one set bit gets a *container* holding the low 16 bits of its row numbers, in
whichever of three forms is smallest:

- an *array* container: the sorted values, 2 bytes each (sparse chunks);
- a *bitset* container: 65,536 bits, 8 KB (dense chunks);
- a *run* container: ``(start, length)`` pairs, 4 bytes each (chunks made of
  a few long runs).

Chunks without set bits take no space at all. Unlike WAH, the choice is made
per chunk, so one bitmap can be sparse in one region and dense in another
without paying for the worst case everywhere, and logical operations only
look at the chunks both operands have.

A container is never modified after it is built, and the form of a container
depends only on its contents, so equal bitmaps have equal containers.
"""

from __future__ import annotations

import struct
from array import array
from bisect import bisect_left
from typing import Iterable, Iterator, Optional

from .plain import _BYTE_BITS

CHUNK_BITS = 16
CHUNK_SIZE = 1 << CHUNK_BITS
ARRAY_MAX = 4096  # an array this long is as large as a bitset
BITSET_BYTES = CHUNK_SIZE // 8
_CHUNK_MASK = (1 << CHUNK_SIZE) - 1

ARRAY, BITSET, RUN = "array", "bitset", "run"
_KIND_IDS = {ARRAY: 0, BITSET: 1, RUN: 2}
_KIND_NAMES = {v: k for k, v in _KIND_IDS.items()}

_HEADER = struct.Struct("<II")  # number of bits | number of containers
_DESCRIPTOR = struct.Struct("<HBH")  # chunk number | kind | cardinality - 1
_RUN_COUNT = struct.Struct("<H")


def _choose(cardinality: int, runs: int) -> str:
    """The smallest form for a container, by serialized size."""
    plain = 2 * cardinality if cardinality <= ARRAY_MAX else BITSET_BYTES
    if _RUN_COUNT.size + 4 * runs < plain:
        return RUN
    return ARRAY if cardinality <= ARRAY_MAX else BITSET


def _int_from_values(values: Iterable[int]) -> int:
    buf = bytearray(BITSET_BYTES)
    for v in values:
        buf[v >> 3] |= 1 << (v & 7)
    return int.from_bytes(buf, "little")


def _values_from_int(bits: int) -> Iterator[int]:
    for i, byte in enumerate(bits.to_bytes(BITSET_BYTES, "little")):
        if byte:
            base = i << 3
            for k in _BYTE_BITS[byte]:
                yield base + k


class _Container:
    """The set bits of one chunk. Immutable once built.

    ``data`` is an ``array('H')`` of sorted values (array), a Python integer
    used as 65,536 bits (bitset), or an ``array('H')`` of alternating run
    starts and ``length - 1`` values (run).
    """

    __slots__ = ("kind", "data", "cardinality")

    def __init__(self, kind: str, data, cardinality: int):
        self.kind = kind
        self.data = data
        self.cardinality = cardinality

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, _Container):
            return NotImplemented
        return self.kind == other.kind and self.data == other.data

    # -- views --------------------------------------------------------------

    def values(self) -> Iterator[int]:
        if self.kind == ARRAY:
            yield from self.data
        elif self.kind == BITSET:
            yield from _values_from_int(self.data)
        else:
            data = self.data
            for i in range(0, len(data), 2):
                yield from range(data[i], data[i] + data[i + 1] + 1)

    def as_int(self) -> int:
        if self.kind == BITSET:
            return self.data
        if self.kind == ARRAY:
            return _int_from_values(self.data)
        bits, data = 0, self.data
        for i in range(0, len(data), 2):
            bits |= ((1 << (data[i + 1] + 1)) - 1) << data[i]
        return bits

    def contains(self, value: int) -> bool:
        if self.kind == ARRAY:
            i = bisect_left(self.data, value)
            return i < len(self.data) and self.data[i] == value
        if self.kind == BITSET:
            return bool(self.data >> value & 1)
        data = self.data
        for i in range(0, len(data), 2):
            if data[i] <= value <= data[i] + data[i + 1]:
                return True
        return False

    # -- serialization ------------------------------------------------------

    @property
    def payload_bytes(self) -> int:
        if self.kind == ARRAY:
            return 2 * len(self.data)
        if self.kind == BITSET:
            return BITSET_BYTES
        return _RUN_COUNT.size + 2 * len(self.data)

    def payload(self) -> bytes:
        if self.kind == ARRAY:
            return struct.pack(f"<{len(self.data)}H", *self.data)
        if self.kind == BITSET:
            return self.data.to_bytes(BITSET_BYTES, "little")
        return struct.pack(f"<H{len(self.data)}H", len(self.data) // 2, *self.data)


def _from_sorted(values: list[int]) -> Optional[_Container]:
    """Build a container from sorted distinct values (``None`` if empty)."""
    if not values:
        return None
    runs = array("H")
    start = previous = values[0]
    for v in values[1:]:
        if v != previous + 1:
            runs.append(start)
            runs.append(previous - start)
            start = v
        previous = v
    runs.append(start)
    runs.append(previous - start)
    kind = _choose(len(values), len(runs) // 2)
    if kind == RUN:
        return _Container(RUN, runs, len(values))
    if kind == ARRAY:
        return _Container(ARRAY, array("H", values), len(values))
    return _Container(BITSET, _int_from_values(values), len(values))


def _from_int(bits: int) -> Optional[_Container]:
    """Build a container from 65,536 bits held in an integer."""
    if not bits:
        return None
    cardinality = bits.bit_count()
    starts = bits & ~(bits << 1)  # set bits whose lower neighbour is clear
    kind = _choose(cardinality, starts.bit_count())
    if kind == BITSET:
        return _Container(BITSET, bits, cardinality)
    if kind == ARRAY:
        return _Container(ARRAY, array("H", _values_from_int(bits)), cardinality)
    ends = bits & ~(bits >> 1)  # set bits whose upper neighbour is clear
    runs = array("H")
    for first, last in zip(_values_from_int(starts), _values_from_int(ends)):
        runs.append(first)
        runs.append(last - first)
    return _Container(RUN, runs, cardinality)


def _from_range(size: int) -> Optional[_Container]:
    """A container with the values ``0 .. size - 1`` set."""
    if size <= 0:
        return None
    if _choose(size, 1) == RUN:
        return _Container(RUN, array("H", (0, size - 1)), size)
    return _from_sorted(list(range(size)))


# -- container operations ---------------------------------------------------
# Arrays are combined value by value; anything else goes through the bitset
# form, where one integer operation covers the whole chunk.


def _and(a: _Container, b: _Container) -> Optional[_Container]:
    if a.kind == ARRAY and b.kind == ARRAY:
        if len(a.data) > len(b.data):
            a, b = b, a
        other = set(b.data)
        return _from_sorted([v for v in a.data if v in other])
    if b.kind == ARRAY:
        a, b = b, a
    if a.kind == ARRAY:  # probe the larger container for each array value
        probe = b.as_int().to_bytes(BITSET_BYTES, "little")
        return _from_sorted([v for v in a.data if probe[v >> 3] >> (v & 7) & 1])
    return _from_int(a.as_int() & b.as_int())


def _or(a: _Container, b: _Container) -> Optional[_Container]:
    if a.kind == ARRAY and b.kind == ARRAY:
        return _from_sorted(sorted(set(a.data).union(b.data)))
    return _from_int(a.as_int() | b.as_int())


def _xor(a: _Container, b: _Container) -> Optional[_Container]:
    if a.kind == ARRAY and b.kind == ARRAY:
        return _from_sorted(sorted(set(a.data).symmetric_difference(b.data)))
    return _from_int(a.as_int() ^ b.as_int())


class RoaringBitmap:
    """A fixed-length sequence of bits stored as Roaring containers."""

    __slots__ = ("num_bits", "_chunks")

    encoding = "roaring"

    def __init__(self, num_bits: int = 0, _chunks: Optional[dict[int, _Container]] = None):
        if num_bits < 0:
            raise ValueError("num_bits must not be negative")
        self.num_bits = num_bits
        self._chunks: dict[int, _Container] = {} if _chunks is None else _chunks

    # -- construction -------------------------------------------------------

    @classmethod
    def zeros(cls, num_bits: int) -> "RoaringBitmap":
        return cls(num_bits)

    @classmethod
    def ones(cls, num_bits: int) -> "RoaringBitmap":
        full, rest = divmod(num_bits, CHUNK_SIZE)
        chunks = {key: _from_range(CHUNK_SIZE) for key in range(full)}
        if rest:
            chunks[full] = _from_range(rest)
        return cls(num_bits, chunks)

    @classmethod
    def from_positions(cls, positions: Iterable[int], num_bits: int) -> "RoaringBitmap":
        """Build a bitmap with the bits at ``positions`` set.

        ``positions`` must be strictly increasing.
        """
        chunks: dict[int, _Container] = {}
        key, values, previous = -1, [], -1
        for p in positions:
            if p <= previous:
                raise ValueError("positions must be strictly increasing and not negative")
            if p >= num_bits:
                raise ValueError(f"position {p} is outside [0, {num_bits})")
            k = p >> CHUNK_BITS
            if k != key:
                if values:
                    chunks[key] = _from_sorted(values)
                key, values = k, []
            values.append(p & (CHUNK_SIZE - 1))
            previous = p
        if values:
            chunks[key] = _from_sorted(values)
        return cls(num_bits, chunks)

    def append(self, bit: bool) -> None:
        """Add one bit at the end (the bit for a newly inserted row).

        A zero bit changes nothing but the length; a one bit rebuilds only
        the last container.
        """
        if bit:
            key, low = divmod(self.num_bits, CHUNK_SIZE)
            last = self._chunks.get(key)
            values = [] if last is None else list(last.values())
            values.append(low)
            self._chunks[key] = _from_sorted(values)
        self.num_bits += 1

    def resized(self, num_bits: int) -> "RoaringBitmap":
        """A copy padded with zero bits up to ``num_bits``."""
        if num_bits < self.num_bits:
            raise ValueError("a bitmap cannot be shortened")
        return RoaringBitmap(num_bits, dict(self._chunks))

    # -- inspection ---------------------------------------------------------

    def __len__(self) -> int:
        return self.num_bits

    @property
    def num_containers(self) -> int:
        return len(self._chunks)

    def container_kinds(self) -> dict[str, int]:
        """How many containers of each form the bitmap uses."""
        kinds = {ARRAY: 0, BITSET: 0, RUN: 0}
        for container in self._chunks.values():
            kinds[container.kind] += 1
        return kinds

    def get(self, position: int) -> bool:
        if not 0 <= position < self.num_bits:
            raise IndexError(f"position {position} is outside [0, {self.num_bits})")
        container = self._chunks.get(position >> CHUNK_BITS)
        return container is not None and container.contains(position & (CHUNK_SIZE - 1))

    def count(self) -> int:
        """Number of set bits, read from the container cardinalities."""
        return sum(c.cardinality for c in self._chunks.values())

    def positions(self) -> Iterator[int]:
        """Yield the positions of the set bits in increasing order."""
        for key in sorted(self._chunks):
            base = key << CHUNK_BITS
            for v in self._chunks[key].values():
                yield base + v

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, RoaringBitmap):
            return NotImplemented
        return self.num_bits == other.num_bits and self._chunks == other._chunks

    def __repr__(self) -> str:
        return (
            f"RoaringBitmap(bits={self.num_bits}, set={self.count()}, "
            f"containers={len(self._chunks)})"
        )

    # -- logical operations -------------------------------------------------
    # A shorter operand is treated as padded with zeros.

    def __and__(self, other: "RoaringBitmap") -> "RoaringBitmap":
        if not isinstance(other, RoaringBitmap):
            return NotImplemented
        small, large = self._chunks, other._chunks
        if len(small) > len(large):
            small, large = large, small
        chunks = {}
        for key, container in small.items():  # chunks missing on either side are skipped
            match = large.get(key)
            if match is not None:
                result = _and(container, match)
                if result is not None:
                    chunks[key] = result
        return RoaringBitmap(max(self.num_bits, other.num_bits), chunks)

    def _merge(self, other: "RoaringBitmap", op) -> "RoaringBitmap":
        if not isinstance(other, RoaringBitmap):
            return NotImplemented
        chunks = dict(self._chunks)  # containers are immutable, so they can be shared
        for key, container in other._chunks.items():
            mine = chunks.get(key)
            if mine is None:
                chunks[key] = container
            else:
                result = op(mine, container)
                if result is None:
                    del chunks[key]
                else:
                    chunks[key] = result
        return RoaringBitmap(max(self.num_bits, other.num_bits), chunks)

    def __or__(self, other: "RoaringBitmap") -> "RoaringBitmap":
        return self._merge(other, _or)

    def __xor__(self, other: "RoaringBitmap") -> "RoaringBitmap":
        return self._merge(other, _xor)

    def __invert__(self) -> "RoaringBitmap":
        chunks = {}
        full, rest = divmod(self.num_bits, CHUNK_SIZE)
        for key in range(full + (1 if rest else 0)):
            size = CHUNK_SIZE if key < full else rest
            container = self._chunks.get(key)
            if container is None:
                result = _from_range(size)  # an empty chunk becomes one run
            else:
                result = _from_int(container.as_int() ^ ((1 << size) - 1))
            if result is not None:
                chunks[key] = result
        return RoaringBitmap(self.num_bits, chunks)

    # -- serialization ------------------------------------------------------

    @property
    def size_bytes(self) -> int:
        """Size of :meth:`to_bytes` output."""
        return _HEADER.size + sum(
            _DESCRIPTOR.size + c.payload_bytes for c in self._chunks.values()
        )

    def to_bytes(self) -> bytes:
        parts = [_HEADER.pack(self.num_bits, len(self._chunks))]
        for key in sorted(self._chunks):
            container = self._chunks[key]
            parts.append(
                _DESCRIPTOR.pack(key, _KIND_IDS[container.kind], container.cardinality - 1)
            )
            parts.append(container.payload())
        return b"".join(parts)

    @classmethod
    def from_bytes(cls, data: bytes | bytearray | memoryview) -> "RoaringBitmap":
        try:
            num_bits, count = _HEADER.unpack_from(data, 0)
            offset = _HEADER.size
            chunks: dict[int, _Container] = {}
            for _ in range(count):
                key, kind_id, cardinality = _DESCRIPTOR.unpack_from(data, offset)
                cardinality += 1
                offset += _DESCRIPTOR.size
                kind = _KIND_NAMES[kind_id]
                if kind == ARRAY:
                    content = array("H", struct.unpack_from(f"<{cardinality}H", data, offset))
                    offset += 2 * cardinality
                elif kind == BITSET:
                    raw = bytes(data[offset : offset + BITSET_BYTES])
                    if len(raw) != BITSET_BYTES:
                        raise ValueError("bitset container is truncated")
                    content = int.from_bytes(raw, "little")
                    offset += BITSET_BYTES
                else:
                    (runs,) = _RUN_COUNT.unpack_from(data, offset)
                    offset += _RUN_COUNT.size
                    content = array("H", struct.unpack_from(f"<{2 * runs}H", data, offset))
                    offset += 4 * runs
                chunks[key] = _Container(kind, content, cardinality)
        except (struct.error, KeyError) as exc:
            raise ValueError("Roaring data is truncated or corrupt") from exc
        return cls(num_bits, chunks)
