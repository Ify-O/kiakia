"""Channel CHECK constraints (W15) and indexes on foreign keys (W16).

- W15: `direction` and `kind` were already restricted to known values, but `channel`
  accepted any text. Now the database only accepts channels the domain knows
  (kiakia.domain.models.Channel). Adding a channel later = a new migration, on purpose.
- W16: PostgreSQL does NOT index foreign keys automatically. Without these, finding
  a contact's identities/conversations (and deleting a contact) scans whole tables.

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-24
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

CHANNELS = "('telegram', 'messenger', 'instagram', 'whatsapp', 'tiktok', 'email')"
TABLES = ("contact_identities", "conversations", "messages")


def upgrade() -> None:
    for table in TABLES:
        op.execute(f"ALTER TABLE {table} ADD CONSTRAINT ck_{table}_channel CHECK (channel IN {CHANNELS})")
    op.execute("CREATE INDEX ix_contact_identities_contact_id ON contact_identities (contact_id)")
    op.execute("CREATE INDEX ix_conversations_contact_id ON conversations (contact_id)")


def downgrade() -> None:
    op.execute("DROP INDEX ix_conversations_contact_id")
    op.execute("DROP INDEX ix_contact_identities_contact_id")
    for table in TABLES:
        op.execute(f"ALTER TABLE {table} DROP CONSTRAINT ck_{table}_channel")
