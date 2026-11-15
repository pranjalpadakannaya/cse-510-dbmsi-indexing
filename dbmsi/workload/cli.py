"""Command-line entry point: ``python -m dbmsi.workload <command> ...``.

Commands::

    scan ROOT -o records.jsonl [-p profile.json]   extract real metadata
    profile records.jsonl -o profile.json          summarize distributions
    generate -n 100000 -o synth.jsonl [--profile profile.json] [--term-skew 1.2]
    queries synth.jsonl -o queries.jsonl --spec range:size --selectivity 0.001 0.01 0.1
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import replace
from typing import Optional, Sequence

from .dataset import iter_records, read_records, write_queries, write_records
from .generator import GeneratorConfig, SyntheticGenerator
from .profile import MetadataProfile
from .queries import QueryGenerator
from .scanner import MetadataScanner


def _scan(args: argparse.Namespace) -> None:
    scanner = MetadataScanner(
        args.root,
        follow_symlinks=args.follow_symlinks,
        include_hidden=not args.no_hidden,
        max_files=args.max_files,
    )
    n = write_records(args.output, scanner.scan())
    s = scanner.stats
    print(
        f"scanned {n} files in {s.directories} directories "
        f"({s.skipped} skipped, {s.errors} unreadable) -> {args.output}"
    )
    if args.profile:
        profile = MetadataProfile.from_records(iter_records(args.output))
        profile.save(args.profile)
        print(f"profile -> {args.profile}")


def _profile(args: argparse.Namespace) -> None:
    profile = MetadataProfile.from_records(iter_records(args.records), top_terms=args.top_terms)
    if args.output:
        profile.save(args.output)
    print(profile.summary())


def _generate(args: argparse.Namespace) -> None:
    if args.profile:
        config = GeneratorConfig.from_profile(
            MetadataProfile.load(args.profile), num_files=args.num_files, seed=args.seed
        )
    else:
        config = GeneratorConfig(num_files=args.num_files, seed=args.seed)
    overrides = {
        name: value
        for name, value in [
            ("term_skew", args.term_skew),
            ("vocab_size", args.vocab_size),
            ("extension_skew", args.extension_skew),
            ("dir_skew", args.dir_skew),
        ]
        if value is not None
    }
    config = replace(config, **overrides)
    n = write_records(args.output, SyntheticGenerator(config).records())
    print(
        f"generated {n} files (seed {config.seed}, term skew {config.term_skew:.2f}, "
        f"vocabulary {config.vocab_size}) -> {args.output}"
    )


def _queries(args: argparse.Namespace) -> None:
    records = read_records(args.records)
    gen = QueryGenerator(records, seed=args.seed)
    queries = []
    for spec in args.spec:
        levels = args.selectivity or [None]
        for s in levels:
            queries.extend(gen.make(spec, s) for _ in range(args.per_level))
    write_queries(args.output, queries)
    print(f"wrote {len(queries)} queries over {len(records)} files -> {args.output}")
    for q in queries[: args.show]:
        print(f"  {q.kind:<6} sel {q.selectivity(len(records)):.5f}  {q.to_dict()}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m dbmsi.workload",
        description="File metadata extraction and synthetic workload generation.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("scan", help="extract metadata from a directory tree")
    p.add_argument("root")
    p.add_argument("-o", "--output", required=True, help="records .jsonl to write")
    p.add_argument("-p", "--profile", help="also write a profile .json")
    p.add_argument("--max-files", type=int)
    p.add_argument("--no-hidden", action="store_true", help="skip dotfiles and dot-directories")
    p.add_argument("--follow-symlinks", action="store_true")
    p.set_defaults(func=_scan)

    p = sub.add_parser("profile", help="summarize the distributions of a records file")
    p.add_argument("records")
    p.add_argument("-o", "--output", help="profile .json to write")
    p.add_argument("--top-terms", type=int, default=5000)
    p.set_defaults(func=_profile)

    p = sub.add_parser("generate", help="generate a synthetic records file")
    p.add_argument("-n", "--num-files", type=int, required=True)
    p.add_argument("-o", "--output", required=True)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--profile", help="imitate the distributions in this profile .json")
    p.add_argument("--term-skew", type=float, help="Zipf exponent of name terms")
    p.add_argument("--vocab-size", type=int)
    p.add_argument("--extension-skew", type=float, help="Zipf exponent of extensions")
    p.add_argument("--dir-skew", type=float, help="Zipf exponent of files per directory")
    p.set_defaults(func=_generate)

    p = sub.add_parser("queries", help="generate a query workload over a records file")
    p.add_argument("records")
    p.add_argument("-o", "--output", required=True)
    p.add_argument(
        "--spec", nargs="+", required=True,
        help="query specs: range:<attr>, point:<attr>, term, and[:k], or[:k]",
    )
    p.add_argument("--selectivity", type=float, nargs="*", help="target selectivities")
    p.add_argument("--per-level", type=int, default=10, help="queries per spec and level")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--show", type=int, default=0, help="print the first N queries")
    p.set_defaults(func=_queries)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        args.func(args)
    except (OSError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0
