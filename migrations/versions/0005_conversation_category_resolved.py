"""Category and resolved time on conversations (docs/adr/0006).

- category: a light label set by staff (enquiry / order / complaint / other), or NULL.
- resolved_at: when someone marked it resolved, or NULL. A conversation counts as
  "resolved" only while no customer message is newer than resolved_at; a new customer
  message makes it "waiting" again automatically (computed in queries, not stored).

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-25
"""

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    ALTER TABLE conversations
        ADD COLUMN category TEXT CHECK (category IN ('enquiry', 'order', 'complaint', 'other')),
        ADD COLUMN resolved_at TIMESTAMPTZ;
    CREATE INDEX ix_conversations_category ON conversations (category);
    CREATE INDEX ix_messages_sent_at ON messages (sent_at DESC, id DESC);
    """)


def downgrade() -> None:
    op.execute("""
    DROP INDEX ix_messages_sent_at;
    DROP INDEX ix_conversations_category;
    ALTER TABLE conversations DROP COLUMN resolved_at, DROP COLUMN category;
    """)
