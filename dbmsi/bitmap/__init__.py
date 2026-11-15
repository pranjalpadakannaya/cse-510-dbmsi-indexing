"""Bitmap indexes for categorical attributes.

:class:`Bitmap` is the uncompressed baseline; :class:`WAHBitmap`
(Word-Aligned Hybrid) and :class:`RoaringBitmap` are the compressed
encodings. All three support AND, OR, XOR and NOT and give the same results.
:class:`BitmapIndex` stores one bitmap per attribute value in 4 KB pages
behind the buffer pool and answers equality and Boolean queries with RIDs.
"""

from .index import ENCODINGS, RIDS_PER_PAGE, BitmapIndex, BitmapIndexStats
from .plain import Bitmap
from .roaring import RoaringBitmap
from .wah import WAHBitmap

__all__ = [
    "Bitmap",
    "BitmapIndex",
    "BitmapIndexStats",
    "ENCODINGS",
    "RIDS_PER_PAGE",
    "RoaringBitmap",
    "WAHBitmap",
]
