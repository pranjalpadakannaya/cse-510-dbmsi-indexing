"""Synthetic file metadata with controlled skew, at any scale.

:class:`SyntheticGenerator` produces :class:`FileMetadata` records whose
distributions are set by a :class:`GeneratorConfig`:

* **Name terms** are drawn from a ranked vocabulary with Zipf exponent
  ``term_skew`` (0 = uniform; larger = a few terms dominate). This is the
  knob that controls posting-list length skew for the inverted index.
* **Extensions** follow given weights, optionally re-shaped to a Zipf curve
  with ``extension_skew`` (controls bitmap cardinality skew).
* **Sizes and times** follow log-normal and recency-skewed defaults, or
  quantile sketches taken from a real :class:`MetadataProfile`.
* **Directories** form a tree whose depth distribution is configurable;
  files are spread over directories with Zipf exponent ``dir_skew``.

Generation is deterministic: the same config always yields the same records,
and a smaller dataset is an exact prefix of a larger one with the same seed,
so scaling experiments compare like with like.
"""

from __future__ import annotations

import math
import random
from bisect import bisect_right
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from itertools import accumulate
from typing import Iterator, Optional, Sequence

from .metadata import FileMetadata
from .profile import MetadataProfile

DEFAULT_EXTENSIONS: dict[str, float] = {
    "txt": 9, "jpg": 8, "py": 7, "pdf": 6, "png": 6, "js": 5, "json": 5, "md": 4,
    "c": 3, "h": 3, "html": 3, "csv": 3, "log": 3, "docx": 2, "xlsx": 2, "zip": 2,
    "mp3": 2, "mp4": 1.5, "java": 1.5, "cpp": 1.5, "xml": 1.5, "sh": 1, "gz": 1,
    "mov": 1, "pptx": 1, "svg": 1, "yaml": 1, "o": 1, "": 3,
}
DEFAULT_DEPTHS: dict[int, float] = {
    0: 1, 1: 5, 2: 12, 3: 20, 4: 22, 5: 17, 6: 12, 7: 7, 8: 4,
}
DEFAULT_TERMS_PER_NAME: dict[int, float] = {1: 30, 2: 35, 3: 22, 4: 10, 5: 3}
DEFAULT_MODES: dict[int, float] = {0o644: 80, 0o755: 12, 0o600: 8}
_DEFAULT_START = datetime(2015, 1, 1, tzinfo=timezone.utc).timestamp()
_DEFAULT_END = datetime(2025, 1, 1, tzinfo=timezone.utc).timestamp()


# -- samplers ---------------------------------------------------------------


class WeightedSampler:
    """Draw items with probability proportional to their weights."""

    def __init__(self, items: Sequence, weights: Sequence[float]):
        if not items or len(items) != len(weights):
            raise ValueError("need one weight per item and at least one item")
        if any(w < 0 for w in weights) or sum(weights) <= 0:
            raise ValueError("weights must be non-negative with a positive sum")
        self.items = list(items)
        self._cum = list(accumulate(weights))

    def sample(self, rng: random.Random):
        i = bisect_right(self._cum, rng.random() * self._cum[-1])
        return self.items[min(i, len(self.items) - 1)]


def zipf_weights(n: int, s: float) -> list[float]:
    """Weights ``1 / rank ** s`` for ranks 1..n."""
    return [1.0 / (r ** s) for r in range(1, n + 1)]


class QuantileSampler:
    """Inverse-CDF sampling from a quantile sketch.

    With ``log_scale`` the interpolation between neighbouring quantiles is
    done on ``log1p`` of the values, which suits heavy-tailed sizes.
    """

    def __init__(self, qs: Sequence[float], log_scale: bool = False):
        if len(qs) < 2:
            raise ValueError("need at least two quantiles")
        self.log_scale = log_scale
        self._qs = [math.log1p(max(q, 0.0)) for q in qs] if log_scale else list(qs)

    def sample(self, rng: random.Random) -> float:
        pos = rng.random() * (len(self._qs) - 1)
        lo = int(pos)
        hi = min(lo + 1, len(self._qs) - 1)
        v = self._qs[lo] + (self._qs[hi] - self._qs[lo]) * (pos - lo)
        return math.expm1(v) if self.log_scale else v


def synthetic_words(n: int, rng: random.Random, exclude: set[str] = frozenset()) -> list[str]:
    """``n`` distinct pronounceable lowercase words (e.g. ``"tamoru"``)."""
    consonants, vowels = "bcdfghjklmnprstvwz", "aeiou"
    words: list[str] = []
    seen = set(exclude)
    while len(words) < n:
        word = "".join(
            rng.choice(consonants) + rng.choice(vowels) for _ in range(rng.randint(2, 4))
        )
        if word not in seen:
            seen.add(word)
            words.append(word)
    return words


# -- configuration ----------------------------------------------------------


