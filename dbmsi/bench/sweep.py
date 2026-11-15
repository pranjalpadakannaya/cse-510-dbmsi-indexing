"""Run one benchmark config at several values of one parameter.

Used for the scaling runs (``num_files``) and the bitmap cardinality runs
(``generator.num_users``, the number of distinct uids). Each value gets its
own results directory, ``<out>/<param>=<value>``, written exactly as by
``run``; the sweep directory then holds:

* ``sweep.csv`` - the per-run ``summary.csv`` rows with the value added;
* ``sweep_build.csv`` - build time and size per method and value;
* ``sweep_estimates.csv`` - measured versus estimated page reads per value;
* ``sweep.md`` - the same as tables;
* ``sweep_*.png`` - size, page reads and latency against the value.

Runs are independent, so ``jobs`` of them can go in parallel processes.
"""

from __future__ import annotations

import copy
import json
import os
import statistics
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from typing import Any, Callable, Sequence

from .harness import BenchmarkConfig, run_benchmark
from .report import _fmt, _size, _table, _write_csv, estimate_check, summarize, write_results


def set_param(data: dict, param: str, value: Any) -> dict:
    """A copy of ``data`` with ``param`` set; dots reach into nested dicts
    (``generator.num_users``)."""
    data = copy.deepcopy(data)
    *path, last = param.split(".")
    node = data
    for key in path:
        node = node.setdefault(key, {})
    node[last] = value
    return data


def _run_one(data: dict, out_dir: str, plots: bool) -> dict:
    result = run_benchmark(BenchmarkConfig.from_dict(data))
    write_results(result, out_dir, plots=plots)
    return {
        "summary": summarize(result),
        "build": result.build,
        "estimates": estimate_check(result),
        "dataset": result.dataset,
        "all_correct": result.all_correct,
    }


def run_sweep(
    data: dict, param: str, values: Sequence[Any], out_dir: str, jobs: int = 1,
    plots: bool = True, log: Callable[[str], None] = lambda _: None,
) -> list[tuple[Any, dict]]:
    """Run the config once per value; returns ``(value, run summary)`` pairs."""
    name = data.get("name", "benchmark")
    runs = []
    for v in values:
        d = set_param(data, param, v)
        d["name"] = f"{name}-{param}={v}"
        runs.append((v, d, os.path.join(out_dir, f"{param}={v}")))

    results: dict[int, dict] = {}
    if jobs > 1:
        with ProcessPoolExecutor(max_workers=jobs) as pool:
            futures = {pool.submit(_run_one, d, o, plots): i for i, (_, d, o) in enumerate(runs)}
            for f, i in futures.items():
                results[i] = f.result()
                log(f"{param}={runs[i][0]}: done")
    else:
        for i, (v, d, o) in enumerate(runs):
            log(f"{param}={v}: running")
            results[i] = _run_one(d, o, plots)
    out = [(v, results[i]) for i, (v, _, _) in enumerate(runs)]
    write_sweep(param, out, out_dir, plots)
    return out


def write_sweep(param: str, runs: list[tuple[Any, dict]], out_dir: str, plots: bool = True) -> None:
    os.makedirs(out_dir, exist_ok=True)
    tag = lambda rows, v: [{param: v, **r} for r in rows]
    summary = [r for v, run in runs for r in tag(run["summary"], v)]
    build = [r for v, run in runs for r in tag(run["build"], v)]
    estimates = [r for v, run in runs for r in tag(run["estimates"], v)]
    _write_csv(os.path.join(out_dir, "sweep.csv"), summary)
    _write_csv(os.path.join(out_dir, "sweep_build.csv"), build,
               [param, "method", "build_seconds", "build_page_writes", "size_pages", "size_bytes"])
    _write_csv(os.path.join(out_dir, "sweep_estimates.csv"), estimates)
    with open(os.path.join(out_dir, "sweep.json"), "w", encoding="utf-8") as f:
        json.dump({"param": param, "runs": [{"value": v, **run} for v, run in runs]}, f, indent=1)
    with open(os.path.join(out_dir, "sweep.md"), "w", encoding="utf-8") as f:
        f.write(sweep_markdown(param, runs))
    if plots:
        write_sweep_plots(param, runs, out_dir)


