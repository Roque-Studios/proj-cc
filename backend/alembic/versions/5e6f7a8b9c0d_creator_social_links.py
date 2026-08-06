"""Add creator public profile social links.

Revision ID: 5e6f7a8b9c0d
Revises: 4c5d6e7f8a9b
Create Date: 2026-08-06

Adds ``creator_profile.social_links`` (JSON dict of platform -> handle/url)
shown on the creator's public landing page.
"""

from alembic import op
import sqlalchemy as sa

revision = "5e6f7a8b9c0d"
down_revision = "4c5d6e7f8a9b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("creator_profile", sa.Column("social_links", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("creator_profile", "social_links")
