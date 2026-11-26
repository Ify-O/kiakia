"""Unit tests for the resilient HTTP helper and the Telegram client.

No network: a fake session returns prepared responses, and a fake sleep
records the waits instead of actually waiting.
"""

import pytest
import requests

from kiakia.adapters.telegram.client import TelegramClient, telegram_error_detail, telegram_retry_after
from kiakia.domain.errors import DeliveryUnknown, RateLimited, SourceRejectedRequest, SourceUnavailable
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
    http = ResilientHttpClient(
        session=session,
        sleep=sleeps.append,
        clock=lambda: 0.0,
        min_interval=0,
        retry=RetryPolicy(jitter=0),
        **kw,
    )
    return http, session, sleeps


def test_success_first_time_uses_timeouts():
    http, session, sleeps = make_client([response(200, {"x": 1})])
    assert http.get_json("https://example.test") == {"x": 1}
    assert session.calls[0]["timeout"] == (5.0, 30.0)
    assert sleeps == []


def test_retries_transient_errors_with_exponential_backoff():
    http, session, sleeps = make_client(
        [requests.Timeout(), requests.ConnectionError(), response(503), response(200, {"ok": 1})]
    )
    assert http.get_json("https://example.test") == {"ok": 1}
    assert sleeps == [1.0, 2.0, 4.0]  # 1s, 2s, 4s
    assert len(session.calls) == 4


def test_gives_up_after_max_attempts():
    http, _, sleeps = make_client([response(500)] * 5)
    with pytest.raises(SourceUnavailable):
        http.get_json("https://example.test", label="demo")
    assert len(sleeps) == 4  # no sleep after the final attempt


def test_permanent_error_fails_fast_without_retry():
    http, session, sleeps = make_client([response(401)])
    with pytest.raises(SourceRejectedRequest):
        http.get_json("https://example.test")
    assert len(session.calls) == 1 and sleeps == []


def test_rate_limit_uses_server_hint():
    http, _, sleeps = make_client([response(429, headers={"Retry-After": "7"}), response(200, {})])
    http.get_json("https://example.test")
    assert sleeps == [7.0]


def test_huge_retry_after_is_not_obeyed_blindly():
    # W2: a server asking for 1 hour must not freeze Kiakia for 1 hour.
    http, session, sleeps = make_client([response(429, headers={"Retry-After": "3600"})])
    with pytest.raises(RateLimited) as exc:
        http.get_json("https://example.test")
    assert sleeps == []  # did not sleep at all
    assert exc.value.retry_after == 3600.0  # caller still learns how long to wait
    assert len(session.calls) == 1


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
    http = ResilientHttpClient(session=session, sleep=sleeps.append, clock=lambda: now[0], min_interval=1.0)
    http.get_json("https://example.test")
    now[0] = 0.25  # second call only 0.25s later
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
    assert params["offset"] == 42 and params["limit"] == 100  # capped at Telegram's max
    assert "123:ABC" not in repr(client)


def test_telegram_ok_false_is_rejected():
    http, _, _ = make_client([response(200, {"ok": False, "description": "Unauthorized"})])
    with pytest.raises(SourceRejectedRequest):
        TelegramClient("123:ABC", http=http).get_updates(offset=None)


# ---------- POST for non-idempotent actions (sending messages) ----------


class FakePostSession(FakeSession):
    def post(self, url, json=None, timeout=None):
        return self.get(url, params=json, timeout=timeout)


def make_poster(outcomes):
    sleeps = []
    session = FakePostSession(outcomes)
    http = ResilientHttpClient(
        session=session,
        sleep=sleeps.append,
        clock=lambda: 0.0,
        min_interval=0,
        retry=RetryPolicy(jitter=0),
        error_detail_reader=telegram_error_detail,
    )
    return http, session, sleeps


def test_post_read_timeout_is_never_retried():
    # The request may have reached Telegram: retrying could message the customer twice.
    http, session, sleeps = make_poster([requests.ReadTimeout(), response(200, {"ok": True})])
    with pytest.raises(DeliveryUnknown):
        http.post_json_unsafe("https://example.test", json_body={})
    assert len(session.calls) == 1 and sleeps == []


def test_post_5xx_is_never_retried():
    http, session, _ = make_poster([response(502), response(200, {})])
    with pytest.raises(DeliveryUnknown):
        http.post_json_unsafe("https://example.test", json_body={})
    assert len(session.calls) == 1


def test_post_connect_timeout_is_retried_because_nothing_was_sent():
    http, session, sleeps = make_poster([requests.ConnectTimeout(), response(200, {"ok": True})])
    assert http.post_json_unsafe("https://example.test", json_body={}) == {"ok": True}
    assert len(session.calls) == 2 and sleeps == [1.0]


def test_post_429_is_retried_with_the_server_hint():
    http, _, sleeps = make_poster([response(429, headers={"Retry-After": "2"}), response(200, {"ok": True})])
    http.post_json_unsafe("https://example.test", json_body={})
    assert sleeps == [2.0]


def test_post_refusal_shows_telegrams_reason():
    http, _, _ = make_poster(
        [response(403, {"ok": False, "description": "Forbidden: bot was blocked by the user"})]
    )
    with pytest.raises(SourceRejectedRequest, match="bot was blocked"):
        http.post_json_unsafe("https://example.test", json_body={}, label="telegram sendMessage")


def test_telegram_sender_returns_message_id_and_utc_time():
    from kiakia.adapters.telegram.sender import TelegramSender

    http, session, _ = make_poster(
        [response(200, {"ok": True, "result": {"message_id": 77, "chat": {"id": 9001}, "date": 1795600000}})]
    )
    sent = TelegramSender(TelegramClient("123:ABC", http=http)).send_text("9001", "Hello")
    assert sent.external_message_id == "77" and sent.sent_at.tzinfo is not None
    assert session.calls[0]["params"] == {"chat_id": "9001", "text": "Hello"}


def test_long_polling_asks_telegram_to_wait_but_never_longer_than_the_read_timeout():
    http, session, _ = make_client([response(200, {"ok": True, "result": []})] * 2)
    client = TelegramClient("123:ABC", http=http)
    client.get_updates(offset=None, wait_seconds=25)
    client.get_updates(offset=None, wait_seconds=999)
    assert [c["params"]["timeout"] for c in session.calls] == [25, 25]


def test_other_request_errors_are_retried_and_never_show_the_url():
    # e.g. Telegram's answer broke off half-way. The message would contain the URL (and so the
    # bot token), so only the error's class name may appear in what we log and raise.
    secret_url = "https://api.telegram.org/botSECRET-TOKEN/getUpdates"
    broken = requests.exceptions.ChunkedEncodingError(f"broken while reading {secret_url}")
    http, session, _ = make_client([broken] * 5)
    with pytest.raises(SourceUnavailable) as caught:
        http.get_json(secret_url, label="telegram getUpdates")
    assert "ChunkedEncodingError" in str(caught.value)
    assert "SECRET-TOKEN" not in str(caught.value)
    assert len(session.calls) == 5


def test_post_other_request_error_is_delivery_unknown_and_never_retried():
    secret_url = "https://api.telegram.org/botSECRET-TOKEN/sendMessage"
    broken = requests.exceptions.ChunkedEncodingError(f"broken while reading {secret_url}")
    http, session, sleeps = make_poster([broken, response(200, {"ok": True})])
    with pytest.raises(DeliveryUnknown) as caught:
        http.post_json_unsafe(secret_url, json_body={})
    assert "SECRET-TOKEN" not in str(caught.value)
    assert len(session.calls) == 1 and sleeps == []
