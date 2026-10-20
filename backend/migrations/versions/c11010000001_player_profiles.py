"""Perfis de jogador buscados por Riot ID (CC-1101)."""

import sqlalchemy as sa
from alembic import op

revision = "c11010000001"
down_revision = "c90600000001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "player_profiles",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("account_key", sa.String(64), nullable=False, unique=True),
        sa.Column("game_name", sa.String(32), nullable=False),
        sa.Column("tag_line", sa.String(8), nullable=False),
        sa.Column("subject_hash", sa.String(64), nullable=False),
        sa.Column("profile_icon_id", sa.Integer()),
        sa.Column("summoner_level", sa.Integer()),
        sa.Column("refreshed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )


def downgrade() -> None:
    op.drop_table("player_profiles")
