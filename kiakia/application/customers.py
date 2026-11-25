"""Use cases for the light customer list with recorded consent (docs/adr/0007).

The rules that make consent trustworthy live HERE, not in the UI or the database adapter:
- personal details (name/phone/email) only with a details "granted";
- the conversation where it happened must belong to the customer (its latest customer
  message is kept as evidence);
- asking again never downgrades a "granted";
- recording the current status again is a no-op (safe to repeat).
"""

from kiakia.domain.errors import InvalidInput, NotFound
from kiakia.domain.models import (
    Channel,
    ConsentChange,
    ConsentKind,
    ConsentStatus,
    Customer,
    CustomerDetail,
    CustomerDetailsInput,
    CustomerFilter,
    Message,
    Page,
)
from kiakia.domain.ports import CustomerStore

from .pagination import decode_cursor, encode_cursor

# Not "cancel": customers write "cancel" about an ORDER, and silently dropping them from offers
# would be a surprise (review finding L1).
OPT_OUT_WORDS = {"stop", "unsubscribe", "stop all", "opt out", "optout"}


def is_opt_out(text: str | None) -> bool:
    """A message that is just STOP (or similar) means "no more offers"."""
    if not text:
        return False
    return text.strip().strip(".!").strip().lower() in OPT_OUT_WORDS


def is_opt_out_message(m: Message) -> bool:
    return m.direction.value == "in" and is_opt_out(m.body)


class ListCustomers:
    def __init__(self, store: CustomerStore):
        self.store = store

    def run(self, filters: CustomerFilter, limit: int, cursor: str | None = None) -> Page[Customer]:
        scope = "customers"
        after = decode_cursor(cursor, scope) if cursor else None
        items = self.store.list_customers(filters, limit + 1, after)
        if len(items) <= limit:
            return Page(items=items, next_cursor=None)
        shown = items[:limit]
        return Page(items=shown, next_cursor=encode_cursor(scope, (shown[-1].last_contact_at, shown[-1].id)))


class GetCustomer:
    def __init__(self, store: CustomerStore):
        self.store = store

    def run(self, customer_id: int) -> CustomerDetail:
        detail = self.store.get_customer(customer_id)
        if detail is None:
            raise NotFound(f"Customer {customer_id} does not exist.")
        return detail


class RecordConsent:
    def __init__(self, store: CustomerStore):
        self.store = store

    def run(
        self,
        customer_id: int,
        kind: ConsentKind,
        status: ConsentStatus,
        *,
        conversation_id: int | None = None,
        wording: str | None = None,
        details: CustomerDetailsInput | None = None,
    ) -> CustomerDetail:
        if status is ConsentStatus.UNKNOWN:
            raise InvalidInput("status", "must be asked, granted, declined or withdrawn")
        if details is not None and not (kind is ConsentKind.DETAILS and status is ConsentStatus.GRANTED):
            raise InvalidInput("details", "only allowed when recording kind=details, status=granted")

        current = self.store.get_customer(customer_id)
        if current is None:
            raise NotFound(f"Customer {customer_id} does not exist.")

        channel: Channel | None = None
        evidence: int | None = None
        if conversation_id is not None:
            found = self.store.conversation_evidence(customer_id, conversation_id)
            if found is None:
                raise NotFound(f"Conversation {conversation_id} does not belong to customer {customer_id}.")
            channel, evidence = found

        now = (
            current.customer.details_consent
            if kind is ConsentKind.DETAILS
            else current.customer.marketing_consent
        )
        if status is now and details is None:
            return current  # nothing changes: safe to repeat
        # Asking again must never undo a "granted" (and so never erase saved details).
        new_current = now if (status is ConsentStatus.ASKED and now is ConsentStatus.GRANTED) else status
        if (
            kind is ConsentKind.MARKETING
            and new_current is ConsentStatus.GRANTED
            and current.customer.details_consent is not ConsentStatus.GRANTED
        ):
            raise InvalidInput("status", "offers need saved details: record a YES to saving details first")

        self.store.record_consent(
            ConsentChange(
                customer_id=customer_id,
                kind=kind,
                status=status,
                new_current=new_current,
                channel=channel,
                wording=wording,
                evidence_message_id=evidence,
                details=details,
                expected_current=now,  # refuse if someone changed it meanwhile (no lost updates)
            )
        )
        updated = self.store.get_customer(customer_id)
        if updated is None:
            raise NotFound(f"Customer {customer_id} does not exist.")
        return updated
