"""Command-line entry point: ``python -m dbmsi.bench <command> ...``.

Commands::

    run benchmarks/default.json [-o results/default] [--set num_files=50000 ...]
    report results/default          redraw the summary and figures from results.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Optional, Sequence

from .harness import BenchmarkConfig, run_benchmark
from .report import load_result, markdown, write_results


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


def _run(args: argparse.Namespace) -> int:
    data = {}
    if args.config:
        with open(args.config, encoding="utf-8") as f:
            data = json.load(f)
    data.update(_parse_override(item) for item in args.set)
    config = BenchmarkConfig.from_dict(data)
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
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Exit code 0 when every result is correct, 2 when any mismatch, 1 on errors."""
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (OSError, ValueError, KeyError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
