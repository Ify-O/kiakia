"""Use cases for browsing the inbox: list conversations, get one, list its messages.

They depend only on ports. Pagination: ask the store for limit + 1 items; if we get
the extra one, there is a next page, and the cursor points after the last item shown.
"""

from collections.abc import Callable
from datetime import UTC, datetime

from kiakia.domain.errors import InvalidInput, NotFound
from kiakia.domain.models import (
    Category,
    ConversationChanges,
    ConversationDetail,
    ConversationFilter,
    ConversationSort,
    ConversationStatus,
    ConversationSummary,
    FeedFilter,
    FeedItem,
    Page,
    StoredMessage,
)
from kiakia.domain.ports import ConversationQuery, ConversationUpdater, Keyset

from .pagination import decode_cursor, encode_cursor


def _conversation_key(c: ConversationSummary, sort: ConversationSort) -> Keyset:
    if sort is ConversationSort.LONGEST_WAITING:
        if c.waiting_since is None:  # only waiting conversations are in this list
            raise ValueError("longestWaiting list returned a conversation that isn't waiting")
        return c.waiting_since, c.id
    return c.last_message_at, c.id


class ListConversations:
    def __init__(self, query: ConversationQuery):
        self.query = query

    def run(
        self, filters: ConversationFilter, limit: int, cursor: str | None = None
    ) -> Page[ConversationSummary]:
        scope = f"conversations:{filters.sort.value}"
        after = decode_cursor(cursor, scope) if cursor else None
        items = self.query.list_conversations(filters, limit + 1, after)
        if len(items) <= limit:
            return Page(items=items, next_cursor=None)
        shown = items[:limit]
        return Page(items=shown, next_cursor=encode_cursor(scope, _conversation_key(shown[-1], filters.sort)))


class GetConversation:
    def __init__(self, query: ConversationQuery):
        self.query = query

    def run(self, conversation_id: int) -> ConversationDetail:
        detail = self.query.get_conversation(conversation_id)
        if detail is None:
            raise NotFound(f"Conversation {conversation_id} does not exist.")
        return detail


class ListMessages:
    def __init__(self, query: ConversationQuery):
        self.query = query

    def run(self, conversation_id: int, limit: int, cursor: str | None = None) -> Page[StoredMessage]:
        if self.query.get_conversation(conversation_id) is None:
            raise NotFound(f"Conversation {conversation_id} does not exist.")
        scope = f"messages:{conversation_id}"
        before = decode_cursor(cursor, scope) if cursor else None
        items = self.query.list_messages(conversation_id, limit + 1, before)
        if len(items) <= limit:
            return Page(items=items, next_cursor=None)
        shown = items[:limit]
        last = shown[-1]
        return Page(items=shown, next_cursor=encode_cursor(scope, (last.sent_at, last.id)))


class ListFeed:
    """All messages across conversations, newest first (the messages-by-platform view)."""

    def __init__(self, query: ConversationQuery):
        self.query = query

    def run(self, filters: FeedFilter, limit: int, cursor: str | None = None) -> Page[FeedItem]:
        scope = f"feed:{filters.channel or 'all'}:{filters.direction or 'all'}"
        before = decode_cursor(cursor, scope) if cursor else None
        items = self.query.list_feed(filters, limit + 1, before)
        if len(items) <= limit:
            return Page(items=items, next_cursor=None)
        shown = items[:limit]
        last = shown[-1].message
        return Page(items=shown, next_cursor=encode_cursor(scope, (last.sent_at, last.id)))


_UNSET = object()


class UpdateConversation:
    """Resolve / reopen / categorise (ADR 0006). Values are SET, so repeating is harmless."""

    def __init__(
        self,
        query: ConversationQuery,
        updater: ConversationUpdater,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ):
        self.query = query
        self.updater = updater
        self.clock = clock

    def run(
        self,
        conversation_id: int,
        *,
        resolved: bool | None = None,
        category: Category | object | None = _UNSET,
    ) -> ConversationDetail:
        if resolved is None and category is _UNSET:
            raise InvalidInput("body", "give at least one of: resolved, category")
        current = self.query.get_conversation(conversation_id)
        if current is None:
            raise NotFound(f"Conversation {conversation_id} does not exist.")

        changes = ConversationChanges()
        if category is not _UNSET:
            changes = ConversationChanges(
                category=category if isinstance(category, Category) else None, category_set=True
            )
        if resolved is True and current.summary.status is not ConversationStatus.RESOLVED:
            # Keep the original time if it's already resolved, so repeating the request changes nothing.
            changes = ConversationChanges(changes.category, changes.category_set, self.clock(), True)
        elif resolved is False:
            changes = ConversationChanges(changes.category, changes.category_set, None, True)

        if changes.category_set or changes.resolved_set:
            self.updater.update_conversation(conversation_id, changes)
        updated = self.query.get_conversation(conversation_id)
        if updated is None:  # deleted in between
            raise NotFound(f"Conversation {conversation_id} does not exist.")
        return updated
