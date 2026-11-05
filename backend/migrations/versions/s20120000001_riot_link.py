"""Vínculo da conta Riot pelo desafio do ícone (Sprint 2, card #12)."""

import sqlalchemy as sa
from alembic import op

revision = "s20120000001"
down_revision = "s20100000001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "riot_link_challenges",
        sa.Column(
            "user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column("account_key", sa.String(64), nullable=False),
        sa.Column("game_name", sa.String(32), nullable=False),
        sa.Column("tag_line", sa.String(8), nullable=False),
        sa.Column("icon_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "riot_links",
        sa.Column(
            "user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column("account_key", sa.String(64), nullable=False, unique=True),
        sa.Column("subject_hash", sa.String(64), nullable=False),
        sa.Column("game_name", sa.String(32), nullable=False),
        sa.Column("tag_line", sa.String(8), nullable=False),
        sa.Column("linked_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("riot_links")
    op.drop_table("riot_link_challenges")
