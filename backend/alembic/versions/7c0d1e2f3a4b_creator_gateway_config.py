'''creator_gateway_config table

Revision ID: 7c0d1e2f3a4b
Revises: a1b2c3d4e5f6
Create Date: 2026-08-06

Per-creator payment gateway configuration: which gateways a creator accepts
for subscriber checkout, plus that gateway's credentials. Credentials are
strictly per-creator (no platform env fallback for checkout); the ``config``
JSON column holds the credential fields, and ``enabled`` gates visibility in
subscriber checkout.
'''

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "7c0d1e2f3a4b"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "creator_gateway_config",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "creator_id",
            sa.Integer(),
            sa.ForeignKey("user.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("gateway", sa.String(length=50), nullable=False),
        sa.Column(
            "enabled",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("config", sa.JSON(), nullable=False),
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
            "gateway",
            name="uq_creator_gateway_config_creator_gateway",
        ),
    )
    op.create_index("ix_creator_gateway_config_id", "creator_gateway_config", ["id"])
    op.create_index(
        "ix_creator_gateway_config_creator_id",
        "creator_gateway_config",
        ["creator_id"],
    )


def downgrade() -> None:
    op.drop_table("creator_gateway_config")
