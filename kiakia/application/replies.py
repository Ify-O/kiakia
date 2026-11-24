"""Use case: reply to a customer from Kiakia, safely (idempotent, docs/adr/0004).

    1. validate the text, find the conversation          (422 / 404)
    2. claim the Idempotency-Key                          (replay / 409)
    3. send through the conversation's channel            (502 / 503)
    4. store the reply, so the customer stops "waiting"
    5. mark the key as completed with the stored reply

Failures:
- Definitely NOT sent (channel refused, rate limit, couldn't connect, unsupported channel):
  the key is released, so retrying with the same key is safe.
- MAYBE sent (DeliveryUnknown, e.g. a timeout after the request left): the key stays
  "in progress", so a retry gets 409 instead of a possible duplicate. The user should
  check the chat; the key expires after 24 h.
- Known gap: if storing (step 4) fails after delivery, the key also stays "in progress"
  (no duplicate possible), but the reply is missing from our database.
"""

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass

from kiakia.domain.errors import (
    ChannelNotSupported,
    DeliveryUnknown,
    IdempotencyConflict,
    InvalidInput,
    NotFound,
)
from kiakia.domain.models import Channel, ClaimOutcome, StoredMessage
from kiakia.domain.ports import ConversationQuery, IdempotencyStore, MessageSender, ReplyStore

SCOPE = "replies"
MAX_LENGTH = 4096  # Telegram's limit, also in openapi.yaml


@dataclass(frozen=True)
class ReplyResult:
    message: StoredMessage
    replayed: bool  # True = an identical earlier request; nothing was sent this time


def request_fingerprint(conversation_id: int, text: str) -> str:
    """Same conversation + same text = same request (used to detect key reuse)."""
    raw = json.dumps({"conversation": conversation_id, "text": text}, sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()


class SendReply:
    def __init__(
        self,
        conversations: ConversationQuery,
        replies: ReplyStore,
        keys: IdempotencyStore,
        senders: Mapping[Channel, MessageSender],
    ):
        self.conversations = conversations
        self.replies = replies
        self.keys = keys
        self.senders = senders

    def run(self, conversation_id: int, text: str, idempotency_key: str) -> ReplyResult:
        text = text.replace("\x00", "").strip()  # NUL can't be stored or sent meaningfully
        if not text:
            raise InvalidInput("text", "must not be empty")
        if len(text) > MAX_LENGTH:
            raise InvalidInput("text", f"must be at most {MAX_LENGTH} characters")

        detail = self.conversations.get_conversation(conversation_id)
        if detail is None:
            raise NotFound(f"Conversation {conversation_id} does not exist.")
        conversation = detail.summary

        claim = self.keys.claim(SCOPE, idempotency_key, request_fingerprint(conversation_id, text))
        if claim.outcome is ClaimOutcome.MISMATCH:
            raise IdempotencyConflict("This Idempotency-Key was already used for a different reply.")
        if claim.outcome is ClaimOutcome.IN_PROGRESS:
            raise IdempotencyConflict("A reply with this Idempotency-Key is still being processed.")
        if claim.outcome is ClaimOutcome.REPLAY:
            stored = self.replies.get_message(claim.message_id) if claim.message_id else None
            if stored is None:
                raise IdempotencyConflict(
                    "The original reply for this Idempotency-Key is no longer available."
                )
            return ReplyResult(message=stored, replayed=True)

        try:
            sender = self.senders.get(conversation.channel)
            if sender is None:
                raise ChannelNotSupported(f"Replying on {conversation.channel.value} is not supported yet.")
            sent = sender.send_text(conversation.external_chat_id, text)
        except DeliveryUnknown:
            raise  # maybe delivered: keep the key locked, so a retry can't send a duplicate
        except Exception:
            self.keys.release(SCOPE, idempotency_key)  # definitely not delivered: a retry is safe
            raise

        stored = self.replies.record_reply(conversation_id, sent.external_message_id, text, sent.sent_at)
        self.keys.complete(SCOPE, idempotency_key, stored.id)
        return ReplyResult(message=stored, replayed=False)
