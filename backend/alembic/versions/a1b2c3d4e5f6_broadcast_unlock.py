'''paid broadcasts + broadcast_unlock table

Revision ID: a1b2c3d4e5f6
Revises: 9f1a2b3c4d5e
Create Date: 2026-08-06

Paid broadcasts: a post with ``broadcast_price_cents`` set is a paid broadcast
(sent to all subscribers as a locked preview; each subscriber pays a one-time
price to unlock full media access). ``broadcast_unlock`` records those one-time
payments, one row per (subscriber, post).
'''

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "a1b2c3d4e5f6"
down_revision = "9f1a2b3c4d5e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "post",
        sa.Column("broadcast_price_cents", sa.Integer(), nullable=True),
    )

    op.create_table(
        "broadcast_unlock",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "subscriber_id",
            sa.Integer(),
            sa.ForeignKey("user.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "post_id",
            sa.Integer(),
            sa.ForeignKey("post.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("payment_provider", sa.String(length=50), nullable=True),
        sa.Column("external_ref", sa.String(length=255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "subscriber_id",
            "post_id",
            name="uq_broadcast_unlock_subscriber_post",
        ),
    )
    op.create_index("ix_broadcast_unlock_id", "broadcast_unlock", ["id"])
    op.create_index(
        "ix_broadcast_unlock_subscriber_id", "broadcast_unlock", ["subscriber_id"]
    )
    op.create_index("ix_broadcast_unlock_post_id", "broadcast_unlock", ["post_id"])


def downgrade() -> None:
    op.drop_table("broadcast_unlock")
    op.drop_column("post", "broadcast_price_cents")