@dataclass
class GeneratorConfig:
    num_files: int = 10_000
    seed: int = 0

    # File names
    vocab_size: int = 5000
    term_skew: float = 1.0
    terms_per_name: dict[int, float] = field(default_factory=lambda: dict(DEFAULT_TERMS_PER_NAME))
    numeric_term_prob: float = 0.1
    vocabulary: Optional[list[str]] = None  # ranked real terms; padded with synthetic words

    # Extensions (weights, in rank order when extension_skew is set)
    extensions: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_EXTENSIONS))
    extension_skew: Optional[float] = None

    # Sizes: log-normal around size_median unless size_quantiles is given
    size_median: float = 16 * 1024
    size_sigma: float = 2.0
    zero_size_prob: float = 0.02
    size_quantiles: Optional[list[float]] = None

    # Times: recency-skewed in [time_start, time_end] unless quantiles are given
    time_start: float = _DEFAULT_START
    time_end: float = _DEFAULT_END
    recency_skew: float = 2.0
    mtime_quantiles: Optional[list[float]] = None
    ctime_offset_quantiles: Optional[list[float]] = None
    atime_offset_quantiles: Optional[list[float]] = None

    # Directory tree
    depths: dict[int, float] = field(default_factory=lambda: dict(DEFAULT_DEPTHS))
    dirs_per_level: int = 200
    dir_skew: float = 1.0

    # Ownership and permissions
    num_users: int = 4
    user_skew: float = 1.5
    first_uid: int = 501
    uids: Optional[dict[int, float]] = None
    modes: dict[int, float] = field(default_factory=lambda: dict(DEFAULT_MODES))

    def validate(self) -> None:
        if self.num_files < 0:
            raise ValueError("num_files must be non-negative")
        if self.vocab_size < 1 or self.dirs_per_level < 1 or self.num_users < 1:
            raise ValueError("vocab_size, dirs_per_level and num_users must be positive")
        for name in ("term_skew", "dir_skew", "user_skew", "size_sigma"):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must be non-negative")
        if self.extension_skew is not None and self.extension_skew < 0:
            raise ValueError("extension_skew must be non-negative")
        for name in ("numeric_term_prob", "zero_size_prob"):
            if not 0 <= getattr(self, name) <= 1:
                raise ValueError(f"{name} must be between 0 and 1")
        if self.time_end < self.time_start or self.recency_skew <= 0:
            raise ValueError("need time_start <= time_end and recency_skew > 0")
        if not self.depths or min(self.depths) < 0:
            raise ValueError("depths must be a non-empty histogram over depths >= 0")

    @classmethod
    def from_profile(
        cls, profile: MetadataProfile, num_files: Optional[int] = None, seed: int = 0, **overrides
    ) -> "GeneratorConfig":
        """A config that reproduces the distributions measured in ``profile``.

        The vocabulary keeps the profile's most frequent terms in rank order
        and its size matches the measured vocabulary. ``term_skew`` defaults
        to the fitted exponent, so pass ``term_skew=...`` to keep everything
        else realistic while sweeping skew. Any other field can be overridden
        the same way.
        """
        if profile.num_files == 0:
            raise ValueError("cannot build a config from an empty profile")
        config = cls(
            num_files=profile.num_files if num_files is None else num_files,
            seed=seed,
            vocab_size=max(1, profile.vocabulary_size),
            term_skew=profile.term_skew,
            terms_per_name={k: float(v) for k, v in profile.terms_per_name.items()},
            numeric_term_prob=0.0,  # numeric terms are already in the real vocabulary
            vocabulary=list(profile.term_doc_freqs),
            extensions={k: float(v) for k, v in profile.extensions.items()},
            size_quantiles=list(profile.size_quantiles),
            mtime_quantiles=list(profile.mtime_quantiles),
            ctime_offset_quantiles=list(profile.ctime_offset_quantiles),
            atime_offset_quantiles=list(profile.atime_offset_quantiles),
            time_start=profile.mtime_quantiles[0],
            time_end=profile.mtime_quantiles[-1],
            depths={k: float(v) for k, v in profile.depths.items()},
            uids={k: float(v) for k, v in profile.uids.items()},
            modes={k: float(v) for k, v in profile.modes.items()},
        )
        return replace(config, **overrides)


# -- generator --------------------------------------------------------------


