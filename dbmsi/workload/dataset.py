"""JSON Lines files for metadata datasets and query workloads.

One JSON object per line keeps files streamable, diff-friendly, and easy to
load from other tools (``pandas.read_json(path, lines=True)``).
"""

from __future__ import annotations

import json
from typing import Iterable, Iterator

from .metadata import FileMetadata
from .queries import Query


def write_records(path: str, records: Iterable[FileMetadata]) -> int:
    """Write records to ``path``; returns how many were written."""
    n = 0
    with open(path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r.to_dict(), ensure_ascii=False) + "\n")
            n += 1
    return n


def iter_records(path: str) -> Iterator[FileMetadata]:
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                yield FileMetadata.from_dict(json.loads(line))


def read_records(path: str) -> list[FileMetadata]:
    return list(iter_records(path))


def write_queries(path: str, queries: Iterable[Query]) -> int:
    n = 0
    with open(path, "w", encoding="utf-8") as f:
        for q in queries:
            f.write(json.dumps(q.to_dict(), ensure_ascii=False) + "\n")
            n += 1
    return n


def read_queries(path: str) -> list[Query]:
    with open(path, encoding="utf-8") as f:
        return [Query.from_dict(json.loads(line)) for line in f if line.strip()]
