"""Creator subscription tier price + per-subscription price snapshot.

Revision ID: 3e4d5f6a7b8c
Revises: 2f1e3d4a5b6c
Create Date: 2026-08-08

- ``creator_profile.tier_price_cents``: the creator's own monthly subscription
  price (admin Settings tab). NULL/0 = the platform default
  ``settings.SUBSCRIPTION_TIER_PRICE_CENTS``.
- ``subscription.tier_price_cents``: the price **snapshotted at checkout** so
  the webhook reconciler records the exact agreed amount in the revenue ledger
  (NULL on legacy rows = the settings default at read time).
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "3e4d5f6a7b8c"
down_revision = "2f1e3d4a5b6c"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "creator_profile",
        sa.Column("tier_price_cents", sa.Integer(), nullable=True),
    )
    op.add_column(
        "subscription",
        sa.Column("tier_price_cents", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("subscription", "tier_price_cents")
    op.drop_column("creator_profile", "tier_price_cents")
