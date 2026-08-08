"""Post likes and comments.

Revision ID: b1c2d3e4f5a6
Revises: 7a8b9c0d1e2f
Create Date: 2026-08-08

- ``post_like``: one row per (post, user) like — the unique pair makes liking
  idempotent; the client toggles by inserting/removing.
- ``post_comment``: text-only comments on posts (body validated at the API
  layer). Both cascade with the post (FK ON DELETE CASCADE).
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "b1c2d3e4f5a6"
down_revision = "7a8b9c0d1e2f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "post_like",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "post_id",
            sa.Integer(),
            sa.ForeignKey("post.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("user.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("post_id", "user_id", name="uq_post_like_post_user"),
    )
    op.create_index("ix_post_like_id", "post_like", ["id"])
    op.create_index("ix_post_like_post_id", "post_like", ["post_id"])
    op.create_index("ix_post_like_user_id", "post_like", ["user_id"])

    op.create_table(
        "post_comment",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "post_id",
            sa.Integer(),
            sa.ForeignKey("post.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("user.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_post_comment_id", "post_comment", ["id"])
    op.create_index("ix_post_comment_post_id", "post_comment", ["post_id"])
    op.create_index("ix_post_comment_user_id", "post_comment", ["user_id"])


def downgrade() -> None:
    op.drop_table("post_comment")
    op.drop_table("post_like")
