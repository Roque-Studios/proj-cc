"""Creator TOS/privacy texts + subscription consent record.

Revision ID: 2f1e3d4a5b6c
Revises: b1c2d3e4f5a6
Create Date: 2026-08-08

- ``creator_profile.tos_text`` / ``privacy_text``: the creator's own Terms of
  Service and Privacy Policy texts (NULL/blank = the platform defaults in
  ``app.legal`` are served instead). Edited from the admin ``Legal`` tab.
- ``subscription.age_confirmed`` / ``subscription.tos_accepted_at``: the
  consent record captured at checkout — the subscriber confirmed they are 18+
  and accepted the Terms of Service before the payment started (the audit
  trail behind the checkout consent gate).
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "2f1e3d4a5b6c"
down_revision = "b1c2d3e4f5a6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("creator_profile", sa.Column("tos_text", sa.Text(), nullable=True))
    op.add_column("creator_profile", sa.Column("privacy_text", sa.Text(), nullable=True))
    op.add_column(
        "subscription",
        sa.Column(
            "age_confirmed",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "subscription",
        sa.Column("tos_accepted_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("subscription", "tos_accepted_at")
    op.drop_column("subscription", "age_confirmed")
    op.drop_column("creator_profile", "privacy_text")
    op.drop_column("creator_profile", "tos_text")
