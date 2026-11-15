"""Benchmark output: raw results, summary tables and figures.

A results directory holds:

* ``results.json`` - everything, including the resolved config and the
  environment, so a run can be re-reported or reproduced;
* ``queries.csv`` - one row per (query, method);
* ``summary.csv`` - means per (spec, target selectivity, method);
* ``build.csv`` and ``updates.csv`` - build time, size and insert cost;
* ``report.md`` - the tables above in Markdown, ready for the slides;
* ``*.png`` - figures, when matplotlib is installed.

The summary and figures are computed from ``results.json`` alone, so
``python -m dbmsi.bench report DIR`` can redraw them without re-running.
"""

from __future__ import annotations

import csv
import json
import math
import os
import statistics
from collections import defaultdict
from typing import Optional

from .harness import IO_FIELDS, BenchmarkResult

SUMMARY_FIELDS = ("page_reads", "sequential_reads", "random_reads", "io_cost")


def summarize(result: BenchmarkResult) -> list[dict]:
    """Means of the I/O counters and the median latency per group."""
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for row in result.queries:
        groups[(row["spec"], row["target_selectivity"], row["method"])].append(row)
    out = []
    for (spec, target, method), rows in groups.items():
        out.append({
            "spec": spec,
            "target_selectivity": target,
            "method": method,
            "queries": len(rows),
            "correct": sum(r["correct"] for r in rows),
            "selectivity": statistics.fmean(r["selectivity"] for r in rows),
            "result_count": statistics.fmean(r["result_count"] for r in rows),
            **{f: statistics.fmean(r[f] for r in rows) for f in SUMMARY_FIELDS},
            "latency_ms": statistics.median(r["latency_ms"] for r in rows),
        })
    order = {m: i for i, m in enumerate(_methods(result))}
    out.sort(key=lambda r: (r["spec"], r["target_selectivity"] or 0, order[r["method"]]))
    return out


def _methods(result: BenchmarkResult) -> list[str]:
    return [b["method"] for b in result.build]


def _write_csv(path: str, rows: list[dict], columns: Optional[list[str]] = None) -> None:
    columns = columns or (list(rows[0]) if rows else [])
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_results(result: BenchmarkResult, out_dir: str, plots: bool = True) -> list[str]:
    """Write every output file to ``out_dir`` and return their paths."""
    os.makedirs(out_dir, exist_ok=True)
    paths = []

    def path(name: str) -> str:
        p = os.path.join(out_dir, name)
        paths.append(p)
        return p

    with open(path("results.json"), "w", encoding="utf-8") as f:
        json.dump(result.to_dict(), f, indent=1)
    _write_csv(path("queries.csv"), result.queries)
    _write_csv(path("summary.csv"), summarize(result))
    _write_csv(path("build.csv"), result.build,
               ["method", "build_seconds", "build_page_writes", "build_page_reads",
                "size_pages", "size_bytes"])
    if result.updates:
        _write_csv(path("updates.csv"), result.updates)
    with open(path("report.md"), "w", encoding="utf-8") as f:
        f.write(markdown(result))
    if plots:
        paths.extend(write_plots(result, out_dir))
    return paths


def load_result(out_dir: str) -> BenchmarkResult:
    with open(os.path.join(out_dir, "results.json"), encoding="utf-8") as f:
        return BenchmarkResult.from_dict(json.load(f))


# -- Markdown ---------------------------------------------------------------


def _fmt(x, digits: int = 1) -> str:
    if x is None:
        return "-"
    if isinstance(x, float):
        if x == 0:
            return "0"
        if abs(x) < 0.01:
            return f"{x:.2g}"
        return f"{x:,.{digits}f}"
    return f"{x:,}"


def _size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.2f} {unit}"
        n /= 1024
    return str(n)


def _table(header: list[str], rows: list[list]) -> str:
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines += ["| " + " | ".join(str(c) for c in row) + " |" for row in rows]
    return "\n".join(lines) + "\n"


