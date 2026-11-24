"""Kiakia's own error types.

Adapters translate library errors (requests, psycopg) into these, so inner
layers never depend on those libraries (docs/ENGINEERING.md §4).
"""


class KiakiaError(Exception):
    """Base class for all Kiakia errors."""


class SourceUnavailable(KiakiaError):
    """A message source failed temporarily and kept failing after retries."""


class RateLimited(SourceUnavailable):
    """The source told us to slow down."""

    def __init__(self, message: str, retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


class SourceRejectedRequest(KiakiaError):
    """A permanent error (bad token, bad request). Retrying won't help."""


class StorageUnavailable(KiakiaError):
    """The database could not be reached or refused the operation."""


class NotFound(KiakiaError):
    """The requested thing does not exist."""


class InvalidCursor(KiakiaError):
    """A pagination cursor is malformed, tampered with, or from a different list."""


class InvalidInput(KiakiaError):
    """A field of the request is invalid in a way only the domain can tell (-> 422)."""

    def __init__(self, field: str, message: str):
        super().__init__(f"{field}: {message}")
        self.field = field
        self.message = message


class IdempotencyConflict(KiakiaError):
    """The Idempotency-Key was used for a different request, or is still in progress (-> 409)."""


class ChannelNotSupported(SourceRejectedRequest):
    """Kiakia can't send messages on this conversation's channel yet (-> 502)."""


class DeliveryUnknown(KiakiaError):
    """We sent the message but couldn't confirm delivery (e.g. timed out waiting for the answer).
    It may have reached the customer, so it must NOT be retried automatically (-> 502)."""


class InvalidQuery(KiakiaError):
    """A query parameter is invalid in a way only the domain can tell (-> 400)."""

    def __init__(self, field: str, message: str):
        super().__init__(f"{field}: {message}")
        self.field = field
        self.message = message
