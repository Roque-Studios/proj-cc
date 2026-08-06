'''unique (payment_provider, external_ref) on subscription

Revision ID: 8e0f1a2b3c4d
Revises: 7c9d0e1f2a3b
Create Date: 2026-08-05

A gateway subscription id uniquely identifies one local subscription; webhook
reconciliation looks rows up by this ref, so ambiguity would silently corrupt
it. Dev data accumulated duplicate refs (the mock provider reused ids across
restarts) — dedupe keeping the most recent row per pair, then enforce.
'''

from alembic import op

# revision identifiers, used by Alembic.
revision = "8e0f1a2b3c4d"
down_revision = "7c9d0e1f2a3b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Remove any row that has a *later* sibling sharing the same
    # (payment_provider, external_ref) — i.e. keep the max id per pair.
    op.execute(
        """
        DELETE FROM subscription
        WHERE id IN (
            SELECT s.id
            FROM subscription s
            JOIN subscription keep
              ON keep.payment_provider = s.payment_provider
             AND keep.external_ref = s.external_ref
            WHERE s.id < keep.id
        )
        """
    )
    op.create_unique_constraint(
        "uq_subscription_provider_ref",
        "subscription",
        ["payment_provider", "external_ref"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_subscription_provider_ref", "subscription", type_="unique"
    )
