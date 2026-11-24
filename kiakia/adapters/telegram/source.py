"""TelegramSource: implements the MessageSource port by combining client + parser."""
from __future__ import annotations

from typing import Optional

from kiakia.domain.models import FetchResult

from .client import TelegramClient
from .parser import parse_updates


class TelegramSource:
    name = "telegram"

    def __init__(self, client: TelegramClient):
        self._client = client

    def fetch(self, cursor: Optional[str], limit: int) -> FetchResult:
        offset = int(cursor) if cursor else None
        updates = self._client.get_updates(offset=offset, limit=limit)
        return parse_updates(updates)
