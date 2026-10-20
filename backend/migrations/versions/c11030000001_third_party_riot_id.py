"""Riot ID de terceiros fora do banco em repouso (CC-1103).

O perfil buscado perde o Riot ID em claro (fica só o HMAC). A execução da sincronização guarda o
Riot ID do jogador só enquanto está ativa: a restrição exige que ele suma ao terminar.
"""

import sqlalchemy as sa
from alembic import op

revision = "c11030000001"
down_revision = "c11010000001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("player_profiles", "game_name")
    op.drop_column("player_profiles", "tag_line")
    op.add_column("history_sync_runs", sa.Column("game_name", sa.String(32)))
    op.add_column("history_sync_runs", sa.Column("tag_line", sa.String(8)))
    op.create_check_constraint(
        op.f("ck_history_sync_runs_history_sync_riot_id_active_only"),
        "history_sync_runs",
        "state IN ('QUEUED','RUNNING') OR (game_name IS NULL AND tag_line IS NULL)",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("ck_history_sync_runs_history_sync_riot_id_active_only"),
        "history_sync_runs",
        type_="check",
    )
    op.drop_column("history_sync_runs", "tag_line")
    op.drop_column("history_sync_runs", "game_name")
    # O Riot ID apagado não volta: os perfis guardados não têm como ser refeitos.
    op.execute("DELETE FROM player_profiles")
    op.add_column("player_profiles", sa.Column("game_name", sa.String(32), nullable=False))
    op.add_column("player_profiles", sa.Column("tag_line", sa.String(8), nullable=False))
