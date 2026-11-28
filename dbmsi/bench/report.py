"""Benchmark output: raw results, summary tables and figures.

A results directory holds:

* ``results.json`` - everything, including the resolved config and the
  environment, so a run can be re-reported or reproduced;
* ``queries.csv`` - one row per (query, method);
* ``summary.csv`` - means per (spec, target selectivity, method);
* ``estimates.csv`` - measured versus analytically estimated page reads per
  (method, spec);
* ``build.csv`` and ``updates.csv`` - build time, size and insert cost;
* ``throughput.csv`` - concurrent B+ tree throughput, when it was measured;
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

from .harness import BenchmarkResult, io_cost_field

# Averaged over the queries of a group; latencies take the median instead.
MEAN_FIELDS = ("page_reads", "sequential_reads", "random_reads", "index_page_reads",
               "fetch_page_reads", "estimated_page_reads")
MEDIAN_FIELDS = (
    "latency_ms",
    "latency_p95_ms",
    "cold_latency_ms",
    "decode_ms",
    "ranking_ms",
)
ESTIMATE_TOLERANCE = 0.05  # success criterion: measured within 5% of the estimate


def _cost_fields(result: BenchmarkResult) -> list[str]:
    return [io_cost_field(k) for k in result.config["random_io_weights"]]


def _mean(rows: list[dict], f: str) -> Optional[float]:
    xs = [r[f] for r in rows if r.get(f) is not None]
    return statistics.fmean(xs) if xs else None


def summarize(result: BenchmarkResult) -> list[dict]:
    """Means of the I/O counters and median latencies per group."""
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
            **{f: _mean(rows, f) for f in MEAN_FIELDS + tuple(_cost_fields(result))},
            **{f: statistics.median(r[f] for r in rows) for f in MEDIAN_FIELDS},
        })
    order = {m: i for i, m in enumerate(_methods(result))}
    out.sort(key=lambda r: (r["spec"], r["target_selectivity"] or 0, order[r["method"]]))
    return out


def estimate_check(result: BenchmarkResult) -> list[dict]:
    """Measured against estimated cold-cache page reads per (method, spec).

    ``error`` compares the totals (measured / estimated - 1);
    ``within_tolerance`` is the share of single queries within 5%.
    """
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for row in result.queries:
        if row.get("estimated_page_reads") is not None:
            groups[(row["method"], row["spec"])].append(row)
    order = {m: i for i, m in enumerate(_methods(result))}
    out = []
    for (method, spec), rows in sorted(groups.items(), key=lambda kv: (order[kv[0][0]], kv[0][1])):
        measured = sum(r["page_reads"] for r in rows)
        estimated = sum(r["estimated_page_reads"] for r in rows)
        close = sum(
            abs(r["page_reads"] - r["estimated_page_reads"])
            <= ESTIMATE_TOLERANCE * max(r["estimated_page_reads"], 1e-9)
            for r in rows
        )
        out.append({
            "method": method, "spec": spec, "queries": len(rows),
            "measured": measured, "estimated": estimated,
            "error": measured / estimated - 1 if estimated else None,
            "within_tolerance": close / len(rows),
        })
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
    _write_csv(path("estimates.csv"), estimate_check(result))
    _write_csv(path("build.csv"), result.build,
               ["method", "build_seconds", "build_page_writes", "build_page_reads",
                "size_pages", "size_bytes"])
    if result.updates:
        _write_csv(path("updates.csv"), result.updates)
    if result.throughput:
        _write_csv(path("throughput.csv"), result.throughput)
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


def _calibration_note(c: dict) -> str:
    cal = c.get("io_calibration_data")
    if not cal:
        return " (assumed, not measured)"
    return (f" (measured: a random read takes {cal['random']['mean_ms'] * 1000:.0f} us and a "
            f"sequential read {cal['sequential']['mean_ms'] * 1000:.0f} us on "
            f"{cal['machine']['platform']})")


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
        f"- Buffer pool: {c['pool_size']} pages. Page I/O is counted on one run per query "
        f"from a cold cache; latency is the median and 95th percentile of up to "
        f"{c['repeat']} warm runs after {c['warmup']} warm-up run(s) (at least "
        f"{c['min_repeat']}, fewer than {c['repeat']} once {c['max_repeat_seconds']:g} s "
        f"is spent)",
        f"- Index results {'include' if c['fetch_records'] else 'exclude'} fetching the "
        f"matching records from the heap" + (" (in RID order)" if c["fetch_records"] else ""),
        f"- I/O cost = sequential reads + k x random reads, for k in "
        f"{', '.join(f'{k:g}' for k in c['random_io_weights'])}"
        + _calibration_note(c),
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

    checks = estimate_check(result)
    if checks:
        measured = sum(e["measured"] for e in checks)
        estimated = sum(e["estimated"] for e in checks)
        parts.append("## Measured versus estimated page I/O\n")
        parts.append(
            "Each method's analytical model of its cold-cache page reads (see "
            "`dbmsi/bench/methods.py`) against the measured count. *error* compares the "
            "totals over all queries of a spec; *within 5%* is the share of single queries "
            f"within 5% of their estimate. Over all queries: {measured:,} measured, "
            f"{estimated:,.0f} estimated ({measured / estimated - 1:+.1%}).\n"
        )
        parts.append(_table(
            ["method", "spec", "queries", "measured", "estimated", "error", "within 5%"],
            [[e["method"], e["spec"], e["queries"], _fmt(e["measured"]), _fmt(e["estimated"]),
              "-" if e["error"] is None else f"{e['error']:+.1%}",
              f"{e['within_tolerance']:.0%}"] for e in checks],
        ))
    if result.throughput:
        t0 = result.throughput[0]
        parts.append("## Concurrent searches and inserts (B+ tree)\n")
        parts.append(
            f"{t0['ops']:,} operations ({t0['inserts']:,} inserts) on a `{t0['attribute']}` "
            f"tree, split across the threads, from a cold pool of "
            f"{t0.get('pool_size', '-')} frames. `global` takes one tree-wide latch per "
            "operation; `crabbing` latches one root-to-leaf path. *read latency* is the "
            "modeled wait per physical page read; *speedup* is against the fewest threads "
            "of the same scheme and latency.\n"
        )
        parts.append(_table(
            ["latching", "read latency (ms)", "threads", "ops / s", "speedup", "page reads",
             "lost inserts"],
            [[t.get("latching", "global"), _fmt(t.get("read_latency_ms", 0.0), 2),
              t["threads"], _fmt(t["ops_per_second"], 0),
              "-" if t.get("speedup") is None else f"{t['speedup']:.2f}x",
              _fmt(t["page_reads"]), t["lost_inserts"]] for t in result.throughput],
        ))
        compare = throughput_comparison(result.throughput)
        if compare:
            parts.append("Crabbing against the global latch at the same latency and "
                         "thread count:\n")
            parts.append(_table(
                ["read latency (ms)", "threads", "global ops / s", "crabbing ops / s",
                 "crabbing / global"],
                [[_fmt(c["read_latency_ms"], 2), c["threads"], _fmt(c["global"], 0),
                  _fmt(c["crabbing"], 0), f"{c['ratio']:.2f}x"] for c in compare],
            ))

    summary = summarize(result)
    decode = [r for r in summary if r["decode_ms"]]
    if decode:
        parts.append("## Posting list decoding\n")
        parts.append("Median time spent decoding posting lists per query (warm runs), "
                     "against the whole query.\n")
        parts.append(_table(
            ["spec", "target sel.", "method", "decode (ms)", "query (ms)", "decode share"],
            [[r["spec"], _fmt(r["target_selectivity"], 4), r["method"], _fmt(r["decode_ms"], 3),
              _fmt(r["latency_ms"], 3), f"{r['decode_ms'] / r['latency_ms']:.0%}"]
             for r in decode],
        ))

    ranking = [r for r in summary if r.get("ranking_ms", 0)]
    if ranking:
        parts.append("## BM25 ranking\n")
        parts.append(
            "Median time spent ranking retrieved candidates with BM25 per query "
            "(warm runs), compared with the whole query.\n"
        )
        parts.append(_table(
            [
                "spec",
                "target sel.",
                "method",
                "ranking (ms)",
                "query (ms)",
                "ranking share",
            ],
            [
                [
                    r["spec"],
                    _fmt(r["target_selectivity"], 4),
                    r["method"],
                    _fmt(r["ranking_ms"], 3),
                    _fmt(r["latency_ms"], 3),
                    f"{r['ranking_ms'] / r['latency_ms']:.0%}",
                ]
                for r in ranking
            ],
        ))
    parts.append("## Queries\n")
    parts.append("Mean physical page reads per query, cold cache (median and 95th percentile "
                 "warm latency in ms in parentheses). A dash means the method does not support "
                 "the query. Full I/O costs for each k are in `summary.csv`.\n")
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
                            f"{_fmt(r['page_reads'])} ({_fmt(r['latency_ms'], 2)} / "
                            f"{_fmt(r['latency_p95_ms'], 2)})")
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


def throughput_comparison(throughput: list[dict]) -> list[dict]:
    """Pair global-latch and crabbing rows at equal latency and thread count."""
    by = {(t.get("latching"), t.get("read_latency_ms", 0.0), t["threads"]): t["ops_per_second"]
          for t in throughput}
    out = []
    for (scheme, latency, threads), ops in by.items():
        other = by.get(("crabbing", latency, threads))
        if scheme == "global" and other:
            out.append({"read_latency_ms": latency, "threads": threads, "global": ops,
                        "crabbing": other, "ratio": other / ops})
    return out


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

    checked = [r for r in result.queries
               if r.get("estimated_page_reads") and r["page_reads"]]
    if checked:
        fig, ax = plt.subplots(figsize=(5, 4.4))
        for m in methods:
            pts = [(r["estimated_page_reads"], r["page_reads"]) for r in checked
                   if r["method"] == m]
            if pts:
                ax.scatter(*zip(*pts), s=12, alpha=0.6, label=m, color=colors[m])
        lo = min(min(r["estimated_page_reads"], r["page_reads"]) for r in checked)
        hi = max(max(r["estimated_page_reads"], r["page_reads"]) for r in checked)
        ax.plot([lo, hi], [lo, hi], color="0.4", linewidth=1, label="measured = estimated")
        ax.set_xscale("log")
        ax.set_yscale("log")
        ax.set_xlabel("estimated page reads")
        ax.set_ylabel("measured page reads")
        ax.set_title("cold-cache page reads per query")
        ax.grid(True, which="major", alpha=0.3)
        ax.legend(fontsize=7)
        fig.tight_layout()
        p = os.path.join(out_dir, "estimates.png")
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

    if result.throughput:
        latencies = sorted({t.get("read_latency_ms", 0.0) for t in result.throughput})
        fig, axes = plt.subplots(1, len(latencies), figsize=(4.2 * len(latencies), 3.4),
                                 squeeze=False)
        for ax, latency in zip(axes.flat, latencies):
            for i, scheme in enumerate(["global", "crabbing"]):
                pts = sorted((t["threads"], t["ops_per_second"]) for t in result.throughput
                             if t.get("latching", "global") == scheme
                             and t.get("read_latency_ms", 0.0) == latency)
                if pts:
                    ax.plot(*zip(*pts), marker="o", label=scheme, color=f"C{i}")
            ax.set_xscale("log", base=2)
            ax.set_xlabel("threads")
            ax.set_ylabel("operations / s")
            ax.set_title(f"{latency:g} ms per page read")
            ax.set_ylim(bottom=0)
            ax.grid(True, alpha=0.3)
            ax.legend(fontsize=7)
        fig.tight_layout()
        p = os.path.join(out_dir, "throughput.png")
        fig.savefig(p, dpi=150)
        plt.close(fig)
        paths.append(p)
    return paths
