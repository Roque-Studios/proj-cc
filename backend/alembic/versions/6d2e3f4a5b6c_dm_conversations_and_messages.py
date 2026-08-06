'''DM conversations + messages + creator messaging policy

Revision ID: 6d2e3f4a5b6c
Revises: 7c0d1e2f3a4b
Create Date: 2026-08-06

Creator-to-subscriber direct messages with thread grouping: a Conversation is
the unique (creator_id, subscriber_id) pair every message between them lands
in, and creator_profile gains the ``allow_messages_from_all_followers`` DM
policy (the gate for starting new threads).
'''

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "6d2e3f4a5b6c"
down_revision = "7c0d1e2f3a4b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "creator_profile",
        sa.Column(
            "allow_messages_from_all_followers",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )

    op.create_table(
        "conversation",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "creator_id",
            sa.Integer(),
            sa.ForeignKey("user.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "subscriber_id",
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
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "creator_id",
            "subscriber_id",
            name="uq_conversation_creator_subscriber",
        ),
    )
    op.create_index("ix_conversation_id", "conversation", ["id"])
    op.create_index("ix_conversation_creator_id", "conversation", ["creator_id"])
    op.create_index("ix_conversation_subscriber_id", "conversation", ["subscriber_id"])

    op.create_table(
        "message",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "conversation_id",
            sa.Integer(),
            sa.ForeignKey("conversation.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "sender_id",
            sa.Integer(),
            sa.ForeignKey("user.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "recipient_id",
            sa.Integer(),
            sa.ForeignKey("user.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_message_id", "message", ["id"])
    op.create_index("ix_message_conversation_id", "message", ["conversation_id"])
    op.create_index("ix_message_sender_id", "message", ["sender_id"])
    op.create_index("ix_message_recipient_id", "message", ["recipient_id"])


def downgrade() -> None:
    op.drop_table("message")
    op.drop_table("conversation")
    op.drop_column("creator_profile", "allow_messages_from_all_followers")
