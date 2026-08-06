"""Add the payment ledger for the creator revenue dashboard.

Each completed monthly subscription payment and one-time broadcast unlock gets
a ``payment`` row; the revenue summary sums the *completed* rows and a refunded
unlock marks its row ``refunded``. ``post_id`` is deliberately not a FK so
revenue history survives post deletion.
"""

from alembic import op
import sqlalchemy as sa

revision = "4c5d6e7f8a9b"
down_revision = "3f4a5b6c7d8e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "payment",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "creator_id",
            sa.Integer(),
            sa.ForeignKey("user.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "subscriber_id",
            sa.Integer(),
            sa.ForeignKey("user.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("amount_cents", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.String(20),
            server_default="completed",
            nullable=False,
        ),
        sa.Column("payment_provider", sa.String(50), nullable=True),
        sa.Column("external_ref", sa.String(255), nullable=True),
        sa.Column("post_id", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_payment_creator_id", "payment", ["creator_id"])
    op.create_index("ix_payment_subscriber_id", "payment", ["subscriber_id"])
    op.create_index("ix_payment_external_ref", "payment", ["external_ref"])


def downgrade() -> None:
    op.drop_table("payment")
