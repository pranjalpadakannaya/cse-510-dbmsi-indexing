"""Page-aware storage kernel: 4 KB pages, LRU buffer pool, I/O accounting.

Every structure in the project (tables and all index nodes) lives in
``PAGE_SIZE`` pages accessed only through a :class:`BufferPoolManager`, so
page reads and writes are counted uniformly in :class:`IOStats`.
"""

from .buffer_pool import BufferPoolFullError, BufferPoolManager
from .constants import INVALID_PAGE_ID, PAGE_SIZE
from .disk_manager import DiskManager
from .heap_file import HeapFile
from .lru_replacer import LRUReplacer
from .page import Page
from .rid import RID
from .slotted_page import MAX_RECORD_SIZE, PageFullError, SlottedPage
from .stats import IOStats, measure

__all__ = [
    "BufferPoolFullError",
    "BufferPoolManager",
    "DiskManager",
    "HeapFile",
    "INVALID_PAGE_ID",
    "IOStats",
    "LRUReplacer",
    "MAX_RECORD_SIZE",
    "PAGE_SIZE",
    "Page",
    "PageFullError",
    "RID",
    "SlottedPage",
    "measure",
]
