"""match reconstructions marker

Revisão CC-203: idempotência de reconstruct_match checava só
list_decision_states do PRIMEIRO participante da partida — se esse
participante especificamente não tinha nenhuma compra (mas outro tinha), a
checagem sempre via lista vazia e uma reexecução tentava reinserir estados
já existentes, levantando IntegrityError. Marca explícita e atômica
(gravada na MESMA transação dos DecisionState produzidos) resolve
idempotência independente de quantas compras cada participante teve —
inclusive o caso de zero compras em toda a partida, um resultado válido que
a heurística anterior não conseguia distinguir de "nunca reconstruída".

Revision ID: e4f8a2c9d6b1
Revises: c7e2a9f1b3d5
Create Date: 2026-09-18 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e4f8a2c9d6b1"
down_revision: str | None = "c7e2a9f1b3d5"
branch_labels: Sequence[str] | str | None = None
depends_on: Sequence[str] | str | None = None


def upgrade() -> None:
    op.create_table(
        "match_reconstructions",
        sa.Column("match_id", sa.String(), nullable=False),
        sa.Column("catalog_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["match_id"], ["matches.match_id"], name=op.f("fk_match_reconstructions_match")
        ),
        sa.ForeignKeyConstraint(
            ["catalog_id"],
            ["catalog_versions.id"],
            name=op.f("fk_match_reconstructions_catalog"),
        ),
        sa.PrimaryKeyConstraint("match_id", name=op.f("pk_match_reconstructions")),
    )


def downgrade() -> None:
    op.drop_table("match_reconstructions")
