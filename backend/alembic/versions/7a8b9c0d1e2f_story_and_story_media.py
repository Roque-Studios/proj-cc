'''story + story_media tables

Revision ID: 7a8b9c0d1e2f
Revises: 6a5b4c3d2e1f
Create Date: 2026-08-08

Creator 24-hour stories (follower-only ephemeral content): a story belongs to
a creator, carries one or more validated image uploads (StoryMedia rows), and
auto-expires 24 hours after creation (expires_at gates every read path).
'''

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "7a8b9c0d1e2f"
down_revision = "6a5b4c3d2e1f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "story",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "creator_id",
            sa.Integer(),
            sa.ForeignKey("user.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("caption", sa.Text(), nullable=True),
        sa.Column(
            "expires_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_story_creator_id", "story", ["creator_id"])
    op.create_index("ix_story_expires_at", "story", ["expires_at"])
    op.create_index("ix_story_id", "story", ["id"])

    op.create_table(
        "story_media",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "story_id",
            sa.Integer(),
            sa.ForeignKey("story.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("media_type", sa.String(length=50), nullable=False),
        sa.Column("storage_key", sa.String(length=255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("storage_key", name="uq_story_media_storage_key"),
    )
    op.create_index("ix_story_media_id", "story_media", ["id"])
    op.create_index("ix_story_media_story_id", "story_media", ["story_id"])
    op.create_index("ix_story_media_storage_key", "story_media", ["storage_key"])


def downgrade() -> None:
    op.drop_table("story_media")
    op.drop_table("story")
