"""Hosted one-time unlocks + paid message content.

Revision ID: 6a5b4c3d2e1f
Revises: 0b1c2d3e4f5a
Create Date: 2026-08-07

- ``paid_unlock`` gains ``checkout_url`` (the hosted payment link the
  subscriber is sent to) and ``paid_at`` (when the payment webhook activates
  the unlock) — broadcast unlocks now use the same hosted-checkout + webhook
  pattern as subscriptions instead of a synchronous card charge that the UI
  could never complete.
- Messages can carry one-time paid content: ``message.price_cents`` marks a
  paid message, ``message_media`` holds its image uploads, and
  ``paid_message_unlock`` records one-time unlocks of paid messages (same
  hosted-checkout + webhook lifecycle as broadcasts).
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "6a5b4c3d2e1f"
down_revision = "0b1c2d3e4f5a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- hosted broadcast unlocks ---
    op.add_column(
        "paid_unlock",
        sa.Column("checkout_url", sa.String(length=500), nullable=True),
    )
    op.add_column(
        "paid_unlock",
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
    )

    # --- paid message content ---
    op.add_column("message", sa.Column("price_cents", sa.Integer(), nullable=True))

    op.create_table(
        "message_media",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "message_id",
            sa.Integer(),
            sa.ForeignKey("message.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("media_type", sa.String(length=50), nullable=False),
        sa.Column("storage_key", sa.String(length=500), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_message_media_id", "message_media", ["id"])
    op.create_index("ix_message_media_message_id", "message_media", ["message_id"])

    op.add_column(
        "payment",
        sa.Column("message_id", sa.Integer(), nullable=True),
    )

    op.create_table(
        "paid_message_unlock",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "subscriber_id",
            sa.Integer(),
            sa.ForeignKey("user.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "message_id",
            sa.Integer(),
            sa.ForeignKey("message.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("payment_provider", sa.String(length=50), nullable=True),
        sa.Column("external_ref", sa.String(length=255), nullable=True),
        sa.Column("checkout_url", sa.String(length=500), nullable=True),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("refunded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "subscriber_id",
            "message_id",
            name="uq_paid_message_unlock_subscriber_message",
        ),
    )
    op.create_index(
        "ix_paid_message_unlock_id", "paid_message_unlock", ["id"]
    )
    op.create_index(
        "ix_paid_message_unlock_subscriber_id",
        "paid_message_unlock",
        ["subscriber_id"],
    )
    op.create_index(
        "ix_paid_message_unlock_message_id",
        "paid_message_unlock",
        ["message_id"],
    )


def downgrade() -> None:
    op.drop_table("paid_message_unlock")
    op.drop_table("message_media")
    op.drop_column("payment", "message_id")
    op.drop_column("message", "price_cents")
    op.drop_column("paid_unlock", "paid_at")
    op.drop_column("paid_unlock", "checkout_url")
