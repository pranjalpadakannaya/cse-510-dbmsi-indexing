"""Throughput of concurrent searches and inserts on one B+ tree (workload W7).

Each measurement bulk loads a fresh tree on one attribute into its own buffer
pool, then runs a fixed mix of point searches and inserts split across
``threads`` worker threads that start together. It records operations per
second, the page I/O done, and how many inserted entries are missing
afterwards (which must be zero).

Two latching schemes are compared on identical operations:

* ``global``: :class:`~dbmsi.btree.BPlusTree`, one tree-wide latch per
  operation, so operations on the tree never overlap;
* ``crabbing``: :class:`~dbmsi.btree.CrabbingBPlusTree`, per-node latch
  crabbing with optimistic inserts.

Python's GIL serializes the CPU work of the threads, so with pages in memory
neither scheme can run faster than one thread; the numbers then show the
cost of latching. ``read_latency_ms`` gives each physical page read a wait
that releases the GIL, modeling a storage device. With it, threads overlap
their page reads only if the latching lets them: the global latch holds the
whole tree across a read, while crabbing holds only the nodes on one path.
"""

from __future__ import annotations

import random
import threading
import time
from typing import Sequence

from dbmsi.btree import BPlusTree, CrabbingBPlusTree
from dbmsi.storage import RID, BufferPoolManager, DiskManager, measure
from dbmsi.workload import FileMetadata

from .methods import codec_for

# Inserted entries point at RIDs past any heap page, so they never collide
# with the bulk-loaded ones.
_FIRST_NEW_PAGE = 1 << 30

LATCHING = {"global": BPlusTree, "crabbing": CrabbingBPlusTree}


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
    rows: Sequence[tuple[RID, FileMetadata]], attribute: str, threads: int,
    ops: Sequence[tuple], latching: str = "global", pool_size: int = 256,
    read_latency_ms: float = 0.0,
) -> dict:
    """Run ``ops`` on ``threads`` threads against a freshly loaded tree."""
    if latching not in LATCHING:
        raise ValueError(f"unknown latching {latching!r}; choose from {list(LATCHING)}")
    disk = DiskManager()
    bpm = BufferPoolManager(disk, pool_size)
    tree = LATCHING[latching](bpm, codec_for(attribute, rows))
    tree.bulk_load((getattr(r, attribute), rid) for rid, r in rows)
    tree.flush()
    bpm.clear()  # every run starts from the same cold cache
    disk.read_latency = read_latency_ms / 1000.0

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
    disk.read_latency = 0.0
    if errors:
        raise errors[0]

    inserts = [op for op in ops if op[0] == "insert"]
    lost = sum(1 for _, key, rid in inserts if rid not in tree.search(key))
    row = {
        "attribute": attribute,
        "latching": latching,
        "read_latency_ms": read_latency_ms,
        "pool_size": pool_size,
        "threads": threads,
        "ops": len(ops),
        "inserts": len(inserts),
        "seconds": seconds,
        "ops_per_second": len(ops) / seconds if seconds else float("inf"),
        "page_reads": m.delta.page_reads,
        "page_writes": m.delta.page_writes,
        "hit_rate": m.delta.hit_rate,
        "tree_height": tree.height,
        "lost_inserts": lost,
        "entries_ok": len(tree) == len(rows) + len(inserts),
        "optimistic_inserts": getattr(tree, "optimistic_inserts", None),
        "pessimistic_inserts": getattr(tree, "pessimistic_inserts", None),
    }
    disk.close()
    return row


def run_throughput(
    rows: Sequence[tuple[RID, FileMetadata]], attribute: str,
    thread_counts: Sequence[int], num_ops: int, insert_fraction: float, seed: int,
    latching: Sequence[str] = ("global", "crabbing"), pool_size: int = 256,
    read_latencies_ms: Sequence[float] = (0.0,), log=None,
) -> list[dict]:
    """One measurement per latching scheme, read latency and thread count,
    all with the same operations. ``speedup`` is relative to the first
    thread count of the same scheme and latency."""
    ops = make_ops(rows, attribute, num_ops, insert_fraction, seed)
    out = []
    for latency in read_latencies_ms:
        for scheme in latching:
            base = None
            for threads in thread_counts:
                row = measure_throughput(rows, attribute, threads, ops, scheme,
                                         pool_size, latency)
                if base is None:
                    base = row["ops_per_second"]
                row["speedup"] = row["ops_per_second"] / base if base else None
                out.append(row)
                if log:
                    log(f"throughput: {scheme}, {latency} ms reads, {threads} threads: "
                        f"{row['ops_per_second']:,.0f} ops/s, "
                        f"{row['lost_inserts']} lost inserts")
    return out
