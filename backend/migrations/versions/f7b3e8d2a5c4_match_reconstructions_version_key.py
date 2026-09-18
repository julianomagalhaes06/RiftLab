"""match reconstructions version key

Antes desta migração, `match_reconstructions` (criada em e4f8a2c9d6b1) tinha
`match_id` como única chave primária: a marca não distinguia QUAL versão do
algoritmo de reconstrução a produziu. Assim, `DecisionState`s gravados por
uma versão anterior ficariam sem marca correspondente, e reexecutar uma
versão nova do algoritmo colidiria com esses estados antigos no índice único
de identidade (que inclui `state_version`) ou os trataria como "já
reconstruídos".

Adiciona `reconstruction_version` como parte da chave primária composta
(match_id, reconstruction_version) — marcas de versões diferentes nunca
colidem entre si, e uma marca de versão antiga nunca é tratada como "já
feita" para a versão atual. Backfill não destrutivo: qualquer marca já
existente recebe reconstruction_version=1, o valor que a aplicação gravava
como `state_version` de todo `DecisionState` antes desta migração. Nenhum
`DecisionState` é alterado ou apagado; a aplicação passa a gravar
`state_version`/`reconstruction_version` = RECONSTRUCTION_ALGORITHM_VERSION
(ver timeline/application.py) sem reescrever linhas de versões anteriores.

Revision ID: f7b3e8d2a5c4
Revises: e4f8a2c9d6b1
Create Date: 2026-09-18 09:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f7b3e8d2a5c4"
down_revision: str | None = "e4f8a2c9d6b1"
branch_labels: Sequence[str] | str | None = None
depends_on: Sequence[str] | str | None = None


def upgrade() -> None:
    op.add_column(
        "match_reconstructions",
        sa.Column("reconstruction_version", sa.Integer(), nullable=True),
    )
    bind = op.get_bind()
    # Backfill não destrutivo: qualquer marca pré-existente corresponde à
    # única versão que a aplicação gravava antes desta migração (1).
    bind.execute(sa.text("UPDATE match_reconstructions SET reconstruction_version = 1"))
    op.alter_column("match_reconstructions", "reconstruction_version", nullable=False)

    op.drop_constraint("pk_match_reconstructions", "match_reconstructions", type_="primary")
    op.create_primary_key(
        "pk_match_reconstructions",
        "match_reconstructions",
        ["match_id", "reconstruction_version"],
    )


def downgrade() -> None:
    op.drop_constraint("pk_match_reconstructions", "match_reconstructions", type_="primary")
    op.create_primary_key("pk_match_reconstructions", "match_reconstructions", ["match_id"])
    op.drop_column("match_reconstructions", "reconstruction_version")
