"""Telegram Bot API client: only knows how to call Telegram.

Docs: https://core.telegram.org/bots/api#getupdates
- The token is part of the URL, so we never log or print the URL.
- Telegram sends its "slow down" hint in the JSON body
  ({"ok": false, "error_code": 429, "parameters": {"retry_after": 5}}),
  so we give the HTTP helper a reader for that.
"""

from typing import Any

import requests

from kiakia.domain.errors import SourceRejectedRequest
from kiakia.infrastructure.http import ResilientHttpClient, header_retry_after

API_BASE = "https://api.telegram.org"
MAX_LIMIT = 100  # Telegram's maximum for getUpdates


def telegram_retry_after(resp: requests.Response) -> float | None:
    try:
        value = (resp.json().get("parameters") or {}).get("retry_after")
        if isinstance(value, (int, float)) and value >= 0:
            return float(value)
    except ValueError:
        pass
    return header_retry_after(resp)


class TelegramClient:
    def __init__(self, token: str, http: ResilientHttpClient | None = None):
        if not token:
            raise ValueError("Telegram bot token is empty")
        self._token = token
        self._http = http or ResilientHttpClient(retry_after_reader=telegram_retry_after)

    def __repr__(self) -> str:
        return "TelegramClient(token=***)"

    def _call(self, method: str, params: dict[str, Any]) -> Any:
        url = f"{API_BASE}/bot{self._token}/{method}"
        body = self._http.get_json(url, params=params, label=f"telegram {method}")
        if not isinstance(body, dict) or body.get("ok") is not True:
            description = body.get("description") if isinstance(body, dict) else None
            raise SourceRejectedRequest(f"telegram {method}: {description or 'unexpected response'}")
        return body.get("result")

    def get_updates(self, offset: int | None, limit: int = MAX_LIMIT) -> list[dict[str, Any]]:
        """Fetch pending updates. Passing `offset` CONFIRMS (deletes) all updates before it,
        so only pass an offset after the earlier updates are saved."""
        params: dict[str, Any] = {"limit": max(1, min(limit, MAX_LIMIT)), "timeout": 0}
        if offset is not None:
            params["offset"] = offset
        result = self._call("getUpdates", params)
        return result if isinstance(result, list) else []
