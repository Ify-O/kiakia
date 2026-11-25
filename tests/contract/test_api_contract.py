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
    assert call(client, "/health").json() == {"status": "ok", "database": "ok", "demoMode": False}


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


# ---------- ADR 0007: customers + recorded consent ----------


def post_json(client, path, body):
    data = json.dumps(body)
    headers = {"Content-Type": "application/json"}
    response = client.post(path, content=data, headers=headers)
    content_type = response.headers.get("content-type", "").split(";")[0]
    CONTRACT.validate_response(
        MockRequest(SERVER, "post", path, headers=headers, data=data.encode()),
        MockResponse(
            response.content,
            status_code=response.status_code,
            headers=dict(response.headers),
            content_type=content_type,
        ),
    )
    return response


def ada_ids(client):
    conv = call(client, "/v1/conversations", {"q": "ada"}).json()["data"][0]
    return conv["contact"]["id"], conv["id"]


def test_new_customers_show_unknown_consent_everywhere(client):
    conv = call(client, "/v1/conversations", {"q": "ada"}).json()["data"][0]
    assert conv["contact"]["detailsConsent"] == "unknown" and conv["contact"]["marketingConsent"] == "unknown"
    feed = call(client, "/v1/messages", {"limit": 1}).json()["data"][0]
    assert feed["contact"]["detailsConsent"] == "unknown"


def test_customer_list_and_detail(client):
    body = call(client, "/v1/customers").json()
    assert len(body["data"]) == 4 and body["data"][0]["displayName"] == "100% Real_Deal"
    ada_id, _ = ada_ids(client)
    detail = call(client, f"/v1/customers/{ada_id}").json()
    assert detail["firstChannel"] == "telegram" and detail["channels"] == [
        {"channel": "telegram", "username": "ada_test"}
    ]
    assert detail["phone"] is None and detail["consentHistory"] == [] and len(detail["conversations"]) == 1


def test_full_consent_flow_in_the_chat(client):
    ada_id, conv_id = ada_ids(client)
    path = f"/v1/customers/{ada_id}/consent"
    asked = post_json(
        client,
        path,
        {
            "kind": "details",
            "status": "asked",
            "conversationId": conv_id,
            "wording": "Can we save your contact details? Reply YES or NO.",
        },
    )
    assert asked.status_code == 200 and asked.json()["detailsConsent"] == "asked"
    yes = post_json(
        client,
        path,
        {
            "kind": "details",
            "status": "granted",
            "conversationId": conv_id,
            "details": {"name": "Ada Okafor", "phone": "+234 803 123 4567", "email": "ada@example.com"},
        },
    )
    body = yes.json()
    assert (body["displayName"], body["fullName"], body["phone"], body["email"]) == (
        "Ada Test",  # the platform name stays separate from the name she gave us (ADR 0009)
        "Ada Okafor",
        "+234 803 123 4567",
        "ada@example.com",
    )
    assert [e["status"] for e in body["consentHistory"]] == ["asked", "granted"]
    assert body["consentHistory"][1]["evidenceMessageId"] is not None  # the customer's message, kept as proof
    assert body["consentHistory"][0]["channel"] == "telegram"
    offers_no = post_json(
        client, path, {"kind": "marketing", "status": "declined", "conversationId": conv_id}
    )
    assert (
        offers_no.json()["marketingConsent"] == "declined" and offers_no.json()["detailsConsent"] == "granted"
    )
    again = post_json(client, path, {"kind": "marketing", "status": "declined"})  # repeat: no new event
    assert len(again.json()["consentHistory"]) == 3
    filtered = call(client, "/v1/customers", {"detailsConsent": "granted"}).json()["data"]
    assert [c["fullName"] for c in filtered] == ["Ada Okafor"]


# ---------- ADR 0009: withdraw, forget, export ----------


def give_details_and_offers(client, customer_id, conv_id, details):
    path = f"/v1/customers/{customer_id}/consent"
    post_json(
        client, path, {"kind": "details", "status": "granted", "conversationId": conv_id, "details": details}
    )
    return post_json(client, path, {"kind": "marketing", "status": "granted", "conversationId": conv_id})


def test_withdrawing_details_erases_the_given_name_and_ends_offers(client):
    ada_id, conv_id = ada_ids(client)
    details = {"name": "Ada Okafor", "phone": "+234 803 123 4567", "email": "ada@example.com"}
    assert give_details_and_offers(client, ada_id, conv_id, details).json()["marketingConsent"] == "granted"
    body = post_json(
        client, f"/v1/customers/{ada_id}/consent", {"kind": "details", "status": "withdrawn"}
    ).json()
    assert (body["fullName"], body["phone"], body["email"]) == (None, None, None)
    assert body["displayName"] == "Ada Test"  # the platform name is still needed to reply
    assert (body["detailsConsent"], body["marketingConsent"]) == ("withdrawn", "withdrawn")
    assert [(e["kind"], e["status"]) for e in body["consentHistory"][-2:]] == [
        ("details", "withdrawn"),
        ("marketing", "withdrawn"),  # recorded as its own event, so the history explains it
    ]


