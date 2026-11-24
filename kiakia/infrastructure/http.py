"""Resilient HTTP: one shared place for timeouts, retries and rate limiting.

Every channel adapter (Telegram now, Meta later) uses this instead of
copying retry logic (no duplication; docs/ENGINEERING.md §5).

- Timeouts on every request (connect, read).
- Retries transient failures (network errors, timeouts, 429, 5xx) with
  exponential backoff + jitter, a limited number of times.
- Honours the server's "retry after" hint when it gives one.
- Keeps a minimum gap between requests (simple client-side rate limit).
- Fails fast on permanent errors (400, 401, 403, 404, 409).
- Translates `requests` errors into Kiakia's own errors.
- Never puts the URL in errors or logs (Telegram's URL contains the bot token).
"""

import logging
import random
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import requests
from urllib3.exceptions import NewConnectionError

from kiakia.domain.errors import DeliveryUnknown, RateLimited, SourceRejectedRequest, SourceUnavailable

log = logging.getLogger(__name__)

TRANSIENT_STATUSES = {429, 500, 502, 503, 504}

# A function that reads a "wait N seconds" hint from a response (APIs do this differently).
RetryAfterReader = Callable[[requests.Response], float | None]
# A function that reads a human-readable reason from an error response, if the API gives one.
ErrorDetailReader = Callable[[requests.Response], str | None]


def _never_reached_server(exc: requests.ConnectionError) -> bool:
    """True only when we provably never connected (refused, DNS failure, connect timeout).
    A connection that broke AFTER the request was sent is NOT this case."""
    if isinstance(exc, requests.ConnectTimeout):
        return True
    reason = getattr(exc.args[0], "reason", None) if exc.args else None
    return isinstance(reason, NewConnectionError)


def header_retry_after(resp: requests.Response) -> float | None:
    value = resp.headers.get("Retry-After", "")
    return float(value) if value.strip().isdigit() else None


@dataclass
class RetryPolicy:
    max_attempts: int = 5  # 1 try + 4 retries
    base_delay: float = 1.0  # 1s, 2s, 4s, 8s ...
    max_delay: float = 30.0
    jitter: float = 0.5  # +/- 50% randomness so retries don't all line up

    def delay(self, attempt: int, rng: Callable[[], float] = random.random) -> float:
        raw = min(self.max_delay, self.base_delay * float(2**attempt))
        return raw * (1 - self.jitter + 2 * self.jitter * rng())


