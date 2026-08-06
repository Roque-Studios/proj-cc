"""Add post visibility (soft-archive) and view tracking for the creator
content dashboard.

``post.is_visible`` lets a creator hide a post from followers (the feed
excludes it and non-owner media/unlock requests 404) without deleting it;
``post.view_count`` tracks engagement (media views served to non-owners).
"""

from alembic import op
import sqlalchemy as sa

revision = "3f4a5b6c7d8e"
down_revision = "6d2e3f4a5b6c"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "post",
        sa.Column("is_visible", sa.Boolean(), server_default=sa.true(), nullable=False),
    )
    op.add_column(
        "post",
        sa.Column("view_count", sa.Integer(), server_default="0", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("post", "view_count")
    op.drop_column("post", "is_visible")
