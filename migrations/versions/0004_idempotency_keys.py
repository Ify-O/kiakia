"""Idempotency keys for safe retries of "send a reply" (docs/adr/0004).

One row per (scope, key). `request_hash` detects a key reused for a different request
(409). `message_id` points to the stored reply, so an identical retry returns it again
without sending anything. Keys older than 24 hours count as expired.

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-24
"""

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE TABLE idempotency_keys (
        scope        TEXT        NOT NULL,                 -- e.g. 'replies'
        key          TEXT        NOT NULL,                 -- the client's Idempotency-Key
        request_hash TEXT        NOT NULL,                 -- sha256 of the request it was first used for
        status       TEXT        NOT NULL CHECK (status IN ('in_progress', 'completed')),
        message_id   BIGINT      REFERENCES messages(id) ON DELETE SET NULL,
        created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
        completed_at TIMESTAMPTZ,
        PRIMARY KEY (scope, key)
    );
    CREATE INDEX ix_idempotency_keys_created_at ON idempotency_keys (created_at);
    """)


def downgrade() -> None:
    op.execute("DROP TABLE idempotency_keys")