class ResilientHttpClient:
    def __init__(
        self,
        *,
        timeout: tuple[float, float] = (5.0, 30.0),  # (connect, read) seconds
        retry: RetryPolicy | None = None,
        min_interval: float = 1.0,  # at most ~1 request per second
        retry_after_reader: RetryAfterReader = header_retry_after,
        error_detail_reader: ErrorDetailReader | None = None,
        session: requests.Session | None = None,
        sleep: Callable[[float], None] = time.sleep,  # injectable for tests
        clock: Callable[[], float] = time.monotonic,
    ):
        self.timeout = timeout
        self.retry = retry or RetryPolicy()
        self.min_interval = min_interval
        self.retry_after_reader = retry_after_reader
        self.error_detail_reader = error_detail_reader
        self.session = session or requests.Session()
        self.session.headers.setdefault("User-Agent", "kiakia/0.1")
        self._sleep = sleep
        self._clock = clock
        self._last_request_at: float | None = None

    def _throttle(self) -> None:
        if self._last_request_at is None:
            return
        wait = self.min_interval - (self._clock() - self._last_request_at)
        if wait > 0:
            self._sleep(wait)

    def get_json(self, url: str, *, params: dict[str, Any] | None = None, label: str = "request") -> Any:
        """GET a URL and return parsed JSON. `label` is what appears in logs and errors."""
        last_problem = "unknown error"
        for attempt in range(self.retry.max_attempts):
            self._throttle()
            self._last_request_at = self._clock()
            wait_hint: float | None = None
            try:
                resp = self.session.get(url, params=params, timeout=self.timeout)
            except requests.Timeout:
                last_problem = "timed out"
            except requests.ConnectionError:
                last_problem = "connection failed"
            else:
                if resp.status_code < 400:
                    try:
                        return resp.json()
                    except ValueError:
                        last_problem = "response was not valid JSON"
                elif resp.status_code in TRANSIENT_STATUSES:
                    last_problem = f"HTTP {resp.status_code}"
                    wait_hint = self.retry_after_reader(resp)
                else:
                    raise SourceRejectedRequest(self._rejection(label, resp))

            if wait_hint is not None and wait_hint > self.retry.max_delay:
                # Never let a server park us for minutes/hours: give up now and report
                # how long it asked us to wait, so the caller can decide (e.g. try later).
                raise RateLimited(
                    f"{label}: server asked to wait {wait_hint:.0f}s (more than {self.retry.max_delay:.0f}s)",
                    retry_after=wait_hint,
                )
            if attempt + 1 >= self.retry.max_attempts:
                break
            delay = wait_hint if wait_hint is not None else self.retry.delay(attempt)
            log.warning(
                "%s: %s (attempt %d/%d), retrying in %.1fs",
                label,
                last_problem,
                attempt + 1,
                self.retry.max_attempts,
                delay,
            )
            self._sleep(delay)

        if last_problem == "HTTP 429":
            raise RateLimited(f"{label}: still rate limited after {self.retry.max_attempts} attempts")
        raise SourceUnavailable(f"{label}: {last_problem} after {self.retry.max_attempts} attempts")

    def _rejection(self, label: str, resp: requests.Response) -> str:
        detail = None
        if self.error_detail_reader is not None:
            try:
                detail = self.error_detail_reader(resp)
            except ValueError:
                detail = None
        return (
            f"{label}: {detail}" if detail else f"{label}: HTTP {resp.status_code} (check the token/request)"
        )

    def post_json_unsafe(self, url: str, *, json_body: dict[str, Any], label: str = "request") -> Any:
        """POST for actions that must NOT happen twice (e.g. sending a message to a customer).

        Retries ONLY when the request certainly had no effect: we couldn't connect at all, or the
        server answered 429 (it didn't process it). Anything ambiguous (timeout after sending,
        broken connection, 5xx, unreadable answer) raises DeliveryUnknown and is never retried.
        """
        last_problem = "unknown error"
        for attempt in range(self.retry.max_attempts):
            self._throttle()
            self._last_request_at = self._clock()
            wait_hint: float | None = None
            try:
                resp = self.session.post(url, json=json_body, timeout=self.timeout)
            except requests.ConnectionError as exc:
                if not _never_reached_server(exc):
                    raise DeliveryUnknown(
                        f"{label}: the connection broke; it may have been delivered"
                    ) from None
                last_problem = "could not connect"
            except requests.Timeout:
                raise DeliveryUnknown(f"{label}: no answer in time; it may have been delivered") from None
            else:
                if resp.status_code < 400:
                    try:
                        return resp.json()
                    except ValueError:
                        raise DeliveryUnknown(
                            f"{label}: unreadable answer; it may have been delivered"
                        ) from None
                if resp.status_code == 429:
                    last_problem = "HTTP 429"
                    wait_hint = self.retry_after_reader(resp)
                elif resp.status_code >= 500:
                    raise DeliveryUnknown(f"{label}: HTTP {resp.status_code}; it may have been delivered")
                else:
                    raise SourceRejectedRequest(self._rejection(label, resp))

            if wait_hint is not None and wait_hint > self.retry.max_delay:
                raise RateLimited(f"{label}: server asked to wait {wait_hint:.0f}s", retry_after=wait_hint)
            if attempt + 1 >= self.retry.max_attempts:
                break
            delay = wait_hint if wait_hint is not None else self.retry.delay(attempt)
            log.warning(
                "%s: %s (attempt %d/%d), retrying in %.1fs",
                label,
                last_problem,
                attempt + 1,
                self.retry.max_attempts,
                delay,
            )
            self._sleep(delay)

        if last_problem == "HTTP 429":
            raise RateLimited(f"{label}: still rate limited after {self.retry.max_attempts} attempts")
        raise SourceUnavailable(f"{label}: {last_problem} after {self.retry.max_attempts} attempts")
