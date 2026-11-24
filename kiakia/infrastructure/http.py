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

from kiakia.domain.errors import RateLimited, SourceRejectedRequest, SourceUnavailable

log = logging.getLogger(__name__)

TRANSIENT_STATUSES = {429, 500, 502, 503, 504}

# A function that reads a "wait N seconds" hint from a response (APIs do this differently).
RetryAfterReader = Callable[[requests.Response], float | None]


def header_retry_after(resp: requests.Response) -> float | None:
    value = resp.headers.get("Retry-After", "")
    return float(value) if value.strip().isdigit() else None


@dataclass
class RetryPolicy:
    max_attempts: int = 5          # 1 try + 4 retries
    base_delay: float = 1.0        # 1s, 2s, 4s, 8s ...
    max_delay: float = 30.0
    jitter: float = 0.5            # +/- 50% randomness so retries don't all line up

    def delay(self, attempt: int, rng: Callable[[], float] = random.random) -> float:
        raw = min(self.max_delay, self.base_delay * (2 ** attempt))
        return raw * (1 - self.jitter + 2 * self.jitter * rng())


class ResilientHttpClient:
    def __init__(
        self,
        *,
        timeout: tuple = (5.0, 30.0),             # (connect, read) seconds
        retry: RetryPolicy | None = None,
        min_interval: float = 1.0,                # at most ~1 request per second
        retry_after_reader: RetryAfterReader = header_retry_after,
        session: requests.Session | None = None,
        sleep: Callable[[float], None] = time.sleep,          # injectable for tests
        clock: Callable[[], float] = time.monotonic,
    ):
        self.timeout = timeout
        self.retry = retry or RetryPolicy()
        self.min_interval = min_interval
        self.retry_after_reader = retry_after_reader
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

    def get_json(self, url: str, *, params: dict[str, Any] | None = None,
                 label: str = "request") -> Any:
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
                    raise SourceRejectedRequest(
                        f"{label}: HTTP {resp.status_code} (permanent; check the token/request)")

            if attempt + 1 >= self.retry.max_attempts:
                break
            delay = wait_hint if wait_hint is not None else self.retry.delay(attempt)
            log.warning("%s: %s (attempt %d/%d), retrying in %.1fs",
                        label, last_problem, attempt + 1, self.retry.max_attempts, delay)
            self._sleep(delay)

        if last_problem == "HTTP 429":
            raise RateLimited(f"{label}: still rate limited after {self.retry.max_attempts} attempts")
        raise SourceUnavailable(f"{label}: {last_problem} after {self.retry.max_attempts} attempts")
