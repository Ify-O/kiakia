"""Contract tests: every response must match openapi.yaml (docs/adr/0001).

Each scenario calls the real API (on a seeded test database), then:
1. validates the response against the contract with openapi-core, and
2. checks the behaviour (filters, pagination, errors) is right.
"""

import json
from pathlib import Path

import pytest
from openapi_core import Config, OpenAPI
from openapi_core.testing import MockRequest, MockResponse

pytestmark = pytest.mark.integration

CONTRACT = OpenAPI.from_file_path(
    str(Path(__file__).resolve().parents[2] / "openapi.yaml"),
    # Teach the validator to read RFC 9457 error bodies (they're JSON with their own media type).
    config=Config(extra_media_type_deserializers={"application/problem+json": json.loads}),
)
SERVER = "http://localhost:8000"


def call(client, path, params=None):
    """GET through the API and validate the response against the contract."""
    response = client.get(path, params=params)
    request = MockRequest(SERVER, "get", path, args=params or {})
    content_type = response.headers.get("content-type", "").split(";")[0]
    CONTRACT.validate_response(
        request,
        MockResponse(
            response.content,
            status_code=response.status_code,
            headers=dict(response.headers),
            content_type=content_type,
        ),
    )
    return response


def post(client, path, body, key="key-" + "0" * 12, raw=None):
    """POST through the API and validate the response against the contract."""
    headers = {"Content-Type": "application/json"}
    if key is not None:
        headers["Idempotency-Key"] = key
    data = raw if raw is not None else json.dumps(body)
    response = client.post(path, content=data, headers=headers)
    request = MockRequest(SERVER, "post", path, headers=headers, data=data.encode())
    content_type = response.headers.get("content-type", "").split(";")[0]
    CONTRACT.validate_response(
        request,
        MockResponse(
            response.content,
            status_code=response.status_code,
            headers=dict(response.headers),
            content_type=content_type,
        ),
    )
    return response


def names(response):
    return [c["contact"]["displayName"] for c in response.json()["data"]]


# ---------- list ----------


def test_list_all_most_recent_first(client):
    r = call(client, "/v1/conversations")
    assert r.status_code == 200
    assert names(r) == ["100% Real_Deal", "Chidi", "Bayo", "Ada Test"]
    assert r.json()["page"] == {"limit": 20, "nextCursor": None}


def test_filter_waiting_longest_first(client):
    r = call(client, "/v1/conversations", {"status": "waiting", "sort": "longestWaiting"})
    assert names(r) == ["Ada Test", "Chidi", "100% Real_Deal"]
    ada = r.json()["data"][0]
    assert ada["status"] == "waiting" and ada["unansweredCount"] == 2 and ada["waitingSince"] is not None
    assert ada["lastMessagePreview"] == "Do you have it in red?"


def test_filter_answered_has_nulls_where_the_contract_says(client):
    r = call(client, "/v1/conversations", {"status": "answered"})
    [bayo] = r.json()["data"]
    assert bayo["status"] == "answered" and bayo["waitingSince"] is None and bayo["lastReplyAt"] is not None
    assert bayo["contact"]["username"] is None  # nullable, but always present


def test_voice_note_preview(client):
    r = call(client, "/v1/conversations", {"q": "chidi"})
    assert r.json()["data"][0]["lastMessagePreview"] == "[voice]"


def test_search_treats_percent_and_underscore_literally(client):
    # Unescaped, "%" and "_" are LIKE wildcards and would match EVERY customer.
    assert names(call(client, "/v1/conversations", {"q": "100%"})) == ["100% Real_Deal"]
    assert names(call(client, "/v1/conversations", {"q": "%"})) == ["100% Real_Deal"]
    # A literal "_" is in "Real_Deal" and in Ada's username "ada_test"; Bayo and Chidi have none.
    assert names(call(client, "/v1/conversations", {"q": "_"})) == ["100% Real_Deal", "Ada Test"]


def test_channel_filter(client):
    assert len(call(client, "/v1/conversations", {"channel": "telegram"}).json()["data"]) == 4
    assert call(client, "/v1/conversations", {"channel": "whatsapp"}).json()["data"] == []


