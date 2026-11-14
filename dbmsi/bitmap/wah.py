"""Word-Aligned Hybrid (WAH) compressed bitmap (Wu, Otoo and Shoshani, 2006).

The bits are cut into groups of 31 and stored in 32-bit words of two kinds:

- a *literal* word (top bit 0) holds one group as it is;
- a *fill* word (top bit 1) stands for a run of identical groups: the next
  bit says whether the groups are all zeros or all ones, and the low 30 bits
  count how many groups the run covers.

AND, OR, XOR and NOT work on the words directly, without decompressing. A
fill is handled in one step however long its run is, so sparse bitmaps are
both small and fast to combine. A bitmap with no long runs costs 32 bits per
31 bits of data, about 3% more than storing it uncompressed.

The last group is padded with zero bits when ``num_bits`` is not a multiple
of 31, and every operation keeps that padding zero. Bitmaps are always built
through the same two helpers, so equal bitmaps have equal words.
"""

from __future__ import annotations

import operator
import struct
from typing import Callable, Iterable, Iterator, Optional

GROUP_BITS = 31
ALL_ONES = (1 << GROUP_BITS) - 1
FILL_FLAG = 1 << 31
FILL_BIT = 1 << 30
MAX_RUN = (1 << 30) - 1

_HEADER = struct.Struct("<I")  # number of bits


def _num_groups(num_bits: int) -> int:
    return (num_bits + GROUP_BITS - 1) // GROUP_BITS


def _append_fill(words: list[int], bit: int, groups: int) -> None:
    """Append ``groups`` identical groups, extending a matching fill word."""
    if groups <= 0:
        return
    head = FILL_FLAG | (FILL_BIT if bit else 0)
    if words:
        last = words[-1]
        if last & ~MAX_RUN == head:
            take = min(groups, MAX_RUN - (last & MAX_RUN))
            words[-1] = last + take
            groups -= take
    while groups > 0:
        take = min(groups, MAX_RUN)
        words.append(head | take)
        groups -= take


def _append_group(words: list[int], literal: int) -> None:
    """Append one group, as a fill when it is all zeros or all ones."""
    if literal == 0:
        _append_fill(words, 0, 1)
    elif literal == ALL_ONES:
        _append_fill(words, 1, 1)
    else:
        words.append(literal)


def _pop_group(words: list[int]) -> int:
    """Remove the last group and return it as a literal."""
    last = words[-1]
    if not last & FILL_FLAG:
        words.pop()
        return last
    if last & MAX_RUN == 1:
        words.pop()
    else:
        words[-1] = last - 1
    return ALL_ONES if last & FILL_BIT else 0


class _Cursor:
    """Reads a word list as a sequence of runs of groups."""

    __slots__ = ("_words", "_next", "is_fill", "value", "left")

    def __init__(self, words: list[int]):
        self._words = words
        self._next = 0
        self.is_fill = False
        self.value = 0  # the group as a literal: for a fill, 0 or ALL_ONES
        self.left = 0  # groups remaining in the current run; 0 at the end
        self._load()

    def _load(self) -> None:
        while self.left == 0 and self._next < len(self._words):
            word = self._words[self._next]
            self._next += 1
            if word & FILL_FLAG:
                self.is_fill = True
                self.value = ALL_ONES if word & FILL_BIT else 0
                self.left = word & MAX_RUN
            else:
                self.is_fill = False
                self.value = word
                self.left = 1

    def consume(self, groups: int) -> None:
        """Advance by ``groups``, which must fit in the current run."""
        self.left -= groups
        if self.left == 0:
            self._load()

    def skip(self, groups: int) -> None:
        """Advance by ``groups`` across any number of runs."""
        while groups > 0 and self.left:
            take = min(groups, self.left)
            groups -= take
            self.consume(take)


def _combine(
    a_words: list[int],
    b_words: list[int],
    op: Callable[[int, int], int],
    absorbing: Optional[int],
) -> list[int]:
    """Combine two word lists covering the same number of groups.

    ``absorbing`` is the group value that decides the result alone (zeros for
    AND, ones for OR). A fill of that value lets the other operand be skipped
    for the whole run without looking at its words.
    """
    out: list[int] = []
    a, b = _Cursor(a_words), _Cursor(b_words)
    while a.left and b.left:
        if a.is_fill and b.is_fill:
            groups = min(a.left, b.left)
            _append_fill(out, 1 if op(a.value, b.value) else 0, groups)
            a.consume(groups)
            b.consume(groups)
        elif a.is_fill and a.value == absorbing:
            groups = a.left
            _append_fill(out, 1 if absorbing else 0, groups)
            a.consume(groups)
            b.skip(groups)
        elif b.is_fill and b.value == absorbing:
            groups = b.left
            _append_fill(out, 1 if absorbing else 0, groups)
            b.consume(groups)
            a.skip(groups)
        else:
            _append_group(out, op(a.value, b.value))
            a.consume(1)
            b.consume(1)
    return out


