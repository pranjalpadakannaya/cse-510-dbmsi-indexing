"""Query workloads with controlled selectivity.

A :class:`QueryGenerator` looks at a concrete dataset and produces queries
whose *selectivity* (fraction of files matched) is close to a requested
target, so experiments can sweep from needle-in-a-haystack lookups to scans
that touch most of the table:

* :class:`PointQuery` - equality on an attribute (``extension = 'pdf'``,
  ``uid = 501``), the bitmap index workload;
* :class:`RangeQuery` - inclusive ranges on numeric or temporal attributes
  (``size BETWEEN ...``, ``mtime BETWEEN ...``), the B+ tree workload;
* :class:`TermQuery` - name terms combined with AND / OR, the inverted index
  workload.

Every query carries ``expected_count``, the exact number of matching records
in the dataset it was generated from, plus a :meth:`Query.matches`
predicate. Together they form the correctness oracle that index
implementations are checked against.
"""

from __future__ import annotations

import math
import random
from bisect import bisect_left, bisect_right
from collections import Counter
from dataclasses import asdict, dataclass, field
from typing import Any, ClassVar, Optional, Sequence

from .metadata import FileMetadata

NUMERIC_ATTRIBUTES = ("size", "mtime", "ctime", "atime", "depth")
CATEGORICAL_ATTRIBUTES = ("extension", "category", "uid", "mode", "depth")


@dataclass(frozen=True, kw_only=True)
class Query:
    kind: ClassVar[str] = ""
    expected_count: int = -1
    target_selectivity: Optional[float] = None

    def matches(self, record: FileMetadata) -> bool:
        raise NotImplementedError

    def selectivity(self, num_records: int) -> float:
        return self.expected_count / num_records if num_records else 0.0

    def to_dict(self) -> dict:
        return {"kind": self.kind, **asdict(self)}

    @staticmethod
    def from_dict(d: dict) -> "Query":
        d = dict(d)
        cls = _QUERY_TYPES[d.pop("kind")]
        if cls is TermQuery:
            d["terms"] = tuple(d["terms"])
        return cls(**d)


@dataclass(frozen=True, kw_only=True)
class PointQuery(Query):
    kind: ClassVar[str] = "point"
    attribute: str
    value: Any

    def matches(self, record: FileMetadata) -> bool:
        return getattr(record, self.attribute) == self.value


@dataclass(frozen=True, kw_only=True)
class RangeQuery(Query):
    kind: ClassVar[str] = "range"
    attribute: str
    low: float
    high: float

    def matches(self, record: FileMetadata) -> bool:
        return self.low <= getattr(record, self.attribute) <= self.high


@dataclass(frozen=True, kw_only=True)
class TermQuery(Query):
    kind: ClassVar[str] = "term"
    terms: tuple[str, ...]
    op: str = "and"

    def __post_init__(self) -> None:
        if self.op not in ("and", "or"):
            raise ValueError("op must be 'and' or 'or'")
        if not self.terms:
            raise ValueError("a term query needs at least one term")

    def matches(self, record: FileMetadata) -> bool:
        tokens = set(record.tokens)
        test = all if self.op == "and" else any
        return test(t in tokens for t in self.terms)


_QUERY_TYPES = {cls.kind: cls for cls in (PointQuery, RangeQuery, TermQuery)}


def evaluate(query: Query, records: Sequence[FileMetadata]) -> list[int]:
    """File ids of the records matching ``query``, by brute force."""
    return [r.file_id for r in records if query.matches(r)]


@dataclass
class _Indexes:
    sorted_values: dict[str, list] = field(default_factory=dict)
    value_counts: dict[str, Counter] = field(default_factory=dict)
    postings: Optional[dict[str, set[int]]] = None


