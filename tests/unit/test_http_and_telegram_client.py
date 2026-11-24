"""Unit tests for the resilient HTTP helper and the Telegram client.

No network: a fake session returns prepared responses, and a fake sleep
records the waits instead of actually waiting.
"""
import pytest
import requests

from kiakia.adapters.telegram.client import TelegramClient, telegram_retry_after
from kiakia.domain.errors import RateLimited, SourceRejectedRequest, SourceUnavailable
from kiakia.infrastructure.http import ResilientHttpClient, RetryPolicy


def response(status, json_body=None, headers=None):
    r = requests.Response()
    r.status_code = status
    r.headers.update(headers or {})
    import json
    r._content = json.dumps(json_body if json_body is not None else {}).encode()
    return r


class FakeSession:
    """Returns (or raises) the prepared outcomes in order."""

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []
        self.headers = {}

    def get(self, url, params=None, timeout=None):
        self.calls.append({"url": url, "params": params, "timeout": timeout})
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def make_client(outcomes, **kw):
    sleeps = []
    session = FakeSession(outcomes)
    http = ResilientHttpClient(session=session, sleep=sleeps.append, clock=lambda: 0.0,
                               min_interval=0, retry=RetryPolicy(jitter=0), **kw)
    return http, session, sleeps


def test_success_first_time_uses_timeouts():
    http, session, sleeps = make_client([response(200, {"x": 1})])
    assert http.get_json("https://example.test") == {"x": 1}
    assert session.calls[0]["timeout"] == (5.0, 30.0)
    assert sleeps == []


def test_retries_transient_errors_with_exponential_backoff():
    http, session, sleeps = make_client([
        requests.Timeout(), requests.ConnectionError(), response(503), response(200, {"ok": 1})])
    assert http.get_json("https://example.test") == {"ok": 1}
    assert sleeps == [1.0, 2.0, 4.0]          # 1s, 2s, 4s
    assert len(session.calls) == 4


def test_gives_up_after_max_attempts():
    http, _, sleeps = make_client([response(500)] * 5)
    with pytest.raises(SourceUnavailable):
        http.get_json("https://example.test", label="demo")
    assert len(sleeps) == 4                   # no sleep after the final attempt


def test_permanent_error_fails_fast_without_retry():
    http, session, sleeps = make_client([response(401)])
    with pytest.raises(SourceRejectedRequest):
        http.get_json("https://example.test")
    assert len(session.calls) == 1 and sleeps == []


def test_rate_limit_uses_server_hint():
    http, _, sleeps = make_client([response(429, headers={"Retry-After": "7"}), response(200, {})])
    http.get_json("https://example.test")
    assert sleeps == [7.0]


def test_rate_limited_forever_raises_rate_limited():
    http, _, _ = make_client([response(429)] * 5)
    with pytest.raises(RateLimited):
        http.get_json("https://example.test")


def test_errors_never_contain_the_url():
    http, _, _ = make_client([response(500)] * 5)
    with pytest.raises(SourceUnavailable) as exc:
        http.get_json("https://api.telegram.org/botSECRET-TOKEN/getUpdates", label="telegram getUpdates")
    assert "SECRET-TOKEN" not in str(exc.value)


def test_min_interval_spaces_out_requests():
    sleeps, now = [], [0.0]
    session = FakeSession([response(200, {}), response(200, {})])
    http = ResilientHttpClient(session=session, sleep=sleeps.append, clock=lambda: now[0],
                               min_interval=1.0)
    http.get_json("https://example.test")
    now[0] = 0.25                              # second call only 0.25s later
    http.get_json("https://example.test")
    assert sleeps == [0.75]


def test_telegram_reads_retry_after_from_json_body():
    r = response(429, {"ok": False, "error_code": 429, "parameters": {"retry_after": 3}})
    assert telegram_retry_after(r) == 3.0


def test_telegram_client_sends_offset_and_returns_result():
    http, session, _ = make_client([response(200, {"ok": True, "result": [{"update_id": 1}]})])
    client = TelegramClient("123:ABC", http=http)
    assert client.get_updates(offset=42, limit=500) == [{"update_id": 1}]
    params = session.calls[0]["params"]
    assert params["offset"] == 42 and params["limit"] == 100   # capped at Telegram's max
    assert "123:ABC" not in repr(client)


def test_telegram_ok_false_is_rejected():
    http, _, _ = make_client([response(200, {"ok": False, "description": "Unauthorized"})])
    with pytest.raises(SourceRejectedRequest):
        TelegramClient("123:ABC", http=http).get_updates(offset=None)
