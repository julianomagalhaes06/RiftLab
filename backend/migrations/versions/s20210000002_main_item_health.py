"""Saúde das regras por itens principais (CC-1504).

`patch_rule_health` e `match_rule_health_contributions` ganham a mesma conferência com os itens
finais oficiais restrita a itens principais e componentes, com a linhagem nos dois lados (etapas
do item de suporte e resultados de transformação contam como a origem). NULL nas linhas antigas
até a recontagem do patch (`cc rules health`). Só adiciona colunas anuláveis; nada é apagado.
"""

import sqlalchemy as sa
from alembic import op

revision = "s20210000002"
down_revision = "s20210000001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "match_rule_health_contributions", sa.Column("main_failures", sa.Integer(), nullable=True)
    )
    op.add_column("patch_rule_health", sa.Column("main_failures", sa.Integer(), nullable=True))
    op.add_column("patch_rule_health", sa.Column("main_wilson_upper", sa.Float(), nullable=True))
    op.add_column("patch_rule_health", sa.Column("main_verdict", sa.String(), nullable=True))
    op.create_check_constraint(
        "health_main_verdict",
        "patch_rule_health",
        "main_verdict IS NULL OR main_verdict IN ('ACCEPT', 'REJECT', 'NO_SAMPLE')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_patch_rule_health_health_main_verdict", "patch_rule_health")
    for column in ("main_verdict", "main_wilson_upper", "main_failures"):
        op.drop_column("patch_rule_health", column)
    op.drop_column("match_rule_health_contributions", "main_failures")
