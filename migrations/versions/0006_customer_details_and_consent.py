"""Customer details and recorded consent (docs/adr/0007).

- contacts.phone / email: ONLY filled when the customer granted "save my details";
  erased again if they decline or withdraw.
- contacts.details_consent / marketing_consent: the CURRENT status, for fast filtering.
- consent_events: the append-only HISTORY (what was asked, the answer, when, where, and
  the customer's message kept as evidence). The current status always matches the latest event.

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-25
"""

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    ALTER TABLE contacts
        ADD COLUMN phone TEXT,
        ADD COLUMN email TEXT,
        ADD COLUMN details_consent TEXT NOT NULL DEFAULT 'unknown'
            CHECK (details_consent IN ('unknown', 'asked', 'granted', 'declined', 'withdrawn')),
        ADD COLUMN marketing_consent TEXT NOT NULL DEFAULT 'unknown'
            CHECK (marketing_consent IN ('unknown', 'asked', 'granted', 'declined', 'withdrawn')),
        -- Personal details may only exist while details consent is granted.
        ADD CONSTRAINT ck_contacts_details_need_consent
            CHECK (details_consent = 'granted' OR (phone IS NULL AND email IS NULL));

    CREATE TABLE consent_events (
        id                  BIGSERIAL PRIMARY KEY,
        contact_id          BIGINT      NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
        kind                TEXT        NOT NULL CHECK (kind IN ('details', 'marketing')),
        status              TEXT        NOT NULL CHECK (status IN ('asked', 'granted', 'declined', 'withdrawn')),
        channel             TEXT,
        wording             TEXT,           -- the exact question we asked
        evidence_message_id BIGINT REFERENCES messages(id) ON DELETE SET NULL,
        recorded_at         TIMESTAMPTZ NOT NULL DEFAULT now()
    );
    CREATE INDEX ix_consent_events_contact ON consent_events (contact_id, recorded_at);
    CREATE INDEX ix_contacts_details_consent ON contacts (details_consent);
    CREATE INDEX ix_contacts_marketing_consent ON contacts (marketing_consent);
    """)


def downgrade() -> None:
    op.execute("""
    DROP TABLE consent_events;
    DROP INDEX ix_contacts_marketing_consent;
    DROP INDEX ix_contacts_details_consent;
    ALTER TABLE contacts
        DROP CONSTRAINT ck_contacts_details_need_consent,
        DROP COLUMN marketing_consent,
        DROP COLUMN details_consent,
        DROP COLUMN email,
        DROP COLUMN phone;
    """)
