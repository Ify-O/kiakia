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
