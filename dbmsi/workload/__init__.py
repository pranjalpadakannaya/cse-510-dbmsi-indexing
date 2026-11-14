"""File metadata extraction and synthetic workload generation.

Scan a real tree into :class:`FileMetadata` records, summarize it as a
:class:`MetadataProfile`, generate synthetic datasets of any size with
controlled skew via :class:`SyntheticGenerator`, and build query workloads
with controlled selectivity via :class:`QueryGenerator`.
"""

from .dataset import iter_records, read_queries, read_records, write_queries, write_records
from .generator import GeneratorConfig, SyntheticGenerator, generate
from .metadata import FileMetadata, tokenize
from .profile import MetadataProfile
from .queries import PointQuery, Query, QueryGenerator, RangeQuery, TermQuery, evaluate
from .scanner import MetadataScanner, ScanStats, scan_directory

__all__ = [
    "FileMetadata",
    "GeneratorConfig",
    "MetadataProfile",
    "MetadataScanner",
    "PointQuery",
    "Query",
    "QueryGenerator",
    "RangeQuery",
    "ScanStats",
    "SyntheticGenerator",
    "TermQuery",
    "evaluate",
    "generate",
    "iter_records",
    "read_queries",
    "read_records",
    "scan_directory",
    "tokenize",
    "write_queries",
    "write_records",
]