def test_offers_need_saved_details_first(client):
    ada_id, _ = ada_ids(client)
    r = post_json(client, f"/v1/customers/{ada_id}/consent", {"kind": "marketing", "status": "granted"})
    assert r.status_code == 422
    assert r.json()["errors"][0]["field"] == "status"


def test_forget_customer_erases_everything_once(client, conn):
    ada_id, conv_id = ada_ids(client)
    give_details_and_offers(client, ada_id, conv_id, {"phone": "+234 803 123 4567"})
    assert client.delete(f"/v1/customers/{ada_id}").status_code == 204
    assert call(client, f"/v1/customers/{ada_id}").status_code == 404
    assert call(client, f"/v1/conversations/{conv_id}").status_code == 404
    assert call(client, "/v1/conversations", {"q": "ada"}).json()["data"] == []
    second = client.delete(f"/v1/customers/{ada_id}")
    assert second.status_code == 404 and second.headers["content-type"].startswith("application/problem+json")
    with conn.cursor() as cur:  # the privacy log proves it happened, without saying who
        cur.execute("SELECT action, detail FROM privacy_log")
        [(action, detail)] = cur.fetchall()
        cur.execute("SELECT count(*) FROM messages WHERE body ILIKE '%%blue bag%%'")
        assert cur.fetchone()[0] == 0
    assert action == "erasure" and detail == {"channels": ["telegram"], "conversations": 1, "messages": 2}
    assert "Ada" not in json.dumps(detail)


def export(client, **params):
    r = client.get("/v1/exports/customers", params=params)
    assert r.status_code == 200, r.text
    assert r.headers["cache-control"] == "no-store"
    return r


def test_export_contains_only_people_who_said_yes_to_offers(client):
    ada_id, ada_conv = ada_ids(client)
    give_details_and_offers(client, ada_id, ada_conv, {"name": "Ada Okafor", "phone": "0803 123 4567"})
    bayo = call(client, "/v1/conversations", {"q": "bayo"}).json()["data"][0]
    path = f"/v1/customers/{bayo['contact']['id']}/consent"
    post_json(client, path, {"kind": "details", "status": "granted", "details": {"email": "b@example.com"}})
    # Bayo saved details but never said yes to offers: he must not be in an ads list.
    r = export(client)
    assert r.headers["content-type"].startswith("text/csv")
    assert 'attachment; filename="kiakia-offers-list-' in r.headers["content-disposition"]
    lines = r.content.decode("utf-8-sig").splitlines()
    assert lines[0] == "Name,Phone,Email,Platform,Customer since"
    assert len(lines) == 2 and lines[1].startswith("Ada Okafor,0803 123 4567,,Telegram,")


def test_google_ads_layout_and_formula_safety(client):
    ada_id, ada_conv = ada_ids(client)
    give_details_and_offers(
        client, ada_id, ada_conv, {"name": "=HYPERLINK(evil) Okafor", "phone": "0803 123 4567"}
    )
    lines = export(client, layout="google-ads").content.decode("utf-8-sig").splitlines()
    assert lines == [
        "Email,Phone,First Name,Last Name,Country",
        ",+2348031234567,'=HYPERLINK(evil),Okafor,NG",
    ]


def test_excel_export_is_a_real_workbook_with_text_cells(client):
    from io import BytesIO

    from openpyxl import load_workbook

    ada_id, ada_conv = ada_ids(client)
    give_details_and_offers(client, ada_id, ada_conv, {"name": "=1+1", "phone": "+234 803 123 4567"})
    r = export(client, format="xlsx")
    assert r.headers["content-disposition"].endswith('.xlsx"')
    sheet = load_workbook(BytesIO(r.content)).active
    rows = [[c.value for c in row] for row in sheet.iter_rows()]
    assert rows[0] == ["Name", "Phone", "Email", "Platform", "Customer since"]
    assert rows[1][:2] == ["=1+1", "+234 803 123 4567"]
    assert "f" not in {c.data_type for c in sheet[2]}  # stored as text: Excel shows it, never runs it


def test_export_rejects_unknown_options(client):
    for params in ({"format": "pdf"}, {"layout": "facebook"}):
        r = client.get("/v1/exports/customers", params=params)
        assert r.status_code == 400 and r.headers["content-type"].startswith("application/problem+json")


