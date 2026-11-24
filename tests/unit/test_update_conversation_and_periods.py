"""Unit tests: resolve/reopen/categorise (ADR 0006) and time periods, with fake ports."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from kiakia.application.conversations import UpdateConversation
from kiakia.domain.errors import InvalidInput, InvalidQuery, NotFound
from kiakia.domain.models import (
    Category,
    Channel,
    ConversationDetail,
    ConversationStatus,
    ConversationSummary,
    Period,
)

T0 = datetime(2026, 11, 25, 9, 0, tzinfo=UTC)
LATER = T0 + timedelta(hours=1)


def detail(status=ConversationStatus.WAITING, category=None, resolved_at=None):
    s = ConversationSummary(
        id=1,
        channel=Channel.TELEGRAM,
        external_chat_id="9001",
        contact_id=1,
        contact_name="Ada",
        contact_username=None,
        status=status,
        waiting_since=T0,
        last_message_at=T0,
        last_reply_at=None,
        last_message_preview="hi",
        unanswered_count=1,
        category=category,
        resolved_at=resolved_at,
    )
    return ConversationDetail(summary=s, created_at=T0, message_count=1)


class FakeStore:
    """Fake ConversationQuery + ConversationUpdater that applies changes like the database would."""

    def __init__(self, d=None):
        self.d = d or detail()
        self.updates = []

    def get_conversation(self, conversation_id):
        return self.d if conversation_id == 1 else None

    def update_conversation(self, conversation_id, changes):
        self.updates.append(changes)
        s = self.d.summary
        if changes.category_set:
            s = replace(s, category=changes.category)
        if changes.resolved_set:
            s = replace(
                s,
                resolved_at=changes.resolved_at,
                status=ConversationStatus.RESOLVED if changes.resolved_at else ConversationStatus.WAITING,
            )
        self.d = replace(self.d, summary=s)


def use_case(store):
    return UpdateConversation(store, store, clock=lambda: LATER)


def test_needs_at_least_one_field():
    with pytest.raises(InvalidInput, match="at least one"):
        use_case(FakeStore()).run(1)


def test_unknown_conversation_is_not_found():
    with pytest.raises(NotFound):
        use_case(FakeStore()).run(999, resolved=True)


def test_resolve_stamps_the_time():
    store = FakeStore()
    result = use_case(store).run(1, resolved=True)
    assert result.summary.status is ConversationStatus.RESOLVED
    assert result.summary.resolved_at == LATER


def test_resolving_again_changes_nothing():
    # PATCH sets values, so repeating it is harmless (naturally idempotent).
    store = FakeStore(detail(ConversationStatus.RESOLVED, resolved_at=T0))
    result = use_case(store).run(1, resolved=True)
    assert result.summary.resolved_at == T0  # original time kept
    assert store.updates == []  # nothing to write


def test_reopen_clears_resolved():
    store = FakeStore(detail(ConversationStatus.RESOLVED, resolved_at=T0))
    result = use_case(store).run(1, resolved=False)
    assert result.summary.resolved_at is None


def test_set_and_clear_category():
    store = FakeStore()
    assert use_case(store).run(1, category=Category.ORDER).summary.category is Category.ORDER
    assert use_case(store).run(1, category=None).summary.category is None  # null removes the label


def test_category_and_resolve_together():
    store = FakeStore()
    result = use_case(store).run(1, resolved=True, category=Category.COMPLAINT)
    assert result.summary.category is Category.COMPLAINT and result.summary.resolved_at == LATER
    assert len(store.updates) == 1  # one write for both


# ---------- periods ----------


def test_open_ended_periods_are_fine():
    Period()
    Period(start=T0)
    Period(end=T0)


def test_from_must_be_before_to():
    with pytest.raises(InvalidQuery, match="before"):
        Period(LATER, T0)
    with pytest.raises(InvalidQuery):
        Period(T0, T0)


def test_period_needs_a_time_zone():
    with pytest.raises(InvalidQuery, match="time zone"):
        Period(datetime(2026, 11, 25, 9, 0))