class WAHBitmap:
    """A fixed-length sequence of bits stored with WAH compression."""

    __slots__ = ("num_bits", "_words")

    encoding = "wah"

    def __init__(self, num_bits: int = 0, _words: Optional[list[int]] = None):
        if num_bits < 0:
            raise ValueError("num_bits must not be negative")
        self.num_bits = num_bits
        if _words is None:
            _words = []
            _append_fill(_words, 0, _num_groups(num_bits))
        self._words = _words

    # -- construction -------------------------------------------------------

    @classmethod
    def zeros(cls, num_bits: int) -> "WAHBitmap":
        return cls(num_bits)

    @classmethod
    def ones(cls, num_bits: int) -> "WAHBitmap":
        words: list[int] = []
        full, rest = divmod(num_bits, GROUP_BITS)
        _append_fill(words, 1, full)
        if rest:
            _append_group(words, (1 << rest) - 1)
        return cls(num_bits, words)

    @classmethod
    def from_positions(cls, positions: Iterable[int], num_bits: int) -> "WAHBitmap":
        """Build a bitmap with the bits at ``positions`` set.

        ``positions`` must be strictly increasing.
        """
        words: list[int] = []
        group, literal, previous = -1, 0, -1
        for p in positions:
            if p <= previous:
                raise ValueError("positions must be strictly increasing and not negative")
            if p >= num_bits:
                raise ValueError(f"position {p} is outside [0, {num_bits})")
            g, k = divmod(p, GROUP_BITS)
            if g != group:
                if group >= 0:
                    _append_group(words, literal)
                _append_fill(words, 0, g - group - 1)
                group, literal = g, 0
            literal |= 1 << k
            previous = p
        if group >= 0:
            _append_group(words, literal)
        _append_fill(words, 0, _num_groups(num_bits) - group - 1)
        return cls(num_bits, words)

    def append(self, bit: bool) -> None:
        """Add one bit at the end (the bit for a newly inserted row).

        Only the last word changes, whatever the length of the bitmap.
        """
        k = self.num_bits % GROUP_BITS
        if k == 0:
            _append_group(self._words, 1 if bit else 0)
        elif bit:
            _append_group(self._words, _pop_group(self._words) | 1 << k)
        self.num_bits += 1

    def resized(self, num_bits: int) -> "WAHBitmap":
        """A copy padded with zero bits up to ``num_bits``."""
        if num_bits < self.num_bits:
            raise ValueError("a bitmap cannot be shortened")
        words = list(self._words)
        _append_fill(words, 0, _num_groups(num_bits) - _num_groups(self.num_bits))
        return WAHBitmap(num_bits, words)

    # -- inspection ---------------------------------------------------------

    def __len__(self) -> int:
        return self.num_bits

    @property
    def num_words(self) -> int:
        return len(self._words)

    def get(self, position: int) -> bool:
        if not 0 <= position < self.num_bits:
            raise IndexError(f"position {position} is outside [0, {self.num_bits})")
        target, k = divmod(position, GROUP_BITS)
        group = 0
        for word in self._words:
            if word & FILL_FLAG:
                group += word & MAX_RUN
                if target < group:
                    return bool(word & FILL_BIT)
            else:
                if target == group:
                    return bool(word >> k & 1)
                group += 1
        return False

    def count(self) -> int:
        """Number of set bits, computed without decompressing."""
        total = 0
        for word in self._words:
            if word & FILL_FLAG:
                if word & FILL_BIT:
                    total += GROUP_BITS * (word & MAX_RUN)
            else:
                total += word.bit_count()
        return total

    def positions(self) -> Iterator[int]:
        """Yield the positions of the set bits in increasing order."""
        base = 0
        for word in self._words:
            if word & FILL_FLAG:
                span = GROUP_BITS * (word & MAX_RUN)
                if word & FILL_BIT:
                    yield from range(base, base + span)
                base += span
            else:
                while word:
                    low = word & -word
                    yield base + low.bit_length() - 1
                    word ^= low
                base += GROUP_BITS

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, WAHBitmap):
            return NotImplemented
        return self.num_bits == other.num_bits and self._words == other._words

    def __repr__(self) -> str:
        return f"WAHBitmap(bits={self.num_bits}, set={self.count()}, words={len(self._words)})"

    # -- logical operations -------------------------------------------------
    # A shorter operand is treated as padded with zeros.

    def _binary(self, other: "WAHBitmap", op, absorbing: Optional[int]) -> "WAHBitmap":
        if not isinstance(other, WAHBitmap):
            return NotImplemented
        num_bits = max(self.num_bits, other.num_bits)
        a = self if self.num_bits == num_bits else self.resized(num_bits)
        b = other if other.num_bits == num_bits else other.resized(num_bits)
        return WAHBitmap(num_bits, _combine(a._words, b._words, op, absorbing))

    def __and__(self, other: "WAHBitmap") -> "WAHBitmap":
        return self._binary(other, operator.and_, 0)

    def __or__(self, other: "WAHBitmap") -> "WAHBitmap":
        return self._binary(other, operator.or_, ALL_ONES)

    def __xor__(self, other: "WAHBitmap") -> "WAHBitmap":
        return self._binary(other, operator.xor, None)

    def __invert__(self) -> "WAHBitmap":
        out: list[int] = []
        for word in self._words:
            if word & FILL_FLAG:
                _append_fill(out, 0 if word & FILL_BIT else 1, word & MAX_RUN)
            else:
                _append_group(out, word ^ ALL_ONES)
        rest = self.num_bits % GROUP_BITS
        if rest:  # the padding of the last group must stay zero
            _append_group(out, _pop_group(out) & ((1 << rest) - 1))
        return WAHBitmap(self.num_bits, out)

    # -- serialization ------------------------------------------------------

    @property
    def size_bytes(self) -> int:
        """Size of :meth:`to_bytes` output."""
        return _HEADER.size + 4 * len(self._words)

    def to_bytes(self) -> bytes:
        return struct.pack(f"<I{len(self._words)}I", self.num_bits, *self._words)

    @classmethod
    def from_bytes(cls, data: bytes | bytearray | memoryview) -> "WAHBitmap":
        (num_bits,) = _HEADER.unpack_from(data, 0)
        count = (len(data) - _HEADER.size) // 4
        words = list(struct.unpack_from(f"<{count}I", data, _HEADER.size))
        covered = sum(w & MAX_RUN if w & FILL_FLAG else 1 for w in words)
        if covered != _num_groups(num_bits):
            raise ValueError("WAH data does not match its bit count")
        return cls(num_bits, words)
