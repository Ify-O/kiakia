"""Unit tests for SendReply (idempotency, validation, failure handling) with fake ports."""

from datetime import UTC, datetime

import pytest

from kiakia.application.replies import SCOPE, SendReply, request_fingerprint
from kiakia.domain.errors import (
    ChannelNotSupported,
    DeliveryUnknown,
    IdempotencyConflict,
    InvalidInput,
    NotFound,
    SourceRejectedRequest,
)
from kiakia.domain.models import (
    Channel,
    ClaimOutcome,
    ConversationDetail,
    ConversationStatus,
    ConversationSummary,
    Direction,
    IdempotencyClaim,
    MessageKind,
    SentMessage,
    StoredMessage,
)

T0 = datetime(2026, 11, 24, 9, 0, tzinfo=UTC)
KEY = "key-0001-abcdef"


def conversation(channel=Channel.TELEGRAM):
    summary = ConversationSummary(
        id=1,
        channel=channel,
        external_chat_id="9001",
        contact_id=1,
        contact_name="Ada",
        contact_username=None,
        status=ConversationStatus.WAITING,
        waiting_since=T0,
        last_message_at=T0,
        last_reply_at=None,
        last_message_preview="hi",
        unanswered_count=1,
    )
    return ConversationDetail(summary=summary, created_at=T0, message_count=1)


class FakeStore:
    """Fake ConversationQuery + ReplyStore + IdempotencyStore (in memory)."""

    def __init__(self, detail=None):
        self.detail = detail if detail is not None else conversation()
        self.keys, self.messages, self.released = {}, {}, []

    def get_conversation(self, conversation_id):
        return self.detail if conversation_id == 1 else None

    def record_reply(self, conversation_id, external_message_id, text, sent_at):
        m = StoredMessage(
            len(self.messages) + 1, conversation_id, Direction.OUT, MessageKind.TEXT, text, False, sent_at
        )
        self.messages[m.id] = m
        return m

    def get_message(self, message_id):
        return self.messages.get(message_id)

    def claim(self, scope, key, request_hash):
        if (scope, key) not in self.keys:
            self.keys[(scope, key)] = {"hash": request_hash, "status": "in_progress", "message_id": None}
            return IdempotencyClaim(ClaimOutcome.NEW)
        row = self.keys[(scope, key)]
        if row["hash"] != request_hash:
            return IdempotencyClaim(ClaimOutcome.MISMATCH)
        if row["status"] == "in_progress":
            return IdempotencyClaim(ClaimOutcome.IN_PROGRESS)
        return IdempotencyClaim(ClaimOutcome.REPLAY, row["message_id"])

    def complete(self, scope, key, message_id):
        self.keys[(scope, key)].update(status="completed", message_id=message_id)

    def release(self, scope, key):
        self.released.append(key)
        self.keys.pop((scope, key), None)


class FakeSender:
    def __init__(self, error=None):
        self.sent, self.error = [], error

    def send_text(self, chat_id, text):
        if self.error:
            raise self.error
        self.sent.append((chat_id, text))
        return SentMessage(external_message_id=str(100 + len(self.sent)), sent_at=T0)


def make(store=None, sender=None):
    store = store or FakeStore()
    sender = sender or FakeSender()
    return SendReply(store, store, store, {Channel.TELEGRAM: sender}), store, sender


def test_sends_stores_and_trims():
    use_case, store, sender = make()
    result = use_case.run(1, "  Hello Ada!  ", KEY)
    assert sender.sent == [("9001", "Hello Ada!")]
    assert result.replayed is False and result.message.body == "Hello Ada!"
    assert store.keys[(SCOPE, KEY)]["status"] == "completed"


def test_same_key_same_text_is_replayed_without_sending_again():
    use_case, _, sender = make()
    first = use_case.run(1, "Hello", KEY)
    again = use_case.run(1, "Hello", KEY)
    assert len(sender.sent) == 1  # THE point of idempotency: the customer gets it once
    assert again.replayed is True and again.message == first.message


def test_same_key_different_text_is_a_conflict():
    use_case, _, sender = make()
    use_case.run(1, "Hello", KEY)
    with pytest.raises(IdempotencyConflict, match="different reply"):
        use_case.run(1, "Goodbye", KEY)
    assert len(sender.sent) == 1


def test_key_still_in_progress_is_a_conflict():
    use_case, store, _ = make()
    store.claim(SCOPE, KEY, request_fingerprint(1, "Hello"))  # someone else is mid-request
    with pytest.raises(IdempotencyConflict, match="still being processed"):
        use_case.run(1, "Hello", KEY)


@pytest.mark.parametrize("text", ["", "   ", "\n\t"])
def test_empty_text_is_invalid_and_nothing_is_claimed(text):
    use_case, store, sender = make()
    with pytest.raises(InvalidInput) as exc:
        use_case.run(1, text, KEY)
    assert exc.value.field == "text" and store.keys == {} and sender.sent == []


def test_too_long_text_is_invalid():
    use_case, _, _ = make()
    with pytest.raises(InvalidInput, match="4096"):
        use_case.run(1, "x" * 4097, KEY)


def test_unknown_conversation_is_not_found():
    use_case, _, _ = make()
    with pytest.raises(NotFound):
        use_case.run(999, "Hello", KEY)


def test_refused_send_releases_the_key_so_a_retry_works():
    store = FakeStore()
    use_case, _, _ = make(store, FakeSender(SourceRejectedRequest("telegram: bot was blocked")))
    with pytest.raises(SourceRejectedRequest):
        use_case.run(1, "Hello", KEY)
    assert store.released == [KEY] and (SCOPE, KEY) not in store.keys
    retry, _, sender = make(store)
    assert retry.run(1, "Hello", KEY).replayed is False and len(sender.sent) == 1


def test_maybe_delivered_keeps_the_key_locked():
    store = FakeStore()
    use_case, _, _ = make(store, FakeSender(DeliveryUnknown("telegram: no answer in time")))
    with pytest.raises(DeliveryUnknown):
        use_case.run(1, "Hello", KEY)
    assert store.released == []  # NOT released: a retry must not risk a duplicate
    retry, _, sender = make(store)
    with pytest.raises(IdempotencyConflict):
        retry.run(1, "Hello", KEY)
    assert sender.sent == []


def test_channel_without_sender_is_not_supported_and_releases_the_key():
    store = FakeStore(conversation(Channel.WHATSAPP))
    use_case, _, _ = make(store)
    with pytest.raises(ChannelNotSupported, match="whatsapp"):
        use_case.run(1, "Hello", KEY)
    assert store.released == [KEY]
