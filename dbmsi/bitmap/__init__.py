"""Bitmap indexes for categorical attributes.

:class:`Bitmap` is the uncompressed baseline and :class:`WAHBitmap` the
Word-Aligned Hybrid compressed encoding; both support AND, OR, XOR and NOT.
:class:`BitmapIndex` stores one bitmap per attribute value in 4 KB pages
behind the buffer pool and answers equality and Boolean queries with RIDs.
"""

from .index import ENCODINGS, RIDS_PER_PAGE, BitmapIndex, BitmapIndexStats
from .plain import Bitmap
from .wah import WAHBitmap

__all__ = [
    "Bitmap",
    "BitmapIndex",
    "BitmapIndexStats",
    "ENCODINGS",
    "RIDS_PER_PAGE",
    "WAHBitmap",
]
