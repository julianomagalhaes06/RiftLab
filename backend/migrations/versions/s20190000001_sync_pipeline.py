"""Etapas visíveis da sincronização do histórico.

`history_sync_runs.steps` guarda quanto tempo levou cada consulta à Riot (conta, lista de
partidas, elo), só com números. `import_jobs.sync_run_id` liga cada importação à sincronização
que a pediu, para a tela acompanhar o download das partidas novas sem guardar IDs de partida na
execução: o expurgo apaga os jobs da partida e nada sobra aqui.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "s20190000001"
down_revision = "s20180000001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("history_sync_runs", sa.Column("steps", JSONB(), nullable=True))
    op.add_column(
        "import_jobs",
        sa.Column(
            "sync_run_id",
            sa.Uuid(),
            sa.ForeignKey(
                "history_sync_runs.id",
                ondelete="SET NULL",
                name="fk_import_jobs_sync_run_id_history_sync_runs",
            ),
            nullable=True,
        ),
    )
    op.create_index("ix_import_jobs_sync_run_id", "import_jobs", ["sync_run_id"])


def downgrade() -> None:
    op.drop_index("ix_import_jobs_sync_run_id", table_name="import_jobs")
    op.drop_column("import_jobs", "sync_run_id")
    op.drop_column("history_sync_runs", "steps")
