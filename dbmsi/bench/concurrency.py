"""Throughput of concurrent searches and inserts on one B+ tree (workload W7).

Each measurement bulk loads a fresh tree on one attribute, then runs a fixed
mix of point searches and inserts split across ``threads`` worker threads
that start together. It records operations per second, the page I/O done,
and how many inserted entries are missing afterwards (which must be zero).

The trees in :mod:`dbmsi.btree` take one tree-wide latch per operation, so
this measures that global-lock baseline; a latch-crabbing tree can be run
through the same function to compare. Python's GIL also serializes the work
of the threads themselves, so the numbers show the cost of contention, not
parallel speedup.
"""

from __future__ import annotations

import random
import threading
import time
from typing import Sequence

from dbmsi.btree import BPlusTree
from dbmsi.storage import RID, BufferPoolManager, measure
from dbmsi.workload import FileMetadata

from .methods import codec_for

# Inserted entries point at RIDs past any heap page, so they never collide
# with the bulk-loaded ones.
_FIRST_NEW_PAGE = 1 << 30


def make_ops(
    rows: Sequence[tuple[RID, FileMetadata]], attribute: str, num_ops: int,
    insert_fraction: float, seed: int,
) -> list[tuple]:
    """``("search", key)`` and ``("insert", key, rid)`` operations; keys are
    drawn from the data, so searches hit and inserts land among existing keys."""
    rng = random.Random(seed)
    ops = []
    for i in range(num_ops):
        key = getattr(rows[rng.randrange(len(rows))][1], attribute)
        if rng.random() < insert_fraction:
            ops.append(("insert", key, RID(_FIRST_NEW_PAGE + i, 0)))
        else:
            ops.append(("search", key))
    return ops


def measure_throughput(
    bpm: BufferPoolManager, rows: Sequence[tuple[RID, FileMetadata]], attribute: str,
    threads: int, ops: Sequence[tuple],
) -> dict:
    tree = BPlusTree(bpm, codec_for(attribute, rows))
    tree.bulk_load((getattr(r, attribute), rid) for rid, r in rows)
    tree.flush()
    bpm.flush_all()

    shares = [ops[i::threads] for i in range(threads)]
    start = threading.Barrier(threads + 1)
    errors: list[BaseException] = []

    def work(share: Sequence[tuple]) -> None:
        start.wait()
        try:
            for op in share:
                if op[0] == "search":
                    tree.search(op[1])
                else:
                    tree.insert(op[1], op[2])
        except BaseException as e:  # reported below, after the threads join
            errors.append(e)

    workers = [threading.Thread(target=work, args=(s,)) for s in shares]
    for w in workers:
        w.start()
    with measure(bpm.stats) as m:
        start.wait()
        t = time.perf_counter()
        for w in workers:
            w.join()
        seconds = time.perf_counter() - t
    if errors:
        raise errors[0]

    inserts = [op for op in ops if op[0] == "insert"]
    lost = sum(1 for _, key, rid in inserts if rid not in tree.search(key))
    return {
        "attribute": attribute,
        "latching": "tree-wide latch",
        "threads": threads,
        "ops": len(ops),
        "inserts": len(inserts),
        "seconds": seconds,
        "ops_per_second": len(ops) / seconds if seconds else float("inf"),
        "page_reads": m.delta.page_reads,
        "page_writes": m.delta.page_writes,
        "lost_inserts": lost,
        "entries_ok": len(tree) == len(rows) + len(inserts),
    }


def run_throughput(
    bpm: BufferPoolManager, rows: Sequence[tuple[RID, FileMetadata]], attribute: str,
    thread_counts: Sequence[int], num_ops: int, insert_fraction: float, seed: int,
) -> list[dict]:
    """One measurement per thread count, all with the same operations."""
    ops = make_ops(rows, attribute, num_ops, insert_fraction, seed)
    out = []
    for threads in thread_counts:
        bpm.clear()
        out.append(measure_throughput(bpm, rows, attribute, threads, ops))
    return out