def test_cursor_pagination_walks_every_item_once(client):
    seen, params = [], {"limit": 3}
    while True:
        body = call(client, "/v1/conversations", params).json()
        seen += [c["id"] for c in body["data"]]
        if body["page"]["nextCursor"] is None:
            break
        params = {"limit": 3, "cursor": body["page"]["nextCursor"]}
    assert len(seen) == len(set(seen)) == 4


# ---------- get one + messages ----------


def test_get_one_conversation(client):
    ada_id = call(client, "/v1/conversations", {"q": "ada"}).json()["data"][0]["id"]
    r = call(client, f"/v1/conversations/{ada_id}")
    assert r.status_code == 200
    assert r.json()["messageCount"] == 2 and r.json()["contact"]["username"] == "ada_test"


def test_messages_newest_first_and_paginated(client):
    ada_id = call(client, "/v1/conversations", {"q": "ada"}).json()["data"][0]["id"]
    first = call(client, f"/v1/conversations/{ada_id}/messages", {"limit": 1}).json()
    assert first["data"][0]["body"] == "Do you have it in red?"
    second = call(
        client, f"/v1/conversations/{ada_id}/messages", {"limit": 1, "cursor": first["page"]["nextCursor"]}
    ).json()
    assert second["data"][0]["body"] == "Hello, how much is the blue bag?"
    assert second["page"]["nextCursor"] is None


# ---------- stats + health ----------


def test_stats(client):
    body = call(client, "/v1/stats").json()
    assert body["conversations"] == 4 and body["waiting"] == 3 and body["messages"] == 6
    assert body["messagesByKind"] == {"text": 5, "voice": 1}


def test_health(client):
    assert call(client, "/health").json() == {"status": "ok", "database": "ok"}


# ---------- errors: Problem Details, correct status codes ----------


def test_unknown_conversation_is_404_problem(client):
    r = call(client, "/v1/conversations/999999")
    assert r.status_code == 404
    assert r.headers["content-type"].startswith("application/problem+json")
    assert r.json()["type"].endswith("/not-found") and "999999" in r.json()["detail"]


@pytest.mark.parametrize(
    ("params", "field"),
    [
        ({"limit": 0}, "limit"),
        ({"limit": 101}, "limit"),
        ({"status": "sleeping"}, "status"),
        ({"channel": "myspace"}, "channel"),
        ({"cursor": "not-a-real-cursor"}, "cursor"),
    ],
)
def test_bad_query_is_400_with_field_errors(client, params, field):
    r = call(client, "/v1/conversations", params)
    assert r.status_code == 400
    assert field in [e["field"] for e in r.json()["errors"]]


def test_bad_path_id_is_400(client):
    r = call(client, "/v1/conversations/0")
    assert r.status_code == 400 and r.json()["errors"][0]["field"] == "conversationId"


def test_cursor_from_another_sort_is_rejected(client):
    cursor = call(client, "/v1/conversations", {"limit": 1}).json()["page"]["nextCursor"]
    r = call(client, "/v1/conversations", {"sort": "longestWaiting", "cursor": cursor})
    assert r.status_code == 400


def test_problem_never_leaks_internals(client):
    r = client.get("/v1/conversations", params={"cursor": "' OR 1=1 --"})
    text = r.text.lower()
    assert r.status_code == 400 and "traceback" not in text and "select" not in text


# ---------- POST /replies: validation, idempotency, channel errors ----------


def waiting_id(client, name):
    return call(client, "/v1/conversations", {"q": name}).json()["data"][0]["id"]


def test_reply_is_sent_once_and_ends_the_wait(client, sender):
    ada = waiting_id(client, "ada")
    r = post(client, f"/v1/conversations/{ada}/replies", {"text": "  It's ₦15,000!  "})
    assert r.status_code == 201
    assert r.headers["Location"] == f"/v1/conversations/{ada}/messages"
    assert r.headers["Idempotent-Replayed"] == "false"
    assert r.json()["direction"] == "out" and r.json()["body"] == "It's ₦15,000!"
    assert sender.sent == [("9001", "It's ₦15,000!")]
    detail = call(client, f"/v1/conversations/{ada}").json()
    assert detail["status"] == "answered" and detail["waitingSince"] is None


