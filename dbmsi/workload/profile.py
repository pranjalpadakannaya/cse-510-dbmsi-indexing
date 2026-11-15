"""Summaries of file metadata distributions.

A :class:`MetadataProfile` captures what the synthetic generator needs to
imitate a real file system: categorical frequencies (extensions, owners,
permission modes, directory depths, terms per name), quantile sketches of
the numeric attributes (size, modification time, and how far ``ctime`` and
``atime`` sit from ``mtime``), document frequencies of name terms, and
fitted Zipf exponents that quantify term and extension skew.

Profiles are plain data and round-trip through JSON, so a profile measured
on one machine can drive generation on another without sharing the
underlying file names beyond the most frequent terms.
"""

from __future__ import annotations

import json
import math
from collections import Counter
from dataclasses import asdict, dataclass, field
from typing import Iterable

from .metadata import FileMetadata, split_extension, tokenize

NUM_QUANTILES = 101  # 0th, 1st, ..., 100th percentile


def quantiles(values: list[float], n: int = NUM_QUANTILES) -> list[float]:
    """``n`` evenly spaced quantiles of ``values`` (linear interpolation)."""
    if not values:
        return []
    xs = sorted(values)
    out = []
    for i in range(n):
        pos = i * (len(xs) - 1) / (n - 1)
        lo = int(pos)
        hi = min(lo + 1, len(xs) - 1)
        out.append(xs[lo] + (xs[hi] - xs[lo]) * (pos - lo))
    return out


def fit_zipf_exponent(frequencies: Iterable[int], max_ranks: int = 1000) -> float:
    """Estimate ``s`` in ``freq(rank) ~ rank ** -s`` by least squares on log-log.

    Only the ``max_ranks`` most frequent items are used, since the long tail
    of singletons flattens the curve. Returns 0.0 for fewer than two items.
    """
    freqs = sorted((f for f in frequencies if f > 0), reverse=True)[:max_ranks]
    if len(freqs) < 2:
        return 0.0
    xs = [math.log(r) for r in range(1, len(freqs) + 1)]
    ys = [math.log(f) for f in freqs]
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    return max(0.0, -sxy / sxx)


def stem_tokens(name: str) -> list[str]:
    """Terms of a file name excluding its extension."""
    return tokenize(split_extension(name)[0])


@dataclass
class MetadataProfile:
    num_files: int = 0
    extensions: dict[str, int] = field(default_factory=dict)
    categories: dict[str, int] = field(default_factory=dict)
    uids: dict[int, int] = field(default_factory=dict)
    modes: dict[int, int] = field(default_factory=dict)
    depths: dict[int, int] = field(default_factory=dict)
    terms_per_name: dict[int, int] = field(default_factory=dict)
    term_doc_freqs: dict[str, int] = field(default_factory=dict)
    vocabulary_size: int = 0
    size_quantiles: list[float] = field(default_factory=list)
    mtime_quantiles: list[float] = field(default_factory=list)
    ctime_offset_quantiles: list[float] = field(default_factory=list)
    atime_offset_quantiles: list[float] = field(default_factory=list)
    term_skew: float = 0.0
    extension_skew: float = 0.0

    @classmethod
    def from_records(
        cls, records: Iterable[FileMetadata], top_terms: int = 5000
    ) -> "MetadataProfile":
        """Build a profile in one pass over ``records``.

        Term document frequencies are counted over the file-name stem (the
        extension is profiled separately) and only the ``top_terms`` most
        frequent terms are kept, along with the total vocabulary size.
        """
        exts, cats, uids, modes, depths, per_name = (Counter() for _ in range(6))
        term_df: Counter = Counter()
        sizes, mtimes, c_off, a_off = [], [], [], []
        n = 0
        for r in records:
            n += 1
            exts[r.extension] += 1
            cats[r.category] += 1
            uids[r.uid] += 1
            modes[r.mode] += 1
            depths[r.depth] += 1
            terms = stem_tokens(r.name)
            per_name[len(terms)] += 1
            term_df.update(set(terms))
            sizes.append(r.size)
            mtimes.append(r.mtime)
            c_off.append(r.ctime - r.mtime)
            a_off.append(r.atime - r.mtime)
        return cls(
            num_files=n,
            extensions=dict(exts.most_common()),
            categories=dict(cats.most_common()),
            uids=dict(uids.most_common()),
            modes=dict(modes.most_common()),
            depths=dict(sorted(depths.items())),
            terms_per_name=dict(sorted(per_name.items())),
            term_doc_freqs=dict(term_df.most_common(top_terms)),
            vocabulary_size=len(term_df),
            size_quantiles=quantiles(sizes),
            mtime_quantiles=quantiles(mtimes),
            ctime_offset_quantiles=quantiles(c_off),
            atime_offset_quantiles=quantiles(a_off),
            term_skew=fit_zipf_exponent(term_df.values()),
            extension_skew=fit_zipf_exponent(exts.values()),
        )

    # -- persistence --------------------------------------------------------

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2)

    @classmethod
    def from_json(cls, text: str) -> "MetadataProfile":
        d = json.loads(text)
        # JSON object keys are strings; restore integer-keyed histograms.
        for key in ("uids", "modes", "depths", "terms_per_name"):
            d[key] = {int(k): v for k, v in d[key].items()}
        return cls(**d)

    def save(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as f:
            f.write(self.to_json())

    @classmethod
    def load(cls, path: str) -> "MetadataProfile":
        with open(path, encoding="utf-8") as f:
            return cls.from_json(f.read())

    # -- reporting ----------------------------------------------------------

    def summary(self, top: int = 8) -> str:
        """Human-readable overview of the main distributions."""

        def pct(count: int) -> str:
            return f"{100 * count / self.num_files:.1f}%" if self.num_files else "-"

        def top_items(d: dict) -> str:
            return ", ".join(
                f"{'(none)' if k == '' else k} {pct(v)}" for k, v in list(d.items())[:top]
            )

        def q(qs: list[float], p: int) -> float:
            return qs[p] if qs else 0.0

        lines = [
            f"files:            {self.num_files}",
            f"extensions:       {len(self.extensions)} distinct, skew {self.extension_skew:.2f}",
            f"  top:            {top_items(self.extensions)}",
            f"categories:       {top_items(self.categories)}",
            f"name terms:       {self.vocabulary_size} distinct, skew {self.term_skew:.2f}",
            f"  top:            {top_items(self.term_doc_freqs)}",
            f"terms per name:   {top_items(self.terms_per_name)}",
            f"depth:            {top_items(self.depths)}",
            f"owners (uid):     {top_items(self.uids)}",
            "size (bytes):     p10 {:.0f}, p50 {:.0f}, p90 {:.0f}, p99 {:.0f}, max {:.0f}".format(
                *(q(self.size_quantiles, p) for p in (10, 50, 90, 99, 100))
            ),
        ]
        return "\n".join(lines)
