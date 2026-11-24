"""Dead-letter table for updates we could not parse (W5).

Before this, a bad update was only logged; once the cursor moved past it, Telegram
deleted it and it was gone forever. Now the original JSON is kept here so it can be
inspected, fixed and replayed.

Privacy: `raw` can contain customer data (names, message text). It must follow the
same retention rules as `messages`.

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-24
"""

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE TABLE rejected_updates (
        id          BIGSERIAL PRIMARY KEY,
        source      TEXT        NOT NULL,          -- e.g. 'telegram'
        ref         TEXT,                          -- e.g. the update_id; NULL if it had none
        reason      TEXT        NOT NULL,
        raw         JSONB,                         -- the original payload, exactly as received
        received_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        CONSTRAINT uq_rejected_update UNIQUE (source, ref)   -- re-importing stores it once
    );
    CREATE INDEX ix_rejected_updates_received_at ON rejected_updates (received_at);
    """)


def downgrade() -> None:
    op.execute("DROP TABLE rejected_updates")
