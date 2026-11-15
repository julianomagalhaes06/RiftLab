"""Bastidores: papel de administrador e registro das chamadas à Riot.

`users.role` separa o administrador (vê os Bastidores) dos demais usuários; o papel só muda
pela linha de comando (`riftlab admin grant`). `riot_api_calls` guarda cada chamada à API da
Riot feita pela API ou pelo worker: qual consulta, região, resultado e tempo. Nenhuma URL,
Riot ID, PUUID ou ID de partida; as linhas com mais de um dia são apagadas pelo próprio registro.
"""

import sqlalchemy as sa
from alembic import op

revision = "s20200000001"
down_revision = "s20190000001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("role", sa.String(12), nullable=False, server_default="USER"),
    )
    op.create_check_constraint("role_valid", "users", "role IN ('USER','ADMIN')")
    op.create_table(
        "riot_api_calls",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("process", sa.String(8), nullable=False),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("region", sa.String(10), nullable=False),
        sa.Column("outcome", sa.String(24), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.CheckConstraint("process IN ('api','worker','cli')", name="process_valid"),
        sa.CheckConstraint("duration_ms >= 0 AND attempt >= 1", name="numbers_valid"),
    )
    op.create_index("ix_riot_api_calls_at", "riot_api_calls", ["at"])


def downgrade() -> None:
    op.drop_index("ix_riot_api_calls_at", table_name="riot_api_calls")
    op.drop_table("riot_api_calls")
    op.drop_constraint("ck_users_role_valid", "users", type_="check")
    op.drop_column("users", "role")
