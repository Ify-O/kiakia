"""DemoSender: 'delivers' replies instantly and offline, for demo mode only (docs/adr/0008).

Nothing is sent to anyone. It implements the MessageSender port, so the reply use case,
idempotency and the UI behave exactly as with Telegram.
"""

import uuid
from datetime import UTC, datetime

from kiakia.domain.models import SentMessage


class DemoSender:
    def send_text(self, external_chat_id: str, text: str) -> SentMessage:
        return SentMessage(external_message_id=f"demo-{uuid.uuid4().hex[:12]}", sent_at=datetime.now(UTC))
