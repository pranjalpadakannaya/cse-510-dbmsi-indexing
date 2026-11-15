"""Command-line entry point: ``python -m dbmsi.bench <command> ...``.

Commands::

    run benchmarks/default.json [-o results/default] [--set num_files=50000 ...]
    report results/default          redraw the summary and figures from results.json
    sweep benchmarks/scale.json --param num_files --values 10000 100000 [--jobs 3]
                                    run the config once per value; see dbmsi.bench.sweep
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Optional, Sequence

from .harness import BenchmarkConfig, run_benchmark
from .report import load_result, markdown, write_results
from .sweep import run_sweep


def _log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def _parse_override(item: str) -> tuple[str, object]:
    key, sep, raw = item.partition("=")
    if not sep:
        raise ValueError(f"--set expects key=value, got {item!r}")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        value = raw  # bare strings need no quotes
    return key, value


def _load(args: argparse.Namespace) -> dict:
    data = {}
    if args.config:
        with open(args.config, encoding="utf-8") as f:
            data = json.load(f)
    data.update(_parse_override(item) for item in args.set)
    return data


def _run(args: argparse.Namespace) -> int:
    config = BenchmarkConfig.from_dict(_load(args))
    out_dir = args.output or os.path.join("results", config.name)

    result = run_benchmark(config, log=_log if not args.quiet else lambda _: None)
    paths = write_results(result, out_dir, plots=not args.no_plots)
    print(markdown(result))
    print(f"wrote {len(paths)} files -> {out_dir}", file=sys.stderr)
    return 0 if result.all_correct else 2


def _report(args: argparse.Namespace) -> int:
    result = load_result(args.results)
    write_results(result, args.results, plots=not args.no_plots)
    print(markdown(result))
    return 0 if result.all_correct else 2


def _sweep(args: argparse.Namespace) -> int:
    data = _load(args)
    values = [_parse_override(f"v={v}")[1] for v in args.values]
    out_dir = args.output or os.path.join("results", f"{data.get('name', 'benchmark')}-sweep")
    runs = run_sweep(data, args.param, values, out_dir, jobs=args.jobs,
                     plots=not args.no_plots, log=_log if not args.quiet else lambda _: None)
    with open(os.path.join(out_dir, "sweep.md"), encoding="utf-8") as f:
        print(f.read())
    print(f"wrote {len(runs)} runs -> {out_dir}", file=sys.stderr)
    return 0 if all(run["all_correct"] for _, run in runs) else 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m dbmsi.bench",
        description="Run query workloads against every access method and compare their cost.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("run", help="run a benchmark config")
    p.add_argument("config", nargs="?", help="config .json (defaults apply without one)")
    p.add_argument("-o", "--output", help="results directory (default results/<name>)")
    p.add_argument("--set", action="append", default=[], metavar="KEY=VALUE",
                   help="override a config value; VALUE is parsed as JSON when it can be")
    p.add_argument("--no-plots", action="store_true", help="skip the matplotlib figures")
    p.add_argument("-q", "--quiet", action="store_true", help="no progress on stderr")
    p.set_defaults(func=_run)

    p = sub.add_parser("report", help="rewrite the summary, report and figures of a run")
    p.add_argument("results", help="results directory containing results.json")
    p.add_argument("--no-plots", action="store_true")
    p.set_defaults(func=_report)

    p = sub.add_parser("sweep", help="run a config at several values of one parameter")
    p.add_argument("config", nargs="?", help="config .json (defaults apply without one)")
    p.add_argument("--param", required=True,
                   help="config key to vary; dots reach into dicts (generator.num_users)")
    p.add_argument("--values", nargs="+", required=True, metavar="VALUE",
                   help="values to run, each parsed as JSON when it can be")
    p.add_argument("-j", "--jobs", type=int, default=1, help="runs in parallel (processes)")
    p.add_argument("-o", "--output", help="sweep directory (default results/<name>-sweep)")
    p.add_argument("--set", action="append", default=[], metavar="KEY=VALUE",
                   help="override a config value before sweeping")
    p.add_argument("--no-plots", action="store_true")
    p.add_argument("-q", "--quiet", action="store_true")
    p.set_defaults(func=_sweep)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Exit code 0 when every result is correct, 2 when any mismatch, 1 on errors."""
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (OSError, ValueError, KeyError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