class SyntheticGenerator:
    def __init__(self, config: GeneratorConfig):
        config.validate()
        self.config = c = config
        setup = random.Random(f"{c.seed}:setup")

        known = list(dict.fromkeys(c.vocabulary or []))[: c.vocab_size]
        self.vocabulary = known + synthetic_words(c.vocab_size - len(known), setup, set(known))
        self._terms = WeightedSampler(self.vocabulary, zipf_weights(len(self.vocabulary), c.term_skew))
        self._terms_per_name = WeightedSampler(*zip(*c.terms_per_name.items()))

        exts = sorted(c.extensions.items(), key=lambda kv: -kv[1])
        ext_weights = (
            zipf_weights(len(exts), c.extension_skew)
            if c.extension_skew is not None
            else [w for _, w in exts]
        )
        self._extensions = WeightedSampler([e for e, _ in exts], ext_weights)

        self.directories = self._build_directories(setup)
        self._depths = WeightedSampler(*zip(*c.depths.items()))
        self._dir_samplers = {
            d: WeightedSampler(dirs, zipf_weights(len(dirs), c.dir_skew))
            for d, dirs in self.directories.items()
        }

        if c.uids:
            self._uids = WeightedSampler(*zip(*c.uids.items()))
        else:
            uids = [c.first_uid + i for i in range(c.num_users)]
            self._uids = WeightedSampler(uids, zipf_weights(len(uids), c.user_skew))
        self._modes = WeightedSampler(*zip(*c.modes.items()))

        self._sizes = QuantileSampler(c.size_quantiles, log_scale=True) if c.size_quantiles else None
        self._mtimes = QuantileSampler(c.mtime_quantiles) if c.mtime_quantiles else None
        self._ctime_offsets = (
            QuantileSampler(c.ctime_offset_quantiles) if c.ctime_offset_quantiles else None
        )
        self._atime_offsets = (
            QuantileSampler(c.atime_offset_quantiles) if c.atime_offset_quantiles else None
        )

    def _build_directories(self, rng: random.Random) -> dict[int, list[str]]:
        """Directories per depth; each level's parents come from the level above."""
        c = self.config
        levels: dict[int, list[str]] = {0: [""]}
        for depth in range(1, max(c.depths) + 1):
            parents = levels[depth - 1]
            seen: set[str] = set()
            level = []
            for _ in range(c.dirs_per_level):
                parent = rng.choice(parents)
                base = rng.choice(self.vocabulary)
                path = f"{parent}/{base}" if parent else base
                n = 2
                while path in seen:
                    path = f"{parent}/{base}{n}" if parent else f"{base}{n}"
                    n += 1
                seen.add(path)
                level.append(path)
            levels[depth] = level
        return levels

    # -- records ------------------------------------------------------------

    def records(self) -> Iterator[FileMetadata]:
        """Yield ``config.num_files`` records lazily."""
        rng = random.Random(f"{self.config.seed}:records")
        seen: dict[str, int] = {}
        for file_id in range(self.config.num_files):
            yield self._make(file_id, rng, seen)

    def generate(self) -> list[FileMetadata]:
        return list(self.records())

    def _make(self, file_id: int, rng: random.Random, seen: dict[str, int]) -> FileMetadata:
        c = self.config
        depth = self._depths.sample(rng)
        directory = self._dir_samplers[depth].sample(rng)
        stem, ext = self._name(rng)
        path = self._unique_path(directory, stem, ext, seen)

        if self._sizes is not None:
            size = int(round(self._sizes.sample(rng)))
        elif rng.random() < c.zero_size_prob:
            size = 0
        else:
            size = max(1, int(rng.lognormvariate(math.log(c.size_median), c.size_sigma)))

        if self._mtimes is not None:
            mtime = self._mtimes.sample(rng)
        else:
            span = c.time_end - c.time_start
            mtime = c.time_end - span * rng.random() ** c.recency_skew
        if self._ctime_offsets is not None:
            ctime = mtime + self._ctime_offsets.sample(rng)
        else:
            ctime = mtime if rng.random() < 0.7 else mtime + rng.expovariate(1 / 86400)
        if self._atime_offsets is not None:
            atime = mtime + self._atime_offsets.sample(rng)
        else:
            atime = mtime + (max(c.time_end, mtime) - mtime) * rng.random()

        return FileMetadata(
            file_id=file_id,
            path=path,
            size=size,
            mtime=mtime,
            ctime=ctime,
            atime=atime,
            uid=self._uids.sample(rng),
            mode=self._modes.sample(rng),
        )

    def _name(self, rng: random.Random) -> tuple[str, str]:
        c = self.config
        terms = []
        for _ in range(max(1, self._terms_per_name.sample(rng))):
            if rng.random() < c.numeric_term_prob:
                terms.append(str(rng.randrange(1, 10000)))
            else:
                terms.append(self._terms.sample(rng))
        style = rng.random()
        if style < 0.2:
            terms = [t.capitalize() for t in terms]
        elif style < 0.25:
            terms = [t.upper() for t in terms]
        sep = rng.choices(("_", "-", " "), (6, 3, 1))[0]
        return sep.join(terms), self._extensions.sample(rng)

    @staticmethod
    def _unique_path(directory: str, stem: str, ext: str, seen: dict[str, int]) -> str:
        def join(s: str) -> str:
            name = f"{s}.{ext}" if ext else s
            return f"{directory}/{name}" if directory else name

        # ``seen`` maps each used path to the last copy number tried for it,
        # so repeated collisions on a popular name stay cheap.
        base = path = join(stem)
        if base in seen:
            n = seen[base]
            while path in seen:
                n += 1
                path = join(f"{stem} ({n})")
            seen[base] = n
        seen[path] = 1
        return path


def generate(config: Optional[GeneratorConfig] = None, **kwargs) -> list[FileMetadata]:
    """Generate a dataset from ``config`` (or from ``GeneratorConfig(**kwargs)``)."""
    if config is None:
        config = GeneratorConfig(**kwargs)
    elif kwargs:
        config = replace(config, **kwargs)
    return SyntheticGenerator(config).generate()
