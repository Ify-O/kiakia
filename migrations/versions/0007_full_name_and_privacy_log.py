"""Customer rights and export (docs/adr/0009).

- contacts.full_name: the name the CUSTOMER gave us (consent-gated, erased on withdrawal).
  Until now it overwrote display_name, so withdrawing consent couldn't erase it.
  For customers who already said YES we copy display_name into full_name (we can't tell
  which names were typed in, so we keep both; nothing is lost).
- Marketing consent now needs details consent: anyone still "granted" for offers after their
  details were declined/withdrawn is withdrawn from offers, with a consent event as the record.
- Both rules are CHECK constraints, so no code path can break them.
- privacy_log: an anonymous record of erasures and exports (no personal data).

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-25
"""

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    ALTER TABLE contacts ADD COLUMN full_name TEXT;
    UPDATE contacts SET full_name = display_name WHERE details_consent = 'granted';

    INSERT INTO consent_events (contact_id, kind, status, wording)
    SELECT id, 'marketing', 'withdrawn', 'Automatic: details consent was declined or withdrawn (ADR 0009)'
    FROM contacts WHERE marketing_consent = 'granted' AND details_consent <> 'granted';
    UPDATE contacts SET marketing_consent = 'withdrawn'
    WHERE marketing_consent = 'granted' AND details_consent <> 'granted';

    ALTER TABLE contacts
        DROP CONSTRAINT ck_contacts_details_need_consent,
        ADD CONSTRAINT ck_contacts_details_need_consent
            CHECK (details_consent = 'granted' OR (full_name IS NULL AND phone IS NULL AND email IS NULL)),
        ADD CONSTRAINT ck_contacts_marketing_needs_details
            CHECK (marketing_consent <> 'granted' OR details_consent = 'granted');

    CREATE TABLE privacy_log (
        id          BIGSERIAL PRIMARY KEY,
        action      TEXT        NOT NULL CHECK (action IN ('erasure', 'export')),
        detail      JSONB       NOT NULL DEFAULT '{}'::jsonb,  -- counts and options only, never personal data
        recorded_at TIMESTAMPTZ NOT NULL DEFAULT now()
    );
    """)


def downgrade() -> None:
    op.execute("""
    DROP TABLE privacy_log;
    ALTER TABLE contacts
        DROP CONSTRAINT ck_contacts_marketing_needs_details,
        DROP CONSTRAINT ck_contacts_details_need_consent;
    UPDATE contacts SET display_name = full_name WHERE full_name IS NOT NULL;
    ALTER TABLE contacts
        DROP COLUMN full_name,
        ADD CONSTRAINT ck_contacts_details_need_consent
            CHECK (details_consent = 'granted' OR (phone IS NULL AND email IS NULL));
    """)
