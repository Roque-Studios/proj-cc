"""subscription cancel_at_period_end

Revision ID: 6a8b3c4d5e6f
Revises: 5f7c2d1e9a4b
Create Date: 2026-08-06 03:00:00.000000

Adds ``cancel_at_period_end`` (non-renew flag) to the ``subscription`` table.
Existing rows default to ``false`` — no behavior change for active subscribers.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '6a8b3c4d5e6f'
down_revision: Union[str, Sequence[str], None] = '5f7c2d1e9a4b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'subscription',
        sa.Column(
            'cancel_at_period_end',
            sa.Boolean(),
            server_default=sa.text('false'),
            nullable=False,
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('subscription', 'cancel_at_period_end')
