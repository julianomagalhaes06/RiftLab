"""Revisão sob demanda: a importação diz se agenda a preparação ao terminar.

`import_jobs.prepare_after`: verdadeiro (padrão, como antes) agenda a preparação na mesma
transação do sucesso; falso, usado pela sincronização de histórico, deixa a partida importada
sem preparação até alguém pedir a revisão ou o worker ficar ocioso.
"""

import sqlalchemy as sa
from alembic import op

revision = "s20160000001"
down_revision = "s20150000001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "import_jobs",
        sa.Column("prepare_after", sa.Boolean(), nullable=False, server_default=sa.true()),
    )


def downgrade() -> None:
    op.drop_column("import_jobs", "prepare_after")
