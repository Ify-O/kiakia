"""Unit tests: opaque cursors (ADR 0003) and the browsing use cases, with a fake query port."""

from datetime import UTC, datetime, timedelta

import pytest

from kiakia.application.conversations import GetConversation, ListConversations, ListMessages
from kiakia.application.pagination import decode_cursor, encode_cursor
from kiakia.domain.errors import InvalidCursor, NotFound
from kiakia.domain.models import (
    Channel,
    ConversationDetail,
    ConversationFilter,
    ConversationSort,
    ConversationStatus,
    ConversationSummary,
    Direction,
    MessageKind,
    StoredMessage,
)

T0 = datetime(2026, 11, 24, 9, 0, tzinfo=UTC)


# ---------- cursors ----------


def test_cursor_round_trip():
    cursor = encode_cursor("conversations:recent", (T0, 42))
    assert "=" not in cursor  # url-safe, no padding
    assert decode_cursor(cursor, "conversations:recent") == (T0, 42)


@pytest.mark.parametrize("bad", ["x", "!!!", "e30", encode_cursor("messages:1", (T0, 1))[:-3], "a" * 50])
def test_garbage_cursors_are_rejected(bad):
    with pytest.raises(InvalidCursor):
        decode_cursor(bad, "messages:1")


def test_cursor_from_another_list_is_rejected():
    cursor = encode_cursor("conversations:recent", (T0, 1))
    with pytest.raises(InvalidCursor, match="different list"):
        decode_cursor(cursor, "conversations:longestWaiting")


# ---------- use cases with a fake query port ----------


def summary(i, waiting=True):
    return ConversationSummary(
        id=i,
        channel=Channel.TELEGRAM,
        external_chat_id=str(i),
        contact_id=i,
        contact_name=f"Customer {i}",
        contact_username=None,
        status=ConversationStatus.WAITING if waiting else ConversationStatus.ANSWERED,
        waiting_since=T0 + timedelta(minutes=i) if waiting else None,
        last_message_at=T0 - timedelta(minutes=i),
        last_reply_at=None,
        last_message_preview="hi",
        unanswered_count=1 if waiting else 0,
    )


class FakeQuery:
    def __init__(self, conversations=(), messages=()):
        self.conversations = list(conversations)
        self.messages = list(messages)
        self.calls = []

    def list_conversations(self, filters, limit, after):
        self.calls.append((filters, limit, after))
        start = (
            0 if after is None else next(i for i, c in enumerate(self.conversations) if c.id == after[1]) + 1
        )
        return self.conversations[start : start + limit]

    def get_conversation(self, conversation_id):
        for c in self.conversations:
            if c.id == conversation_id:
                return ConversationDetail(summary=c, created_at=T0, message_count=len(self.messages))
        return None

    def list_messages(self, conversation_id, limit, before):
        start = 0 if before is None else next(i for i, m in enumerate(self.messages) if m.id == before[1]) + 1
        return self.messages[start : start + limit]


def test_pages_through_all_conversations_without_gaps_or_duplicates():
    query = FakeQuery([summary(i) for i in range(1, 8)])
    use_case, filters, seen, cursor = ListConversations(query), ConversationFilter(), [], None
    while True:
        page = use_case.run(filters, limit=3, cursor=cursor)
        seen += [c.id for c in page.items]
        if page.next_cursor is None:
            break
        cursor = page.next_cursor
    assert seen == [1, 2, 3, 4, 5, 6, 7]
    assert query.calls[0][1] == 4  # asks for limit + 1 to know if there's a next page


def test_last_page_has_no_cursor():
    page = ListConversations(FakeQuery([summary(1)])).run(ConversationFilter(), limit=5)
    assert page.next_cursor is None and len(page.items) == 1


def test_longest_waiting_cursor_uses_waiting_since():
    query = FakeQuery([summary(1), summary(2)])
    page = ListConversations(query).run(ConversationFilter(sort=ConversationSort.LONGEST_WAITING), limit=1)
    assert decode_cursor(page.next_cursor, "conversations:longestWaiting") == (T0 + timedelta(minutes=1), 1)


def test_get_missing_conversation_is_not_found():
    with pytest.raises(NotFound, match="999"):
        GetConversation(FakeQuery()).run(999)


def test_messages_of_missing_conversation_is_not_found():
    with pytest.raises(NotFound):
        ListMessages(FakeQuery()).run(999, limit=10)


def test_messages_paginate_newest_first():
    msgs = [
        StoredMessage(
            i, 1, Direction.IN, MessageKind.TEXT, f"m{i}", False, T0 - timedelta(minutes=i), Channel.TELEGRAM
        )
        for i in range(1, 5)
    ]
    use_case = ListMessages(FakeQuery([summary(1)], msgs))
    first = use_case.run(1, limit=3)
    second = use_case.run(1, limit=3, cursor=first.next_cursor)
    assert [m.id for m in first.items] == [1, 2, 3]
    assert [m.id for m in second.items] == [4] and second.next_cursor is None
