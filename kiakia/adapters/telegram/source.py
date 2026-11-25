"""TelegramSource: implements the MessageSource port by combining client + parser."""

from kiakia.domain.models import FetchResult

from .client import TelegramClient
from .parser import parse_updates


class TelegramSource:
    name = "telegram"

    def __init__(self, client: TelegramClient, wait_seconds: int = 0):
        self._client = client
        self._wait = wait_seconds  # > 0 = long polling (used by `kiakia watch`)

    def fetch(self, cursor: str | None, limit: int) -> FetchResult:
        offset = int(cursor) if cursor else None
        updates = self._client.get_updates(offset=offset, limit=limit, wait_seconds=self._wait)
        return parse_updates(updates)
