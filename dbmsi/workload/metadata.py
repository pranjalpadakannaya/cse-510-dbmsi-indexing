"""File metadata records shared by the scanner, the generator and every index.

A :class:`FileMetadata` is one row of the benchmark table. Its stored fields
are the raw attributes a file system reports; derived attributes (name,
extension, category, directory depth, name tokens) are computed from them so
real and synthetic data are interpreted the same way.

Records serialize to compact bytes for storage in a
:class:`~dbmsi.storage.HeapFile`, and to plain dicts for JSON Lines files.
"""

from __future__ import annotations

import posixpath
import re
import struct
from dataclasses import asdict, dataclass

_TOKEN_RE = re.compile(r"[a-z0-9]+")

# file_id | size | mtime | ctime | atime | uid | mode | path length
_FIXED = struct.Struct("<qqdddIIH")
MAX_PATH_BYTES = 0xFFFF

CATEGORIES: dict[str, tuple[str, ...]] = {
    "image": ("jpg", "jpeg", "png", "gif", "bmp", "svg", "webp", "heic", "tif", "tiff", "ico"),
    "video": ("mp4", "mov", "avi", "mkv", "webm", "m4v"),
    "audio": ("mp3", "wav", "flac", "aac", "ogg", "m4a"),
    "document": ("pdf", "doc", "docx", "txt", "md", "rtf", "odt", "tex", "pages", "key", "ppt", "pptx"),
    "spreadsheet": ("xls", "xlsx", "csv", "tsv", "numbers", "ods"),
    "code": (
        "py", "c", "h", "cpp", "hpp", "cc", "java", "js", "ts", "tsx", "jsx", "go", "rs",
        "rb", "sh", "swift", "kt", "scala", "html", "css", "sql", "ipynb", "r", "m",
    ),
    "data": ("json", "xml", "yaml", "yml", "toml", "ini", "cfg", "db", "sqlite", "parquet", "log"),
    "archive": ("zip", "tar", "gz", "tgz", "bz2", "xz", "7z", "rar", "dmg", "iso"),
    "binary": ("exe", "dll", "so", "dylib", "o", "a", "class", "jar", "pyc", "bin", "wasm"),
}
_EXT_TO_CATEGORY = {ext: cat for cat, exts in CATEGORIES.items() for ext in exts}
OTHER_CATEGORY = "other"


def tokenize(text: str) -> list[str]:
    """Split a file name into lowercase alphanumeric terms.

    Same rule as the inverted index: ``"Final_Report-v2.PDF"`` gives
    ``["final", "report", "v2", "pdf"]``.
    """
    return _TOKEN_RE.findall(text.lower())


def split_extension(name: str) -> tuple[str, str]:
    """Return ``(stem, extension)``; the extension is lowercase without a dot.

    Dotfiles such as ``.bashrc`` have no extension.
    """
    dot = name.rfind(".")
    if dot <= 0 or dot == len(name) - 1:
        return name, ""
    return name[:dot], name[dot + 1 :].lower()


def category_of(extension: str) -> str:
    return _EXT_TO_CATEGORY.get(extension, OTHER_CATEGORY)


@dataclass(frozen=True)
class FileMetadata:
    """Metadata for one regular file.

    ``path`` is POSIX-style and relative to the scanned (or generated) root;
    times are POSIX timestamps in seconds; ``mode`` holds the permission
    bits.
    """

    file_id: int
    path: str
    size: int
    mtime: float
    ctime: float
    atime: float
    uid: int
    mode: int

    # -- derived attributes -------------------------------------------------

    @property
    def name(self) -> str:
        return posixpath.basename(self.path)

    @property
    def directory(self) -> str:
        return posixpath.dirname(self.path)

    @property
    def extension(self) -> str:
        return split_extension(self.name)[1]

    @property
    def category(self) -> str:
        return category_of(self.extension)

    @property
    def depth(self) -> int:
        """Number of directories between the root and the file."""
        return self.path.count("/")

    @property
    def tokens(self) -> list[str]:
        """Terms of the file name, the unit indexed by the inverted index."""
        return tokenize(self.name)

    # -- serialization ------------------------------------------------------

    def pack(self) -> bytes:
        path = self.path.encode("utf-8")
        if len(path) > MAX_PATH_BYTES:
            raise ValueError(f"path is {len(path)} B, max is {MAX_PATH_BYTES} B")
        return _FIXED.pack(
            self.file_id, self.size, self.mtime, self.ctime, self.atime,
            self.uid, self.mode, len(path),
        ) + path

    @classmethod
    def unpack(cls, buf: bytes | bytearray | memoryview) -> "FileMetadata":
        file_id, size, mtime, ctime, atime, uid, mode, n = _FIXED.unpack_from(buf, 0)
        start = _FIXED.size
        path = bytes(buf[start : start + n]).decode("utf-8")
        return cls(file_id, path, size, mtime, ctime, atime, uid, mode)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "FileMetadata":
        return cls(
            int(d["file_id"]), str(d["path"]), int(d["size"]), float(d["mtime"]),
            float(d["ctime"]), float(d["atime"]), int(d["uid"]), int(d["mode"]),
        )
