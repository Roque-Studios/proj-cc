"""subscription incomplete status + checkout_url

Revision ID: 5f7c2d1e9a4b
Revises: b4c3aea32d63
Create Date: 2026-08-06 02:00:00.000000

Adds ``incomplete`` to the ``subscriptionstatus`` enum (pending payment state
for the subscribe endpoint) and a ``checkout_url`` column holding the hosted
checkout link for a pending subscription.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '5f7c2d1e9a4b'
down_revision: Union[str, Sequence[str], None] = 'b4c3aea32d63'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Postgres: add the new enum value. ``IF NOT EXISTS`` keeps this idempotent.
    op.execute(
        "ALTER TYPE subscriptionstatus ADD VALUE IF NOT EXISTS 'incomplete'"
    )
    op.add_column(
        'subscription',
        sa.Column('checkout_url', sa.String(length=500), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema.

    Postgres cannot remove a value from an enum type; the column drop is
    performed, and the enum value is left in place (harmless for old code).
    """
    op.drop_column('subscription', 'checkout_url')