def test_saying_no_saves_nothing_and_is_remembered(client):
    ada_id, conv_id = ada_ids(client)
    r = post_json(
        client,
        f"/v1/customers/{ada_id}/consent",
        {"kind": "details", "status": "declined", "conversationId": conv_id},
    )
    assert r.json()["detailsConsent"] == "declined" and r.json()["phone"] is None
    conv = call(client, f"/v1/conversations/{conv_id}").json()
    assert conv["contact"]["detailsConsent"] == "declined"  # the "no" is remembered, so we never ask again


@pytest.mark.parametrize(
    "body",
    [
        {
            "kind": "marketing",
            "status": "granted",
            "details": {"name": "Ada"},
        },  # details only with a details yes
        {"kind": "details", "status": "granted", "details": {}},  # share at least one thing
        {"kind": "details", "status": "granted", "details": {"phone": "call me"}},
        {"kind": "details", "status": "granted", "details": {"email": "not-an-email"}},
        {"kind": "details", "status": "unknown"},
        {"kind": "photos", "status": "granted"},
        {"kind": "details", "status": "asked", "conversationId": "5"},
    ],
)
def test_bad_consent_records_are_422(client, body):
    ada_id, _ = ada_ids(client)
    assert post_json(client, f"/v1/customers/{ada_id}/consent", body).status_code == 422


@pytest.mark.parametrize(
    ("field", "value", "hint"),
    [("phone", "call me", "+234 802 555 0123"), ("email", "not-an-email", "name@example.com")],
)
def test_bad_phone_or_email_gets_a_plain_language_message(client, field, value, hint):
    """The form shows this text under the field, so it must not be a regex."""
    ada_id, _ = ada_ids(client)
    body = {"kind": "details", "status": "granted", "details": {field: value}}
    [error] = post_json(client, f"/v1/customers/{ada_id}/consent", body).json()["errors"]
    assert error["field"] == field
    assert hint in error["message"] and "pattern" not in error["message"]


def test_evidence_must_be_the_customers_own_chat(client):
    ada_id, _ = ada_ids(client)
    bayo_conv = call(client, "/v1/conversations", {"q": "bayo"}).json()["data"][0]["id"]
    r = post_json(
        client,
        f"/v1/customers/{ada_id}/consent",
        {"kind": "details", "status": "asked", "conversationId": bayo_conv},
    )
    assert r.status_code == 404


def test_unknown_customer_is_404(client):
    assert call(client, "/v1/customers/999999").status_code == 404
    assert (
        post_json(client, "/v1/customers/999999/consent", {"kind": "details", "status": "asked"}).status_code
        == 404
    )


@pytest.mark.parametrize("field", ["wording", "conversationId", "details"])
def test_optional_fields_may_be_left_out_but_not_null(client, field):
    ada_id, _ = ada_ids(client)
    r = post_json(
        client, f"/v1/customers/{ada_id}/consent", {"kind": "details", "status": "asked", field: None}
    )
    assert r.status_code == 422 and field in [e["field"] for e in r.json()["errors"]]


def test_invisible_nul_characters_never_crash_the_api(client, sender):
    # Found by Schemathesis: PostgreSQL rejects NUL (\u0000) in text, which used to cause a 500.
    assert call(client, "/v1/customers", {"q": "\x00"}).status_code == 200
    assert call(client, "/v1/conversations", {"q": "a\x00da"}).status_code == 200
    ada = waiting_id(client, "ada")
    r = post(client, f"/v1/conversations/{ada}/replies", {"text": "hi\u0000 there"}, key="nul-key-00001")
    assert r.status_code == 201 and r.json()["body"] == "hi there" and sender.sent[-1][1] == "hi there"


def test_whole_number_floats_are_integers(client):
    ada_id, conv_id = ada_ids(client)
    r = post_json(
        client,
        f"/v1/customers/{ada_id}/consent",
        {"kind": "details", "status": "asked", "conversationId": float(conv_id)},
    )
    assert r.status_code == 200


def test_requests_for_other_hosts_are_refused(client):
    # DNS rebinding protection: a web page pretending to be another site can't read customer data.
    r = client.get("/v1/customers", headers={"Host": "evil.example"})
    assert r.status_code == 400


def test_database_outage_is_503_with_retry_after(client, monkeypatch):
    from kiakia.adapters.postgres.repository import PostgresStore
    from kiakia.domain.errors import StorageUnavailable

    def down(self, *args, **kwargs):
        raise StorageUnavailable("connection lost")

    monkeypatch.setattr(PostgresStore, "list_customers", down)
    r = call(client, "/v1/customers")
    assert r.status_code == 503 and r.headers["retry-after"] == "5"
