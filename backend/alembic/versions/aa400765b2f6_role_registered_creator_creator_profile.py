"""role registered/creator + creator profile

Revision ID: aa400765b2f6
Revises: cd6766c0f6f8
Create Date: 2026-08-06 00:10:33.265583

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'aa400765b2f6'
down_revision: Union[str, Sequence[str], None] = 'cd6766c0f6f8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Creator profile table (one-to-one with user).
    op.create_table('creator_profile',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('display_name', sa.String(length=100), nullable=True),
    sa.Column('bio', sa.Text(), nullable=True),
    sa.Column('avatar_url', sa.String(length=500), nullable=True),
    sa.Column('payout_info', sa.JSON(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['user.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_creator_profile_id'), 'creator_profile', ['id'], unique=False)
    op.create_index(op.f('ix_creator_profile_user_id'), 'creator_profile', ['user_id'], unique=True)

    # is_creator flag: add with a temporary default so existing rows become
    # False, then drop the server default to match the model (the app always
    # sets it explicitly).
    op.add_column(
        'user',
        sa.Column('is_creator', sa.Boolean(), nullable=False, server_default=sa.text('false')),
    )
    op.alter_column('user', 'is_creator', server_default=None)

    # Role enum: user/admin/creator -> registered/creator (map existing rows).
    op.execute("ALTER TYPE userrole RENAME TO userrole_old")
    op.execute("CREATE TYPE userrole AS ENUM ('registered', 'creator')")
    op.execute(
        """
        ALTER TABLE "user" ALTER COLUMN role TYPE userrole
        USING CASE role::text
                 WHEN 'user' THEN 'registered'::userrole
                 WHEN 'admin' THEN 'creator'::userrole
                 ELSE 'registered'::userrole
              END
        """
    )
    op.execute("ALTER TABLE \"user\" ALTER COLUMN role SET DEFAULT 'registered'")


def downgrade() -> None:
    """Downgrade schema."""
    # Role enum back to user/admin/creator.
    op.execute("ALTER TABLE \"user\" ALTER COLUMN role DROP DEFAULT")
    op.execute("ALTER TYPE userrole RENAME TO userrole_new")
    op.execute("CREATE TYPE userrole AS ENUM ('user', 'admin', 'creator')")
    op.execute(
        """
        ALTER TABLE "user" ALTER COLUMN role TYPE userrole
        USING CASE role::text
                 WHEN 'registered' THEN 'user'::userrole
                 ELSE 'creator'::userrole
              END
        """
    )
    op.execute("ALTER TABLE \"user\" ALTER COLUMN role SET DEFAULT 'user'")
    op.execute("DROP TYPE userrole_new")

    op.drop_column('user', 'is_creator')
    op.drop_index(op.f('ix_creator_profile_user_id'), table_name='creator_profile')
    op.drop_index(op.f('ix_creator_profile_id'), table_name='creator_profile')
    op.drop_table('creator_profile')
