"""blocked_user table (creator bans)

Revision ID: 4f5e6d7c8b9a
Revises: 3e4d5f6a7b8c
Create Date: 2026-08-08

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "4f5e6d7c8b9a"
down_revision: Union[str, None] = "3e4d5f6a7b8c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "blocked_user",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("creator_id", sa.Integer(), sa.ForeignKey("user.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("user.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("creator_id", "user_id", name="uq_blocked_user_creator_user"),
    )
    op.create_index("ix_blocked_user_creator_id", "blocked_user", ["creator_id"])
    op.create_index("ix_blocked_user_user_id", "blocked_user", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_blocked_user_user_id", table_name="blocked_user")
    op.drop_index("ix_blocked_user_creator_id", table_name="blocked_user")
    op.drop_table("blocked_user")