class QueryGenerator:
    def __init__(self, records: Sequence[FileMetadata], seed: int = 0):
        if not records:
            raise ValueError("cannot generate queries over an empty dataset")
        self.records = records
        self.n = len(records)
        self.rng = random.Random(f"{seed}:queries")
        self._ix = _Indexes()

    # -- lazily built lookup structures -------------------------------------

    def _sorted(self, attribute: str) -> list:
        if attribute not in self._ix.sorted_values:
            self._ix.sorted_values[attribute] = sorted(getattr(r, attribute) for r in self.records)
        return self._ix.sorted_values[attribute]

    def _counts(self, attribute: str) -> Counter:
        if attribute not in self._ix.value_counts:
            self._ix.value_counts[attribute] = Counter(getattr(r, attribute) for r in self.records)
        return self._ix.value_counts[attribute]

    def _postings(self) -> dict[str, set[int]]:
        if self._ix.postings is None:
            postings: dict[str, set[int]] = {}
            for i, r in enumerate(self.records):
                for t in set(r.tokens):
                    postings.setdefault(t, set()).add(i)
            self._ix.postings = postings
        return self._ix.postings

    def _check_selectivity(self, s: Optional[float]) -> None:
        if s is not None and not 0 < s <= 1:
            raise ValueError("selectivity must be in (0, 1]")

    def _pick_closest(self, candidates: dict, target: float):
        """A random candidate among those whose count is closest to ``target``.

        Distance is measured on a log scale, and anything within 25% of the
        best distance counts as a tie, which keeps workloads varied.
        """
        goal = math.log(max(target * self.n, 1e-9))
        dist = {k: abs(math.log(c) - goal) for k, c in candidates.items() if c > 0}
        best = min(dist.values())
        near = sorted((k for k, d in dist.items() if d <= best + math.log(1.25)), key=str)
        return self.rng.choice(near)

    # -- query constructors -------------------------------------------------

    def point(self, attribute: str, selectivity: Optional[float] = None) -> PointQuery:
        """Equality on ``attribute``.

        Without a target, the value of a random record is used, so frequent
        values are queried more often (as in real lookups). With a target,
        the value whose frequency is closest to it is chosen.
        """
        if attribute not in CATEGORICAL_ATTRIBUTES + NUMERIC_ATTRIBUTES:
            raise ValueError(f"unknown attribute {attribute!r}")
        self._check_selectivity(selectivity)
        counts = self._counts(attribute)
        if selectivity is None:
            value = getattr(self.rng.choice(self.records), attribute)
        else:
            value = self._pick_closest(counts, selectivity)
        return PointQuery(attribute=attribute, value=value,
                          expected_count=counts[value], target_selectivity=selectivity)

    def range(self, attribute: str, selectivity: float) -> RangeQuery:
        """An inclusive range on ``attribute`` covering about ``selectivity`` of rows.

        The window is placed uniformly at random over the sorted values.
        Ties at the edges can make the match count slightly larger than
        requested; ``expected_count`` is always exact.
        """
        if attribute not in NUMERIC_ATTRIBUTES:
            raise ValueError(f"range queries need a numeric attribute, not {attribute!r}")
        self._check_selectivity(selectivity)
        values = self._sorted(attribute)
        k = max(1, round(selectivity * self.n))
        start = self.rng.randrange(0, self.n - k + 1)
        low, high = values[start], values[start + k - 1]
        count = bisect_right(values, high) - bisect_left(values, low)
        return RangeQuery(attribute=attribute, low=low, high=high,
                          expected_count=count, target_selectivity=selectivity)

    def term(self, selectivity: Optional[float] = None) -> TermQuery:
        """A single-term lookup.

        Without a target, a term is taken from a random record's name, which
        favours common terms just like the underlying skew.
        """
        self._check_selectivity(selectivity)
        postings = self._postings()
        if selectivity is None:
            term = self._random_term()
        else:
            term = self._pick_closest({t: len(p) for t, p in postings.items()}, selectivity)
        return TermQuery(terms=(term,), expected_count=len(postings[term]),
                         target_selectivity=selectivity)

    def conjunction(self, selectivity: float, num_terms: int = 2, candidates: int = 200) -> TermQuery:
        """An AND of ``num_terms`` co-occurring terms, closest to the target found.

        Candidates are term sets taken from the names of random records, so
        every candidate matches at least one file. The best of
        ``candidates`` samples is returned; very high targets may not be
        reachable when terms rarely co-occur.
        """
        self._check_selectivity(selectivity)
        postings = self._postings()
        options: dict[tuple[str, ...], int] = {}
        for _ in range(candidates):
            tokens = sorted(set(self.rng.choice(self.records).tokens))
            if len(tokens) < num_terms:
                continue
            terms = tuple(sorted(self.rng.sample(tokens, num_terms)))
            if terms not in options:
                options[terms] = len(set.intersection(*(postings[t] for t in terms)))
        if not options:
            raise ValueError(f"no file name has {num_terms} distinct terms")
        terms = self._pick_closest(options, selectivity)
        return TermQuery(terms=terms, op="and", expected_count=options[terms],
                         target_selectivity=selectivity)

    def disjunction(self, selectivity: float, num_terms: int = 2) -> TermQuery:
        """An OR of ``num_terms`` distinct terms, each near ``selectivity / num_terms``."""
        self._check_selectivity(selectivity)
        postings = self._postings()
        if len(postings) < num_terms:
            raise ValueError(f"vocabulary has fewer than {num_terms} terms")
        pool = {t: len(p) for t, p in postings.items()}
        terms = []
        for _ in range(num_terms):
            t = self._pick_closest(pool, selectivity / num_terms)
            terms.append(t)
            del pool[t]
        terms = tuple(sorted(terms))
        count = len(set.union(*(postings[t] for t in terms)))
        return TermQuery(terms=terms, op="or", expected_count=count,
                         target_selectivity=selectivity)

    def _random_term(self) -> str:
        while True:
            tokens = self.rng.choice(self.records).tokens
            if tokens:
                return self.rng.choice(tokens)

    # -- workloads ----------------------------------------------------------

    def make(self, spec: str, selectivity: Optional[float] = None) -> Query:
        """Build one query from a spec such as ``"range:size"``, ``"point:extension"``,
        ``"term"``, ``"and"`` or ``"or"``."""
        kind, _, attribute = spec.partition(":")
        if kind == "point":
            return self.point(attribute, selectivity)
        if kind == "range":
            if selectivity is None:
                raise ValueError("range queries need a selectivity")
            return self.range(attribute, selectivity)
        if kind == "term":
            return self.term(selectivity)
        if kind in ("and", "or"):
            if selectivity is None:
                raise ValueError(f"{kind} queries need a selectivity")
            n = int(attribute) if attribute else 2
            return (self.conjunction if kind == "and" else self.disjunction)(selectivity, n)
        raise ValueError(f"unknown query spec {spec!r}")

    def sweep(self, spec: str, selectivities: Sequence[float], per_level: int) -> list[Query]:
        """``per_level`` queries of one spec at each target selectivity."""
        return [self.make(spec, s) for s in selectivities for _ in range(per_level)]

    def workload(
        self,
        num_queries: int,
        mix: dict[str, float],
        selectivities: Sequence[float],
    ) -> list[Query]:
        """A mixed workload: specs drawn by weight, targets drawn uniformly.

        ``mix`` maps query specs (see :meth:`make`) to relative weights.
        """
        specs, weights = zip(*mix.items())
        return [
            self.make(self.rng.choices(specs, weights)[0], self.rng.choice(selectivities))
            for _ in range(num_queries)
        ]
