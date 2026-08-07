"""Add creator public hero banner.

Revision ID: 0b1c2d3e4f5a
Revises: 5e6f7a8b9c0d
Create Date: 2026-08-07

Adds ``creator_profile.banner_url`` — the public hero banner on the creator's
landing page (set by the banner upload endpoint, which stores the file and
records its public URL here).
"""

from alembic import op
import sqlalchemy as sa

revision = "0b1c2d3e4f5a"
down_revision = "5e6f7a8b9c0d"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("creator_profile", sa.Column("banner_url", sa.String(500), nullable=True))


def downgrade() -> None:
    op.drop_column("creator_profile", "banner_url")
