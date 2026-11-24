"""TelegramSender: implements the MessageSender port with Telegram's sendMessage."""

from datetime import UTC, datetime

from kiakia.domain.errors import DeliveryUnknown
from kiakia.domain.models import SentMessage

from .client import TelegramClient


class TelegramSender:
    def __init__(self, client: TelegramClient):
        self._client = client

    def send_text(self, external_chat_id: str, text: str) -> SentMessage:
        result = self._client.send_message(external_chat_id, text)
        message_id, date = result.get("message_id"), result.get("date")
        if type(message_id) is not int or type(date) is not int:
            # Telegram said "ok", so it was sent, but we can't record it properly.
            raise DeliveryUnknown("telegram sendMessage: sent, but the answer was missing message_id/date")
        return SentMessage(external_message_id=str(message_id), sent_at=datetime.fromtimestamp(date, tz=UTC))
