"""Initial schema: contacts, conversations, messages, sync cursors, import runs.

Written as plain SQL so it's easy to read and explain.
Later features (businesses, staff, payments) arrive as NEW migrations;
never edit this file once it has been applied.

Revision ID: 0001
Revises:
Create Date: 2026-11-24
"""
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    -- A customer (one person, possibly on several channels).
    CREATE TABLE contacts (
        id           BIGSERIAL PRIMARY KEY,
        display_name TEXT        NOT NULL,
        created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
    );

    -- That customer's identity on one channel (e.g. their Telegram user id).
    CREATE TABLE contact_identities (
        id          BIGSERIAL PRIMARY KEY,
        contact_id  BIGINT NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
        channel     TEXT   NOT NULL,
        external_id TEXT   NOT NULL,
        username    TEXT,
        CONSTRAINT uq_identity UNIQUE (channel, external_id)
    );

    -- A thread with one customer on one channel.
    CREATE TABLE conversations (
        id               BIGSERIAL PRIMARY KEY,
        channel          TEXT        NOT NULL,
        external_chat_id TEXT        NOT NULL,
        contact_id       BIGINT      NOT NULL REFERENCES contacts(id),
        last_inbound_at  TIMESTAMPTZ,   -- last message FROM the customer
        last_outbound_at TIMESTAMPTZ,   -- last reply FROM the business
        created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
        CONSTRAINT uq_conversation UNIQUE (channel, external_chat_id)
    );

    -- Every message in and out. The unique key makes imports idempotent.
    CREATE TABLE messages (
        id                  BIGSERIAL PRIMARY KEY,
        conversation_id     BIGINT      NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
        channel             TEXT        NOT NULL,
        external_chat_id    TEXT        NOT NULL,
        external_message_id TEXT        NOT NULL,
        direction           TEXT        NOT NULL CHECK (direction IN ('in', 'out')),
        kind                TEXT        NOT NULL CHECK (kind IN ('text', 'voice', 'audio', 'image', 'video', 'document', 'sticker', 'other')),
        body                TEXT,          -- text or caption; NULL for media without a caption
        media_ref           TEXT,          -- the platform's file id, to download later (voice notes!)
        sent_at             TIMESTAMPTZ NOT NULL,   -- always UTC
        imported_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
        CONSTRAINT uq_message UNIQUE (channel, external_chat_id, external_message_id)
    );
    CREATE INDEX ix_messages_conversation_sent ON messages (conversation_id, sent_at);

    -- Where each source should continue from (e.g. Telegram's update offset).
    CREATE TABLE sync_cursors (
        source     TEXT PRIMARY KEY,
        cursor     TEXT        NOT NULL,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
    );

    -- One row per import run, for debugging and the demo.
    CREATE TABLE import_runs (
        id            BIGSERIAL PRIMARY KEY,
        source        TEXT        NOT NULL,
        started_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
        finished_at   TIMESTAMPTZ,
        status        TEXT        NOT NULL DEFAULT 'running' CHECK (status IN ('running', 'succeeded', 'failed')),
        records_seen  INT         NOT NULL DEFAULT 0,
        records_saved INT         NOT NULL DEFAULT 0,
        records_bad   INT         NOT NULL DEFAULT 0,
        error         TEXT
    );
    """)


def downgrade() -> None:
    op.execute("""
    DROP TABLE import_runs;
    DROP TABLE sync_cursors;
    DROP TABLE messages;
    DROP TABLE conversations;
    DROP TABLE contact_identities;
    DROP TABLE contacts;
    """)