def markdown(result: BenchmarkResult) -> str:
    c, d, env = result.config, result.dataset, result.environment
    methods = _methods(result)
    total = len(result.queries)
    correct = sum(q["correct"] for q in result.queries)
    mismatches = result.verification.get("mismatches", [])

    source = c["records"] or f"synthetic, seed {c['seed']}"
    parts = [
        f"# Benchmark: {c['name']}\n",
        f"- Dataset: {d['num_rows']:,} rows ({source}), {d['heap_pages']:,} heap pages",
        f"- Workload: {d['num_queries']} queries (query seed {c['query_seed']})",
        f"- Buffer pool: {c['pool_size']} pages, "
        f"{'cold' if c['cold_cache'] else 'warm'} cache per query, "
        f"median of {c['repeat']} runs",
        f"- Index results {'include' if c['fetch_records'] else 'exclude'} fetching the "
        f"matching records from the heap",
        f"- I/O cost = sequential reads + {c['random_io_weight']:g} x random reads",
        f"- Run at {env['timestamp']} on commit {env['git_commit'] or 'unknown'}, "
        f"Python {env['python']}\n",
        "## Correctness\n",
        f"{correct} of {total} query results match the sequential scan "
        f"(and the workload's expected counts).",
    ]
    if result.verification:
        parts.append(
            f"After {d['num_updates']} inserts, {result.verification['checked']} results were "
            f"checked again: {len(mismatches)} mismatches."
        )
    wrong = [q for q in result.queries if not q["correct"]]
    if wrong:
        parts.append("\n" + _table(
            ["query", "spec", "method", "got", "scan", "expected"],
            [[q["query_id"], q["spec"], q["method"], q["result_count"],
              _expected(result, q), q["expected_count"]] for q in wrong[:20]],
        ))
    parts.append("")

    parts.append("## Build and storage\n")
    parts.append(_table(
        ["method", "build time (s)", "pages written", "size (pages)", "size"],
        [[b["method"], _fmt(b["build_seconds"], 2), _fmt(b["build_page_writes"]),
          _fmt(b["size_pages"]), _size(b["size_bytes"])] for b in result.build],
    ))
    if result.updates:
        parts.append(f"## Inserts ({d['num_updates']} rows)\n")
        parts.append("The seqscan row is the heap insert alone; every index pays it too.\n")
        parts.append(_table(
            ["method", "ms / insert", "page reads / insert", "page writes / insert"],
            [[u["method"], _fmt(u["ms_per_insert"], 3), _fmt(u["page_reads_per_insert"], 2),
              _fmt(u["page_writes_per_insert"], 2)] for u in result.updates],
        ))

    summary = summarize(result)
    parts.append("## Queries\n")
    parts.append("Mean physical page reads per query (median latency in ms in parentheses). "
                 "A dash means the method does not support the query.\n")
    by_spec: dict[str, list[dict]] = defaultdict(list)
    for row in summary:
        by_spec[row["spec"]].append(row)
    for spec, rows in by_spec.items():
        cells: dict[tuple, dict] = {(r["target_selectivity"], r["method"]): r for r in rows}
        targets = sorted({r["target_selectivity"] for r in rows}, key=lambda t: t or 0)
        present = [m for m in methods if any(r["method"] == m for r in rows)]
        table_rows = []
        for t in targets:
            sel = statistics.fmean(r["selectivity"] for r in rows if r["target_selectivity"] == t)
            line = [_fmt(t, 3) if t is not None else "-", f"{sel:.4f}"]
            for m in present:
                r = cells.get((t, m))
                line.append("-" if r is None else
                            f"{_fmt(r['page_reads'])} ({_fmt(r['latency_ms'], 2)})")
            table_rows.append(line)
        parts.append(f"### `{spec}`\n")
        parts.append(_table(["target sel.", "actual sel."] + present, table_rows))
    return "\n".join(parts)


def _expected(result: BenchmarkResult, q: dict) -> int:
    for row in result.queries:
        if row["query_id"] == q["query_id"] and row["method"] == "seqscan":
            return row["result_count"]
    return -1


# -- figures ----------------------------------------------------------------


def write_plots(result: BenchmarkResult, out_dir: str) -> list[str]:
    """Draw the figures with matplotlib; returns no paths if it is missing."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return []

    summary = summarize(result)
    methods = _methods(result)
    colors = {m: f"C{i}" for i, m in enumerate(methods)}
    specs = sorted({r["spec"] for r in summary})
    paths = []

    for metric, label, name in [
        ("page_reads", "page reads per query", "page_reads.png"),
        ("latency_ms", "median latency (ms)", "latency.png"),
    ]:
        cols = min(3, len(specs))
        rows = math.ceil(len(specs) / cols)
        fig, axes = plt.subplots(rows, cols, figsize=(4.2 * cols, 3.4 * rows), squeeze=False)
        for ax, spec in zip(axes.flat, specs):
            for m in methods:
                pts = sorted((r["selectivity"], r[metric]) for r in summary
                             if r["spec"] == spec and r["method"] == m)
                if pts:
                    xs, ys = zip(*pts)
                    ax.plot(xs, [max(y, 1e-3) for y in ys], marker="o", label=m, color=colors[m])
            ax.set_xscale("log")
            ax.set_yscale("log")
            ax.set_title(spec)
            ax.set_xlabel("selectivity")
            ax.set_ylabel(label)
            ax.grid(True, which="major", alpha=0.3)
        for ax in list(axes.flat)[len(specs):]:
            ax.axis("off")
        handles, labels = axes.flat[0].get_legend_handles_labels()
        for ax in axes.flat:
            for h, l in zip(*ax.get_legend_handles_labels()):
                if l not in labels:
                    handles.append(h)
                    labels.append(l)
        fig.legend(handles, labels, loc="lower center", ncol=len(labels), frameon=False)
        fig.tight_layout(rect=(0, 0.06, 1, 1))
        p = os.path.join(out_dir, name)
        fig.savefig(p, dpi=150)
        plt.close(fig)
        paths.append(p)

    fig, ax = plt.subplots(figsize=(6, 3.4))
    ax.bar([b["method"] for b in result.build], [b["size_pages"] for b in result.build],
           color=[colors[b["method"]] for b in result.build])
    ax.set_ylabel("pages (4 KB)")
    ax.set_title("storage size")
    ax.tick_params(axis="x", rotation=20)
    fig.tight_layout()
    p = os.path.join(out_dir, "storage.png")
    fig.savefig(p, dpi=150)
    plt.close(fig)
    paths.append(p)
    return paths
