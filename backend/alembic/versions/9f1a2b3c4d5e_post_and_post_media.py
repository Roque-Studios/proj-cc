'''post + post_media tables

Revision ID: 9f1a2b3c4d5e
Revises: 8e0f1a2b3c4d
Create Date: 2026-08-05

Creator photo posts (follower-only content): a post belongs to a creator and
carries one or more validated image uploads (PostMedia rows).
'''

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "9f1a2b3c4d5e"
down_revision = "8e0f1a2b3c4d"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "post",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "creator_id",
            sa.Integer(),
            sa.ForeignKey("user.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("caption", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_post_creator_id", "post", ["creator_id"])
    op.create_index("ix_post_id", "post", ["id"])

    op.create_table(
        "post_media",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "post_id",
            sa.Integer(),
            sa.ForeignKey("post.id", ondelete="CASCADE"),
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
        sa.UniqueConstraint("storage_key", name="uq_post_media_storage_key"),
    )
    op.create_index("ix_post_media_id", "post_media", ["id"])
    op.create_index("ix_post_media_post_id", "post_media", ["post_id"])
    op.create_index("ix_post_media_storage_key", "post_media", ["storage_key"])


def downgrade() -> None:
    op.drop_table("post_media")
    op.drop_table("post")
