"""Unit tests: consent rules (ADR 0007) and STOP detection, with fake ports."""

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from kiakia.application.customers import RecordConsent, is_opt_out
from kiakia.domain.errors import InvalidInput, NotFound
from kiakia.domain.models import (
    Channel,
    ConsentKind,
    ConsentStatus,
    Customer,
    CustomerDetail,
    CustomerDetailsInput,
)

T0 = datetime(2026, 11, 25, 9, 0, tzinfo=UTC)


def customer(details=ConsentStatus.UNKNOWN, marketing=ConsentStatus.UNKNOWN):
    c = Customer(
        id=1,
        display_name="Ada",
        full_name=None,
        phone=None,
        email=None,
        details_consent=details,
        marketing_consent=marketing,
        first_channel=Channel.TELEGRAM,
        channels=[],
        first_contact_at=T0,
        last_contact_at=T0,
        conversation_count=1,
        order_count=0,
    )
    return CustomerDetail(customer=c, conversations=[], history=[])


class FakeStore:
    def __init__(self, detail=None):
        self.detail = detail or customer()
        self.changes = []

    def get_customer(self, customer_id):
        return self.detail if customer_id == 1 else None

    def conversation_evidence(self, customer_id, conversation_id):
        return (Channel.TELEGRAM, 77) if (customer_id, conversation_id) == (1, 10) else None

    def record_consent(self, change):
        self.changes.append(change)
        c = self.detail.customer
        if change.kind is ConsentKind.DETAILS:
            c = replace(c, details_consent=change.new_current)
        else:
            c = replace(c, marketing_consent=change.new_current)
        self.detail = replace(self.detail, customer=c)


def test_asked_then_granted_with_evidence():
    store = FakeStore()
    RecordConsent(store).run(
        1, ConsentKind.DETAILS, ConsentStatus.ASKED, conversation_id=10, wording="Can we save…?"
    )
    RecordConsent(store).run(
        1,
        ConsentKind.DETAILS,
        ConsentStatus.GRANTED,
        conversation_id=10,
        details=CustomerDetailsInput(phone="+2348031234567"),
    )
    asked, granted = store.changes
    assert (asked.status, asked.wording, asked.channel) == (
        ConsentStatus.ASKED,
        "Can we save…?",
        Channel.TELEGRAM,
    )
    assert granted.evidence_message_id == 77 and granted.details.phone == "+2348031234567"
    assert store.detail.customer.details_consent is ConsentStatus.GRANTED


@pytest.mark.parametrize(
    ("kind", "status"),
    [
        (ConsentKind.DETAILS, ConsentStatus.DECLINED),
        (ConsentKind.MARKETING, ConsentStatus.GRANTED),
        (ConsentKind.DETAILS, ConsentStatus.ASKED),
    ],
)
def test_details_only_with_a_details_yes(kind, status):
    with pytest.raises(InvalidInput, match="details"):
        RecordConsent(FakeStore()).run(1, kind, status, details=CustomerDetailsInput(name="Ada"))


def test_asking_again_never_undoes_a_yes():
    store = FakeStore(customer(details=ConsentStatus.GRANTED))
    RecordConsent(store).run(1, ConsentKind.DETAILS, ConsentStatus.ASKED)
    assert store.changes[0].new_current is ConsentStatus.GRANTED  # still granted: details kept


def test_recording_the_same_answer_again_changes_nothing():
    store = FakeStore(customer(marketing=ConsentStatus.DECLINED))
    RecordConsent(store).run(1, ConsentKind.MARKETING, ConsentStatus.DECLINED)
    assert store.changes == []


def test_conversation_must_belong_to_the_customer():
    with pytest.raises(NotFound, match="does not belong"):
        RecordConsent(FakeStore()).run(1, ConsentKind.DETAILS, ConsentStatus.ASKED, conversation_id=999)


def test_unknown_customer_and_unknown_status():
    with pytest.raises(NotFound):
        RecordConsent(FakeStore()).run(2, ConsentKind.DETAILS, ConsentStatus.ASKED)
    with pytest.raises(InvalidInput):
        RecordConsent(FakeStore()).run(1, ConsentKind.DETAILS, ConsentStatus.UNKNOWN)


@pytest.mark.parametrize("text", ["STOP", "stop", " Stop! ", "unsubscribe", "Opt out"])
def test_stop_words(text):
    assert is_opt_out(text)


@pytest.mark.parametrize("text", [None, "", "please don't stop sending", "stop by the shop?", "hello"])
def test_normal_messages_are_not_stop(text):
    assert not is_opt_out(text)


def test_offers_need_saved_details_first():
    store = FakeStore()
    with pytest.raises(InvalidInput):
        RecordConsent(store).run(1, ConsentKind.MARKETING, ConsentStatus.GRANTED)
    assert store.changes == []
    store = FakeStore(customer(details=ConsentStatus.GRANTED))
    RecordConsent(store).run(1, ConsentKind.MARKETING, ConsentStatus.GRANTED)
    assert store.detail.customer.marketing_consent is ConsentStatus.GRANTED


def test_every_change_says_what_it_expects_the_current_status_to_be():
    # The store refuses (ConcurrentChange) if someone changed it meanwhile: no lost updates.
    store = FakeStore(customer(details=ConsentStatus.ASKED))
    RecordConsent(store).run(1, ConsentKind.DETAILS, ConsentStatus.DECLINED)
    assert store.changes[0].expected_current is ConsentStatus.ASKED