def test_retry_with_same_key_is_replayed_not_resent(client, sender):
    ada = waiting_id(client, "ada")
    first = post(client, f"/v1/conversations/{ada}/replies", {"text": "Hello"}, key="retry-key-0001")
    again = post(client, f"/v1/conversations/{ada}/replies", {"text": "Hello"}, key="retry-key-0001")
    assert again.status_code == 201 and again.headers["Idempotent-Replayed"] == "true"
    assert again.json() == first.json()
    assert len(sender.sent) == 1  # the customer got it ONCE


def test_same_key_with_different_text_is_409(client, sender):
    ada = waiting_id(client, "ada")
    post(client, f"/v1/conversations/{ada}/replies", {"text": "Hello"}, key="reused-key-001")
    r = post(client, f"/v1/conversations/{ada}/replies", {"text": "Bye"}, key="reused-key-001")
    assert r.status_code == 409 and r.json()["type"].endswith("/idempotency-key-reused")
    assert len(sender.sent) == 1


@pytest.mark.parametrize(
    ("body", "field"),
    [
        ({"text": ""}, "text"),
        ({"text": "   "}, "text"),
        ({"text": "x" * 4097}, "text"),
        ({}, "text"),
        ({"text": "hi", "extra": 1}, "extra"),
    ],
)
def test_invalid_body_is_422_with_field_errors(client, sender, body, field):
    ada = waiting_id(client, "ada")
    r = post(client, f"/v1/conversations/{ada}/replies", body)
    assert r.status_code == 422 and field in [e["field"] for e in r.json()["errors"]]
    assert sender.sent == []


@pytest.mark.parametrize("key", [None, "short", "has spaces in it", "x" * 256])
def test_missing_or_bad_idempotency_key_is_400(client, sender, key):
    ada = waiting_id(client, "ada")
    r = post(client, f"/v1/conversations/{ada}/replies", {"text": "Hello"}, key=key)
    assert r.status_code == 400 and "Idempotency-Key" in [e["field"] for e in r.json()["errors"]]
    assert sender.sent == []


def test_malformed_json_is_400(client):
    ada = waiting_id(client, "ada")
    assert post(client, f"/v1/conversations/{ada}/replies", None, raw="{not json").status_code == 400


def test_reply_to_unknown_conversation_is_404(client):
    assert post(client, "/v1/conversations/999999/replies", {"text": "Hello"}).status_code == 404


def test_channel_refusal_is_502_and_the_key_can_be_retried(client, sender):
    from kiakia.domain.errors import SourceRejectedRequest

    ada = waiting_id(client, "ada")
    sender.error = SourceRejectedRequest("telegram sendMessage: Forbidden: bot was blocked by the user")
    r = post(client, f"/v1/conversations/{ada}/replies", {"text": "Hello"}, key="blocked-key-01")
    assert r.status_code == 502 and "blocked" in r.json()["detail"]
    sender.error = None
    assert (
        post(client, f"/v1/conversations/{ada}/replies", {"text": "Hello"}, key="blocked-key-01").status_code
        == 201
    )


def test_unconfirmed_delivery_is_502_and_the_key_stays_locked(client, sender):
    from kiakia.domain.errors import DeliveryUnknown

    ada = waiting_id(client, "ada")
    sender.error = DeliveryUnknown("telegram sendMessage: no answer in time; it may have been delivered")
    r = post(client, f"/v1/conversations/{ada}/replies", {"text": "Hello"}, key="maybe-sent-key")
    assert r.status_code == 502 and r.json()["type"].endswith("/delivery-unknown")
    sender.error = None
    assert (
        post(client, f"/v1/conversations/{ada}/replies", {"text": "Hello"}, key="maybe-sent-key").status_code
        == 409
    )
    assert sender.sent == []  # never sent twice


def test_form_errors_are_in_plain_language(client):
    ada = waiting_id(client, "ada")
    r = post(client, f"/v1/conversations/{ada}/replies", {"text": "   "})
    assert r.json()["errors"] == [{"field": "text", "message": "must not be empty or only spaces"}]
    r = post(client, f"/v1/conversations/{ada}/replies", {"text": "hi"}, key=None)
    assert r.json()["errors"][0]["message"].startswith("is required")


