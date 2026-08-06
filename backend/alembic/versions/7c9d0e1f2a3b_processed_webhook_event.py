'''processed_webhook_event (idempotency ledger)

Revision ID: 7c9d0e1f2a3b
Revises: 6a8b3c4d5e6f
Create Date: 2026-08-05

Records each processed (provider, event_id) so webhook redeliveries (provider
retries) are recognized and skipped - no duplicate renewals or failure
notifications.
'''

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "7c9d0e1f2a3b"
down_revision = "6a8b3c4d5e6f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "processed_webhook_event",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("event_id", sa.String(length=255), nullable=False),
        sa.Column(
            "processed_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "provider",
            "event_id",
            name="uq_webhook_event_provider_id",
        ),
    )


def downgrade() -> None:
    op.drop_table("processed_webhook_event")
