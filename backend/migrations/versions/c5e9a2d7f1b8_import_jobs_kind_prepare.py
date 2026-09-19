"""import jobs kind (IMPORT/PREPARE)

CC-205: a preparação durável pós-importação (catálogo -> reconstrução ->
extração de decisões) reaproveita a maquinaria de jobs do CC-201 (lease com
token de posse, heartbeat, backoff, retomada após crash) em vez de um loop
paralelo: `import_jobs` ganha `kind` (IMPORT | PREPARE). Linhas existentes são
IMPORT (default), então nada muda para o CC-201.

- `ux_import_jobs_active_identity` passa a valer só para `kind = 'IMPORT'`
  (mesma identidade e mesmos estados de antes); um PREPARE nunca disputa a
  unicidade de importação da mesma partida.
- `ux_prepare_jobs_active_match`: no máximo UMA preparação ativa
  (QUEUED/RUNNING/RETRY_WAIT) por partida. SUCCEEDED (READY) fica de fora para
  permitir uma nova preparação no futuro (ex.: nova versão de reconstrução).

Revision ID: c5e9a2d7f1b8
Revises: b8d4f1a6c3e9
Create Date: 2026-09-19 17:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c5e9a2d7f1b8"
down_revision: str | None = "b8d4f1a6c3e9"
branch_labels: Sequence[str] | str | None = None
depends_on: Sequence[str] | str | None = None


def upgrade() -> None:
    op.add_column(
        "import_jobs",
        sa.Column("kind", sa.String(), server_default="IMPORT", nullable=False),
    )
    op.create_check_constraint(
        op.f("ck_import_jobs_kind_valid"), "import_jobs", "kind IN ('IMPORT','PREPARE')"
    )
    op.drop_index("ux_import_jobs_active_identity", table_name="import_jobs")
    op.create_index(
        "ux_import_jobs_active_identity",
        "import_jobs",
        ["requested_match_id", "mode"],
        unique=True,
        postgresql_where=sa.text(
            "kind = 'IMPORT' AND state IN ('QUEUED','RUNNING','RETRY_WAIT','SUCCEEDED')"
        ),
    )
    op.create_index(
        "ux_prepare_jobs_active_match",
        "import_jobs",
        ["requested_match_id"],
        unique=True,
        postgresql_where=sa.text("kind = 'PREPARE' AND state IN ('QUEUED','RUNNING','RETRY_WAIT')"),
    )


def downgrade() -> None:
    # PREPARE não existe antes desta revisão: remove as linhas antes de voltar
    # ao índice único de importação sem `kind` (senão um PREPARE SUCCEEDED
    # colidiria com o IMPORT SUCCEEDED da mesma partida).
    op.execute("UPDATE import_jobs SET retry_of_job_id = NULL WHERE kind = 'PREPARE'")
    op.execute(
        "UPDATE import_jobs SET retry_of_job_id = NULL "
        "WHERE retry_of_job_id IN (SELECT id FROM import_jobs WHERE kind = 'PREPARE')"
    )
    op.execute("DELETE FROM import_jobs WHERE kind = 'PREPARE'")
    op.drop_index("ux_prepare_jobs_active_match", table_name="import_jobs")
    op.drop_index("ux_import_jobs_active_identity", table_name="import_jobs")
    op.create_index(
        "ux_import_jobs_active_identity",
        "import_jobs",
        ["requested_match_id", "mode"],
        unique=True,
        postgresql_where=sa.text("state IN ('QUEUED','RUNNING','RETRY_WAIT','SUCCEEDED')"),
    )
    op.drop_constraint(op.f("ck_import_jobs_kind_valid"), "import_jobs", type_="check")
    op.drop_column("import_jobs", "kind")
