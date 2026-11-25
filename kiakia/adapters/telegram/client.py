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


def telegram_error_detail(resp: requests.Response) -> str | None:
    """Telegram explains refusals in the body, e.g. "Forbidden: bot was blocked by the user"."""
    try:
        body = resp.json()
    except ValueError:  # not JSON at all (e.g. an HTML error page from a proxy)
        return None
    description = body.get("description") if isinstance(body, dict) else None
    return str(description) if description else None


class TelegramClient:
    def __init__(self, token: str, http: ResilientHttpClient | None = None):
        if not token:
            raise ValueError("Telegram bot token is empty")
        self._token = token
        self._http = http or ResilientHttpClient(
            retry_after_reader=telegram_retry_after, error_detail_reader=telegram_error_detail
        )

    def __repr__(self) -> str:
        return "TelegramClient(token=***)"

    def _url(self, method: str) -> str:
        return f"{API_BASE}/bot{self._token}/{method}"

    def _result(self, method: str, body: Any) -> Any:
        if not isinstance(body, dict) or body.get("ok") is not True:
            description = body.get("description") if isinstance(body, dict) else None
            raise SourceRejectedRequest(f"telegram {method}: {description or 'unexpected response'}")
        return body.get("result")

    def _call(self, method: str, params: dict[str, Any]) -> Any:
        return self._result(
            method, self._http.get_json(self._url(method), params=params, label=f"telegram {method}")
        )

    def send_message(self, chat_id: str, text: str) -> dict[str, Any]:
        """Send a text message. Never retried when delivery is uncertain (see post_json_unsafe)."""
        body = self._http.post_json_unsafe(
            self._url("sendMessage"),
            json_body={"chat_id": chat_id, "text": text},
            label="telegram sendMessage",
        )
        result = self._result("sendMessage", body)
        return result if isinstance(result, dict) else {}

    def get_updates(
        self, offset: int | None, limit: int = MAX_LIMIT, wait_seconds: int = 0
    ) -> list[dict[str, Any]]:
        """Fetch pending updates. Passing `offset` CONFIRMS (deletes) all updates before it,
        so only pass an offset after the earlier updates are saved.

        `wait_seconds` > 0 = long polling: Telegram holds the request open until a message
        arrives (or the time is up), so new messages show up almost instantly. It must stay
        below the HTTP read timeout (30 s)."""
        params: dict[str, Any] = {
            "limit": max(1, min(limit, MAX_LIMIT)),
            "timeout": max(0, min(wait_seconds, 25)),
        }
        if offset is not None:
            params["offset"] = offset
        result = self._call("getUpdates", params)
        return result if isinstance(result, list) else []
