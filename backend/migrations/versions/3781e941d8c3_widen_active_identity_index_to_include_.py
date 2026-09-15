"""widen active identity index to include succeeded

Revisão CC-201, achado 4: sem SUCCEEDED no índice, um job concorrente que
termina SUCCEEDED entre a consulta e o insert de request_import/retry_import
não bloqueava a segunda inserção — criava um job QUEUED duplicado para uma
identidade já concluída, refazendo rede desnecessariamente. Reproduzido com
teste de barreira real (tests/integration/test_import_idempotency_race.py)
antes desta correção.

Revisão complementar CC-201-REVIEW-2: um banco anterior a esta migração
permitia legitimamente vários jobs SUCCEEDED para a mesma identidade (o
índice antigo só cobria QUEUED/RUNNING/RETRY_WAIT) — criar o índice único
novo direto sobre esses dados falharia com uma violação de constraint crua
e pouco acionável do Postgres. upgrade() verifica isso antes de tentar criar
o índice e aborta com diagnóstico explícito e as identidades afetadas em vez
de deixar o Postgres falhar sem contexto — nunca apaga/mescla histórico
automaticamente, nunca assume banco vazio.

Revision ID: 3781e941d8c3
Revises: b916edb0afd0
Create Date: 2026-09-15 15:20:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "3781e941d8c3"
down_revision: str | None = "b916edb0afd0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NEW_COVERED_STATES = ("QUEUED", "RUNNING", "RETRY_WAIT", "SUCCEEDED")


def upgrade() -> None:
    bind = op.get_bind()
    duplicates = bind.execute(
        sa.text(
            """
            SELECT requested_match_id, mode, COUNT(*) AS n
            FROM import_jobs
            WHERE state IN :states
            GROUP BY requested_match_id, mode
            HAVING COUNT(*) > 1
            """
        ).bindparams(sa.bindparam("states", expanding=True)),
        {"states": _NEW_COVERED_STATES},
    ).fetchall()
    if duplicates:
        identities = ", ".join(f"{row.requested_match_id!r}/{row.mode}" for row in duplicates[:10])
        raise RuntimeError(
            "migração 3781e941d8c3 abortada: já existem múltiplos import_jobs "
            "não-FAILED (incluindo possivelmente vários SUCCEEDED) para a mesma "
            "identidade (requested_match_id, mode) — isso era permitido pelo "
            "índice anterior (só cobria QUEUED/RUNNING/RETRY_WAIT). Criar o "
            "índice novo sobre esses dados violaria unicidade. Resolver "
            "manualmente antes de reexecutar esta migração: decidir qual job é "
            "o canônico de cada identidade afetada e mover os demais para outro "
            "estado (nunca apagar histórico automaticamente). Identidades "
            f"afetadas (até 10 mostradas, {len(duplicates)} no total): {identities}"
        )

    op.drop_index("ux_import_jobs_active_identity", table_name="import_jobs")
    op.create_index(
        "ux_import_jobs_active_identity",
        "import_jobs",
        ["requested_match_id", "mode"],
        unique=True,
        postgresql_where=sa.text("state IN ('QUEUED','RUNNING','RETRY_WAIT','SUCCEEDED')"),
    )


def downgrade() -> None:
    op.drop_index("ux_import_jobs_active_identity", table_name="import_jobs")
    op.create_index(
        "ux_import_jobs_active_identity",
        "import_jobs",
        ["requested_match_id", "mode"],
        unique=True,
        postgresql_where=sa.text("state IN ('QUEUED','RUNNING','RETRY_WAIT')"),
    )