def sweep_markdown(param: str, runs: list[tuple[Any, dict]]) -> str:
    values = [v for v, _ in runs]
    methods = list(dict.fromkeys(b["method"] for _, run in runs for b in run["build"]))
    head = [param] + [str(v) for v in values]
    parts = [f"# Sweep over `{param}`\n"]
    parts.append("Every result matches the sequential scan in every run.\n"
                 if all(run["all_correct"] for _, run in runs)
                 else "**Some results do not match the sequential scan; see each run's report.**\n")

    parts.append("## Rows and heap pages\n")
    parts.append(_table(head, [
        ["rows"] + [_fmt(run["dataset"]["num_rows"]) for _, run in runs],
        ["heap pages"] + [_fmt(run["dataset"]["heap_pages"]) for _, run in runs],
    ]))
    parts.append("## Index size\n")
    size = {(v, b["method"]): b for v, run in runs for b in run["build"]}
    parts.append(_table(["method"] + head[1:], [
        [m] + [_size(size[v, m]["size_bytes"]) if (v, m) in size else "-" for v in values]
        for m in methods
    ]))
    parts.append("## Build time (s)\n")
    parts.append(_table(["method"] + head[1:], [
        [m] + [_fmt(size[v, m]["build_seconds"], 2) if (v, m) in size else "-" for v in values]
        for m in methods
    ]))

    parts.append("## Queries\n")
    parts.append("Mean page reads per query, with the median latency in ms in parentheses.\n")
    cells = {(v, r["spec"], r["target_selectivity"], r["method"]): r
             for v, run in runs for r in run["summary"]}
    keys = sorted({(r["spec"], r["target_selectivity"], r["method"])
                   for _, run in runs for r in run["summary"]},
                  key=lambda k: (k[0], k[1] or 0, methods.index(k[2])))
    by_spec = defaultdict(list)
    for spec, t, m in keys:
        by_spec[spec].append((t, m))
    for spec, rows in by_spec.items():
        parts.append(f"### `{spec}`\n")
        parts.append(_table(["target sel.", "method"] + head[1:], [
            [_fmt(t, 4) if t is not None else "-", m] + [
                "-" if (v, spec, t, m) not in cells else
                f"{_fmt(cells[v, spec, t, m]['page_reads'])} "
                f"({_fmt(cells[v, spec, t, m]['latency_ms'], 2)})"
                for v in values
            ] for t, m in rows
        ]))

    parts.append("## Measured versus estimated page reads\n")
    parts.append("Total measured over total estimated reads, minus one, per method.\n")
    err = defaultdict(lambda: [0.0, 0.0])
    for v, run in runs:
        for e in run["estimates"]:
            err[v, e["method"]][0] += e["measured"]
            err[v, e["method"]][1] += e["estimated"]
    parts.append(_table(["method"] + head[1:], [
        [m] + [f"{err[v, m][0] / err[v, m][1] - 1:+.1%}" if err[v, m][1] else "-" for v in values]
        for m in methods
    ]))
    return "\n".join(parts)


def write_sweep_plots(param: str, runs: list[tuple[Any, dict]], out_dir: str) -> list[str]:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return []
    numeric = all(isinstance(v, (int, float)) for v, _ in runs)
    methods = list(dict.fromkeys(b["method"] for _, run in runs for b in run["build"]))
    colors = {m: f"C{i}" for i, m in enumerate(methods)}
    paths = []

    def finish(fig, ax, name):
        if numeric:
            ax.set_xscale("log")
        ax.set_xlabel(param)
        ax.grid(True, which="major", alpha=0.3)
        ax.legend(fontsize=8)
        fig.tight_layout()
        p = os.path.join(out_dir, name)
        fig.savefig(p, dpi=150)
        plt.close(fig)
        paths.append(p)

    fig, ax = plt.subplots(figsize=(6, 3.8))
    for m in methods:
        pts = [(v, b["size_bytes"]) for v, run in runs for b in run["build"] if b["method"] == m]
        if pts:
            ax.plot(*zip(*pts), marker="o", label=m, color=colors[m])
    ax.set_yscale("log")
    ax.set_ylabel("size (bytes)")
    ax.set_title("index size")
    finish(fig, ax, "sweep_storage.png")

    specs = sorted({r["spec"] for _, run in runs for r in run["summary"]})
    for metric, label, name in [("page_reads", "page reads per query", "sweep_page_reads.png"),
                                ("latency_ms", "median latency (ms)", "sweep_latency.png")]:
        cols = min(3, len(specs))
        rows = -(-len(specs) // cols)
        fig, axes = plt.subplots(rows, cols, figsize=(4.2 * cols, 3.4 * rows), squeeze=False)
        for ax, spec in zip(axes.flat, specs):
            for m in methods:
                pts = []
                for v, run in runs:
                    ys = [r[metric] for r in run["summary"] if r["spec"] == spec and r["method"] == m]
                    if ys:
                        pts.append((v, statistics.fmean(ys)))
                if pts:
                    ax.plot(*zip(*pts), marker="o", label=m, color=colors[m])
            if numeric:
                ax.set_xscale("log")
            ax.set_yscale("log")
            ax.set_title(f"{spec} (mean over selectivities)", fontsize=9)
            ax.set_xlabel(param)
            ax.set_ylabel(label)
            ax.grid(True, which="major", alpha=0.3)
        for ax in list(axes.flat)[len(specs):]:
            ax.axis("off")
        labels = {}
        for ax in axes.flat:
            for h, l in zip(*ax.get_legend_handles_labels()):
                labels.setdefault(l, h)
        fig.legend(labels.values(), labels.keys(), loc="lower center", ncol=len(labels),
                   frameon=False)
        fig.tight_layout(rect=(0, 0.06, 1, 1))
        p = os.path.join(out_dir, name)
        fig.savefig(p, dpi=150)
        plt.close(fig)
        paths.append(p)
    return paths
