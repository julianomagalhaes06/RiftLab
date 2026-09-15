"""widen active identity index to include succeeded

Revisão CC-201, achado 4: sem SUCCEEDED no índice, um job concorrente que
termina SUCCEEDED entre a consulta e o insert de request_import/retry_import
não bloqueava a segunda inserção — criava um job QUEUED duplicado para uma
identidade já concluída, refazendo rede desnecessariamente. Reproduzido com
teste de barreira real (tests/integration/test_import_idempotency_race.py)
antes desta correção.

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


def upgrade() -> None:
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
