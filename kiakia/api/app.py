"""Kiakia web API (FastAPI): a second entry point next to the CLI.

The contract is openapi.yaml (docs/adr/0001), not FastAPI's auto-generated schema:
/docs shows openapi.yaml itself, and contract tests check responses against it.
Handlers only translate HTTP <-> use cases; all logic lives in the use cases.

Run locally:
    uv run uvicorn kiakia.api.app:create_app_from_env --factory --reload
"""

from collections.abc import Iterator, Mapping
from datetime import datetime
from pathlib import Path as FilePath
from typing import Annotated, Any, Literal

import yaml
from fastapi import Depends, FastAPI, Header, Path, Query, Response

from kiakia.bootstrap import Services
from kiakia.bootstrap import app as services_for
from kiakia.config import Settings, load_settings
from kiakia.domain.errors import InvalidQuery
from kiakia.domain.models import (
    Category,
    Channel,
    ConversationFilter,
    ConversationSort,
    ConversationStatus,
    Direction,
    FeedFilter,
    Period,
)
from kiakia.domain.ports import MessageSender

from .errors import install_error_handlers
from .schemas import (
    ConversationDetailOut,
    ConversationPageOut,
    ConversationUpdateIn,
    FeedPageOut,
    HealthOut,
    InboxStatsOut,
    MessageOut,
    MessagePageOut,
    ReplyIn,
)

CONTRACT_PATH = FilePath(__file__).resolve().parents[2] / "openapi.yaml"

ConversationId = Annotated[int, Path(alias="conversationId", ge=1, le=2**63 - 1)]
Limit = Annotated[int, Query(ge=1, le=100)]
Cursor = Annotated[str | None, Query(min_length=1, max_length=500)]
From = Annotated[str | None, Query(alias="from", min_length=1, max_length=40)]
To = Annotated[str | None, Query(alias="to", min_length=1, max_length=40)]


def parse_time(field: str, value: str | None) -> datetime | None:
    """Strict RFC 3339 (e.g. 2026-11-25T10:00:00Z). Plain numbers are NOT accepted as timestamps."""
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise InvalidQuery(field, "must be a date-time like 2026-11-25T10:00:00Z") from None
    if parsed.utcoffset() is None:
        raise InvalidQuery(field, "must include a time zone, e.g. 2026-11-25T10:00:00Z")
    return parsed


def period(from_: str | None, to: str | None) -> Period:
    return Period(parse_time("from", from_), parse_time("to", to))


IdempotencyKey = Annotated[
    str, Header(alias="Idempotency-Key", min_length=8, max_length=255, pattern=r"^[A-Za-z0-9_-]+$")
]


def load_contract() -> dict[str, Any]:
    with CONTRACT_PATH.open(encoding="utf-8") as f:
        contract: dict[str, Any] = yaml.safe_load(f)
    return contract


def create_app(settings: Settings, senders: Mapping[Channel, MessageSender] | None = None) -> FastAPI:
    api = FastAPI(title="Kiakia API", version="1.0.0")
    contract = load_contract()
    api.openapi = lambda: contract  # type: ignore[method-assign]  # /docs shows OUR contract
    install_error_handlers(api)

    def get_services() -> Iterator[Services]:
        # One DB connection per request, always closed afterwards (bootstrap.app).
        # A connection pool is the next step when traffic grows.
        with services_for(settings, senders) as services:
            yield services

    ServicesDep = Annotated[Services, Depends(get_services)]

    @api.get("/v1/conversations", response_model=ConversationPageOut, response_model_by_alias=True)
    def list_conversations(
        services: ServicesDep,
        status: Literal["all", "waiting", "answered", "resolved"] = "all",
        channel: Channel | None = None,
        q: Annotated[str | None, Query(min_length=1, max_length=100)] = None,
        sort: Literal["recent", "longestWaiting"] = "recent",
        category: Literal["enquiry", "order", "complaint", "other", "none"] | None = None,
        from_: From = None,
        to: To = None,
        limit: Limit = 20,
        cursor: Cursor = None,
    ) -> ConversationPageOut:
        filters = ConversationFilter(
            status=None if status == "all" else ConversationStatus(status),
            channel=channel,
            search=q,
            sort=ConversationSort(sort),
            category=Category(category) if category and category != "none" else None,
            uncategorized=category == "none",
            period=period(from_, to),
        )
        page = services.list_conversations().run(filters, limit, cursor)
        return ConversationPageOut.from_domain(page, limit)

    @api.get(
        "/v1/conversations/{conversationId}",
        response_model=ConversationDetailOut,
        response_model_by_alias=True,
    )
    def get_conversation(services: ServicesDep, conversation_id: ConversationId) -> ConversationDetailOut:
        return ConversationDetailOut.from_detail(services.get_conversation().run(conversation_id))

    @api.patch(
        "/v1/conversations/{conversationId}",
        response_model=ConversationDetailOut,
        response_model_by_alias=True,
    )
    def update_conversation(
        services: ServicesDep, conversation_id: ConversationId, body: ConversationUpdateIn
    ) -> ConversationDetailOut:
        given: dict[str, Any] = {}
        if "resolved" in body.model_fields_set:
            given["resolved"] = body.resolved
        if "category" in body.model_fields_set:
            given["category"] = body.category
        detail = services.update_conversation().run(conversation_id, **given)
        return ConversationDetailOut.from_detail(detail)

    @api.get("/v1/messages", response_model=FeedPageOut, response_model_by_alias=True)
    def list_all_messages(
        services: ServicesDep,
        channel: Channel | None = None,
        direction: Direction | None = None,
        from_: From = None,
        to: To = None,
        limit: Limit = 20,
        cursor: Cursor = None,
    ) -> FeedPageOut:
        filters = FeedFilter(channel=channel, direction=direction, period=period(from_, to))
        return FeedPageOut.from_domain(services.list_feed().run(filters, limit, cursor), limit)

    @api.get(
        "/v1/conversations/{conversationId}/messages",
        response_model=MessagePageOut,
        response_model_by_alias=True,
    )
    def list_messages(
        services: ServicesDep, conversation_id: ConversationId, limit: Limit = 20, cursor: Cursor = None
    ) -> MessagePageOut:
        page = services.list_messages().run(conversation_id, limit, cursor)
        return MessagePageOut.from_domain(page, limit)

    @api.post(
        "/v1/conversations/{conversationId}/replies",
        status_code=201,
        response_model=MessageOut,
        response_model_by_alias=True,
    )
    def create_reply(
        services: ServicesDep,
        conversation_id: ConversationId,
        body: ReplyIn,
        response: Response,
        idempotency_key: IdempotencyKey,
    ) -> MessageOut:
        result = services.send_reply().run(conversation_id, body.text, idempotency_key)
        response.headers["Location"] = f"/v1/conversations/{conversation_id}/messages"
        response.headers["Idempotent-Replayed"] = "true" if result.replayed else "false"
        return MessageOut.from_domain(result.message)

    @api.get("/v1/stats", response_model=InboxStatsOut, response_model_by_alias=True)
    def get_stats(services: ServicesDep, from_: From = None, to: To = None) -> InboxStatsOut:
        return InboxStatsOut.from_domain(services.get_inbox_stats().run(period(from_, to)))

    @api.get("/health", response_model=HealthOut)
    def get_health(services: ServicesDep) -> HealthOut:
        services.check_health()
        return HealthOut()

    return api


def create_app_from_env() -> FastAPI:
    return create_app(load_settings(require_telegram=False))