# ---------- ADR 0006: PATCH, filters, feed, period stats ----------


def patch(client, path, body):
    data = json.dumps(body)
    headers = {"Content-Type": "application/json"}
    response = client.patch(path, content=data, headers=headers)
    content_type = response.headers.get("content-type", "").split(";")[0]
    CONTRACT.validate_response(
        MockRequest(SERVER, "patch", path, headers=headers, data=data.encode()),
        MockResponse(
            response.content,
            status_code=response.status_code,
            headers=dict(response.headers),
            content_type=content_type,
        ),
    )
    return response


def test_resolve_and_categorise_then_reopen(client):
    ada = waiting_id(client, "ada")
    r = patch(client, f"/v1/conversations/{ada}", {"resolved": True, "category": "order"})
    assert r.status_code == 200
    assert r.json()["status"] == "resolved" and r.json()["category"] == "order" and r.json()["resolvedAt"]
    assert names(call(client, "/v1/conversations", {"status": "resolved"})) == ["Ada Test"]
    assert "Ada Test" not in names(call(client, "/v1/conversations", {"status": "waiting"}))
    assert names(call(client, "/v1/conversations", {"category": "order"})) == ["Ada Test"]
    again = patch(client, f"/v1/conversations/{ada}", {"resolved": True})  # same again: no change
    assert again.json()["resolvedAt"] == r.json()["resolvedAt"]
    reopened = patch(client, f"/v1/conversations/{ada}", {"resolved": False, "category": None})
    assert reopened.json()["status"] == "waiting" and reopened.json()["category"] is None


@pytest.mark.parametrize(
    ("body", "field"),
    [
        ({}, "body"),
        ({"resolved": "true"}, "resolved"),
        ({"category": "sales"}, "category"),
        ({"resolved": None}, "resolved"),
        ({"colour": "red"}, "colour"),
    ],
)
def test_bad_patch_is_422(client, body, field):
    ada = waiting_id(client, "ada")
    r = patch(client, f"/v1/conversations/{ada}", body)
    assert r.status_code == 422 and field in [e["field"] for e in r.json()["errors"]]


def test_patch_unknown_conversation_is_404(client):
    assert patch(client, "/v1/conversations/999999", {"resolved": True}).status_code == 404


def test_feed_by_channel_and_direction(client):
    body = call(client, "/v1/messages", {"channel": "telegram", "direction": "in", "limit": 2}).json()
    assert len(body["data"]) == 2 and body["page"]["nextCursor"]
    first = body["data"][0]
    assert first["message"]["channel"] == "telegram" and first["message"]["direction"] == "in"
    assert first["contact"]["displayName"] == "100% Real_Deal"  # newest customer message
    assert call(client, "/v1/messages", {"channel": "whatsapp"}).json()["data"] == []


def test_period_filters_and_stats(client):
    from datetime import UTC, datetime, timedelta

    now = datetime.now(UTC)
    recent = (now - timedelta(minutes=20)).isoformat()
    assert names(call(client, "/v1/conversations", {"from": recent})) == ["100% Real_Deal"]
    stats = call(client, "/v1/stats", {"from": recent}).json()
    assert stats["messages"] == 1 and stats["waiting"] == 3  # waiting is "right now", not the period
    full = call(client, "/v1/stats").json()
    assert full["messagesReceived"] == 5 and full["responseRate"] == 0.25  # 1 of 4 chats answered
    assert full["medianFirstResponseSeconds"] == 5 * 60  # Bayo: 50 -> 45 min ago
    assert full["byChannel"][0]["channel"] == "telegram" and full["byCategory"]["none"] == 4


@pytest.mark.parametrize(
    ("params", "field"),
    [
        ({"from": "2026-11-25T10:00:00Z", "to": "2026-11-25T09:00:00Z"}, "from"),
        ({"from": "2026-11-25T10:00:00"}, "from"),  # no time zone
        ({"to": "yesterday"}, "to"),
    ],
)
def test_bad_periods_are_400(client, params, field):
    for path in ("/v1/conversations", "/v1/stats", "/v1/messages"):
        r = call(client, path, params)
        assert r.status_code == 400 and field in [e["field"] for e in r.json()["errors"]], path
