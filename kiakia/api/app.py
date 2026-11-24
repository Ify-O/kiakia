"""Kiakia web API (FastAPI): a second entry point next to the CLI.

The contract is openapi.yaml (docs/adr/0001), not FastAPI's auto-generated schema:
/docs shows openapi.yaml itself, and contract tests check responses against it.
Handlers only translate HTTP <-> use cases; all logic lives in the use cases.

Run locally:
    uv run uvicorn kiakia.api.app:create_app_from_env --factory --reload
"""

from collections.abc import Iterator
from pathlib import Path as FilePath
from typing import Annotated, Any, Literal

import yaml
from fastapi import Depends, FastAPI, Path, Query

from kiakia.bootstrap import Services
from kiakia.bootstrap import app as services_for
from kiakia.config import Settings, load_settings
from kiakia.domain.models import Channel, ConversationFilter, ConversationSort, ConversationStatus

from .errors import install_error_handlers
from .schemas import (
    ConversationDetailOut,
    ConversationPageOut,
    HealthOut,
    InboxStatsOut,
    MessagePageOut,
)

CONTRACT_PATH = FilePath(__file__).resolve().parents[2] / "openapi.yaml"

ConversationId = Annotated[int, Path(alias="conversationId", ge=1, le=2**63 - 1)]
Limit = Annotated[int, Query(ge=1, le=100)]
Cursor = Annotated[str | None, Query(min_length=1, max_length=500)]


def load_contract() -> dict[str, Any]:
    with CONTRACT_PATH.open(encoding="utf-8") as f:
        contract: dict[str, Any] = yaml.safe_load(f)
    return contract


def create_app(settings: Settings) -> FastAPI:
    api = FastAPI(title="Kiakia API", version="1.0.0")
    contract = load_contract()
    api.openapi = lambda: contract  # type: ignore[method-assign]  # /docs shows OUR contract
    install_error_handlers(api)

    def get_services() -> Iterator[Services]:
        # One DB connection per request, always closed afterwards (bootstrap.app).
        # A connection pool is the next step when traffic grows.
        with services_for(settings) as services:
            yield services

    ServicesDep = Annotated[Services, Depends(get_services)]

    @api.get("/v1/conversations", response_model=ConversationPageOut, response_model_by_alias=True)
    def list_conversations(
        services: ServicesDep,
        status: Literal["all", "waiting", "answered"] = "all",
        channel: Channel | None = None,
        q: Annotated[str | None, Query(min_length=1, max_length=100)] = None,
        sort: Literal["recent", "longestWaiting"] = "recent",
        limit: Limit = 20,
        cursor: Cursor = None,
    ) -> ConversationPageOut:
        filters = ConversationFilter(
            status=None if status == "all" else ConversationStatus(status),
            channel=channel,
            search=q,
            sort=ConversationSort(sort),
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

    @api.get("/v1/stats", response_model=InboxStatsOut, response_model_by_alias=True)
    def get_stats(services: ServicesDep) -> InboxStatsOut:
        return InboxStatsOut.from_domain(services.get_inbox_stats().run())

    @api.get("/health", response_model=HealthOut)
    def get_health(services: ServicesDep) -> HealthOut:
        services.check_health()
        return HealthOut()

    return api


def create_app_from_env() -> FastAPI:
    return create_app(load_settings(require_telegram=False))
