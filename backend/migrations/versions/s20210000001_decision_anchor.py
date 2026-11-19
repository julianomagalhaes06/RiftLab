"""Âncora de compromisso dos pontos de decisão (CC-1501, seleção v3).

`decision_points.anchor` guarda de onde vem o contexto da decisão de um item principal: o
estado da primeira compra depois do item principal anterior, seu instante e se o inventário
teve incerteza tolerada (só botas, consumíveis e trinket). Linhas de extrações anteriores ficam
com NULL e continuam lidas como antes. Só adiciona uma coluna anulável; nenhum dado é apagado.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "s20210000001"
down_revision = "s20200000001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "decision_points",
        sa.Column("anchor", postgresql.JSONB(none_as_null=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("decision_points", "anchor")
