"""Extract metadata from a real directory tree.

The scanner walks the tree iteratively with :func:`os.scandir`, visiting
entries in sorted order so that the same tree always yields the same record
order and ``file_id`` values. Only regular files become records. Symlinks are
not followed by default (which also prevents cycles), and entries that
vanish or cannot be read are counted and skipped rather than aborting a long
scan.
"""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from typing import Iterator, Optional

from .metadata import FileMetadata


@dataclass
class ScanStats:
    files: int = 0
    directories: int = 0
    skipped: int = 0  # symlinks, sockets, devices, and hidden entries
    errors: int = 0  # entries that vanished or could not be read


class MetadataScanner:
    def __init__(
        self,
        root: str,
        follow_symlinks: bool = False,
        include_hidden: bool = True,
        max_files: Optional[int] = None,
    ):
        """
        :param root: directory to scan; record paths are relative to it.
        :param follow_symlinks: follow symlinked files and directories. Each
            directory is visited at most once, so link cycles are safe.
        :param include_hidden: include entries whose name starts with ``.``.
        :param max_files: stop after this many records.
        """
        if not os.path.isdir(root):
            raise NotADirectoryError(root)
        self.root = os.path.abspath(root)
        self.follow_symlinks = follow_symlinks
        self.include_hidden = include_hidden
        self.max_files = max_files
        self.stats = ScanStats()

    def scan(self) -> Iterator[FileMetadata]:
        """Yield one :class:`FileMetadata` per regular file, depth first.

        Within a directory, its files come first (sorted by name), followed
        by the contents of each subdirectory in name order.
        """
        self.stats = ScanStats()
        seen_dirs: set[tuple[int, int]] = set()
        stack = [""]  # relative directory paths still to visit
        while stack:
            rel_dir = stack.pop()
            abs_dir = os.path.join(self.root, rel_dir) if rel_dir else self.root
            try:
                st = os.stat(abs_dir)
                if (st.st_dev, st.st_ino) in seen_dirs:
                    continue
                seen_dirs.add((st.st_dev, st.st_ino))
                with os.scandir(abs_dir) as it:
                    entries = sorted(it, key=lambda e: e.name)
            except OSError:
                self.stats.errors += 1
                continue
            self.stats.directories += 1

            subdirs = []
            for entry in entries:
                if not self.include_hidden and entry.name.startswith("."):
                    self.stats.skipped += 1
                    continue
                rel = f"{rel_dir}/{entry.name}" if rel_dir else entry.name
                try:
                    if entry.is_symlink() and not self.follow_symlinks:
                        self.stats.skipped += 1
                        continue
                    st = entry.stat(follow_symlinks=self.follow_symlinks)
                except OSError:
                    self.stats.errors += 1
                    continue
                if stat.S_ISDIR(st.st_mode):
                    subdirs.append(rel)
                elif stat.S_ISREG(st.st_mode):
                    yield FileMetadata(
                        file_id=self.stats.files,
                        path=rel,
                        size=st.st_size,
                        mtime=st.st_mtime,
                        ctime=st.st_ctime,
                        atime=st.st_atime,
                        uid=st.st_uid,
                        mode=stat.S_IMODE(st.st_mode),
                    )
                    self.stats.files += 1
                    if self.max_files is not None and self.stats.files >= self.max_files:
                        return
                else:
                    self.stats.skipped += 1
            # Push in reverse so subdirectories are visited in sorted order.
            stack.extend(reversed(subdirs))


def scan_directory(root: str, **kwargs) -> list[FileMetadata]:
    """Scan ``root`` and return every record (see :class:`MetadataScanner`)."""
    return list(MetadataScanner(root, **kwargs).scan())
