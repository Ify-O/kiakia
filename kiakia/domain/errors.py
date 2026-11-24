"""Kiakia's own error types.

Adapters translate library errors (requests, psycopg2) into these, so inner
layers never depend on those libraries (docs/ENGINEERING.md §4).
"""
from __future__ import annotations

from typing import Optional


class KiakiaError(Exception):
    """Base class for all Kiakia errors."""


class SourceUnavailable(KiakiaError):
    """A message source failed temporarily and kept failing after retries."""


class RateLimited(SourceUnavailable):
    """The source told us to slow down."""

    def __init__(self, message: str, retry_after: Optional[float] = None):
        super().__init__(message)
        self.retry_after = retry_after


class SourceRejectedRequest(KiakiaError):
    """A permanent error (bad token, bad request). Retrying won't help."""


class StorageUnavailable(KiakiaError):
    """The database could not be reached or refused the operation."""
