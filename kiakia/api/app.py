"""Kiakia web API (FastAPI): a second entry point next to the CLI.

The contract is openapi.yaml (docs/adr/0001), not FastAPI's auto-generated schema:
/docs shows openapi.yaml itself, and contract tests check responses against it.
Handlers only translate HTTP <-> use cases; all logic lives in the use cases.

Run locally:
    uv run uvicorn kiakia.api.app:create_app_from_env --factory --reload
"""

from collections.abc import AsyncIterator, Iterator, Mapping
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path as FilePath
from typing import Annotated, Any, Literal

import yaml
from fastapi import Depends, FastAPI, Header, Path, Query, Response
from starlette.middleware.trustedhost import TrustedHostMiddleware

from kiakia.bootstrap import Services, api_pool, default_senders, pooled_app
from kiakia.config import Settings, load_settings
from kiakia.domain.errors import InvalidInput, InvalidQuery
from kiakia.domain.models import (
    Category,
    Channel,
    ConsentStatus,
    ConversationFilter,
    ConversationSort,
    ConversationStatus,
    CustomerDetailsInput,
    CustomerFilter,
    Direction,
    ExportFormat,
    ExportLayout,
    FeedFilter,
    Period,
)
from kiakia.domain.ports import MessageSender

from .errors import install_error_handlers
from .schemas import (
    ConsentRecordIn,
    ConversationDetailOut,
    ConversationPageOut,
    ConversationUpdateIn,
    CustomerDetailOut,
    CustomerPageOut,
    FeedPageOut,
    HealthOut,
    InboxStatsOut,
    MessageOut,
    MessagePageOut,
    ReplyIn,
)
from .security import SecurityHeaders

CONTRACT_PATH = FilePath(__file__).resolve().parents[2] / "openapi.yaml"

ConversationId = Annotated[int, Path(alias="conversationId", ge=1, le=2**63 - 1)]
CustomerId = Annotated[int, Path(alias="customerId", ge=1, le=2**63 - 1)]
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
    contract = load_contract()
    # A few database connections kept open and shared by all requests (review finding M4b):
    # opening a new one per request cost ~55 ms each time. Closed when the API stops.
    pool = api_pool(settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            pool.close()

    api = FastAPI(title="Kiakia API", version=str(contract["info"]["version"]), lifespan=lifespan)
    # The API has no logins yet and holds customer data, so only answer requests addressed to this
    # computer. That blocks "DNS rebinding", where a web page tricks your browser into calling
    # localhost (review finding M6). "testserver" is the name used by the automated tests.
    api.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "testserver"])
    # Added last = runs first, so even a refused Host (400) gets the headers.
    api.add_middleware(SecurityHeaders)
    # Build the senders ONCE, so the HTTP client and its rate limiting are shared by all
    # requests instead of being created again for every reply (review finding M5).
    senders = senders if senders is not None else default_senders(settings)
    api.openapi = lambda: contract  # type: ignore[method-assign]  # /docs shows OUR contract
    install_error_handlers(api)

    def get_services() -> Iterator[Services]:
        # One pooled DB connection per request, always given back afterwards (bootstrap.pooled_app).
        with pooled_app(pool, settings, senders) as services:
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

    # ---------- Customers + consent (ADR 0007) ----------

    @api.get("/v1/customers", response_model=CustomerPageOut, response_model_by_alias=True)
    def list_customers(
        services: ServicesDep,
        q: Annotated[str | None, Query(min_length=1, max_length=100)] = None,
        details_consent: Annotated[ConsentStatus | None, Query(alias="detailsConsent")] = None,
        marketing_consent: Annotated[ConsentStatus | None, Query(alias="marketingConsent")] = None,
        channel: Channel | None = None,
        limit: Limit = 20,
        cursor: Cursor = None,
    ) -> CustomerPageOut:
        filters = CustomerFilter(
            search=q, details_consent=details_consent, marketing_consent=marketing_consent, channel=channel
        )
        return CustomerPageOut.from_domain(services.list_customers().run(filters, limit, cursor), limit)

    @api.get("/v1/customers/{customerId}", response_model=CustomerDetailOut, response_model_by_alias=True)
    def get_customer(services: ServicesDep, customer_id: CustomerId) -> CustomerDetailOut:
        return CustomerDetailOut.from_detail(services.get_customer().run(customer_id))

    @api.delete("/v1/customers/{customerId}", status_code=204)
    def forget_customer(services: ServicesDep, customer_id: CustomerId) -> Response:
        services.forget_customer().run(customer_id)
        return Response(status_code=204)

    @api.post(
        "/v1/customers/{customerId}/consent", response_model=CustomerDetailOut, response_model_by_alias=True
    )
    def record_consent(
        services: ServicesDep, customer_id: CustomerId, body: ConsentRecordIn
    ) -> CustomerDetailOut:
        details = None
        if body.details is not None:
            given = body.details.model_fields_set
            if not given:
                raise InvalidInput("details", "share at least one of: name, phone, email")
            details = CustomerDetailsInput(
                name=body.details.name.strip() if body.details.name else None,
                phone=body.details.phone,
                email=body.details.email,
            )
        detail = services.record_consent().run(
            customer_id,
            body.kind,
            ConsentStatus(body.status),
            conversation_id=body.conversation_id,
            wording=body.wording,
            details=details,
        )
        return CustomerDetailOut.from_detail(detail)

    @api.get("/v1/exports/customers", response_class=Response)
    def export_customers(
        services: ServicesDep,
        format_: Annotated[ExportFormat, Query(alias="format")] = ExportFormat.CSV,
        layout: ExportLayout = ExportLayout.STANDARD,
    ) -> Response:
        file = services.export_customers().run(format_, layout)
        return Response(
            file.content,
            media_type=file.media_type,
            headers={
                "Content-Disposition": f'attachment; filename="{file.filename}"',
                "Cache-Control": "no-store",  # personal data: never keep a copy in a cache
            },
        )

    @api.get("/v1/stats", response_model=InboxStatsOut, response_model_by_alias=True)
    def get_stats(services: ServicesDep, from_: From = None, to: To = None) -> InboxStatsOut:
        return InboxStatsOut.from_domain(services.get_inbox_stats().run(period(from_, to)))

    @api.get("/health", response_model=HealthOut, response_model_by_alias=True)
    def get_health(services: ServicesDep) -> HealthOut:
        services.check_health()
        return HealthOut(demo_mode=services.settings.demo_mode)

    return api


def create_app_from_env() -> FastAPI:
    return create_app(load_settings(require_telegram=False))
