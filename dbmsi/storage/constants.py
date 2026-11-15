"""Storage-layer constants shared by every page-based structure."""

PAGE_SIZE = 4096
"""Size of every on-disk page, in bytes."""

INVALID_PAGE_ID = -1
"""Sentinel page id meaning "no page" (e.g. end of a page chain)."""
