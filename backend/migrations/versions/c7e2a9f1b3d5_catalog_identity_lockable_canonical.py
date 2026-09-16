"""catalog identity lockable canonical

Revisão complementar CC-202: o índice único parcial `is_canonical` sobre
catalog_versions (migração a3f6b1d8c4e2) só impedia duas linhas canônicas
simultâneas DEPOIS de ambas tentarem existir — duas sincronizações
concorrentes de conteúdo divergente para uma identidade NOVA (sem nenhuma
linha catalog_versions ainda visível uma para a outra) colidiam só no
COMMIT, e a perdedora recebia IntegrityError bruto do banco, derrubando a
transação inteira e perdendo o conteúdo que tinha acabado de inserir
(reproduzido com review_cc202_canonical_probe.py contra Postgres real
isolado antes desta correção).

Substitui por uma linha de identidade própria (catalog_identities,
game_patch+data_dragon_version+mapping_version como chave primária) que
existe de forma estável independente de qualquer catalog_versions —
promoção a canônico agora trava essa linha (SELECT ... FOR UPDATE) em vez
de competir por um índice único: a segunda transação concorrente espera a
primeira commitar em vez de receber um erro de banco, nunca perde conteúdo
já inserido.

Revision ID: c7e2a9f1b3d5
Revises: a3f6b1d8c4e2
Create Date: 2026-09-17 14:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c7e2a9f1b3d5"
down_revision: str | None = "a3f6b1d8c4e2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()

    op.create_table(
        "catalog_identities",
        sa.Column("game_patch", sa.String(), nullable=False),
        sa.Column("data_dragon_version", sa.String(), nullable=False),
        sa.Column("mapping_version", sa.String(), nullable=False),
        sa.Column("canonical_catalog_id", sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(
            ["canonical_catalog_id"],
            ["catalog_versions.id"],
            name=op.f("fk_catalog_identities_canonical_catalog_id_catalog_versions"),
        ),
        sa.PrimaryKeyConstraint(
            "game_patch",
            "data_dragon_version",
            "mapping_version",
            name=op.f("pk_catalog_identities"),
        ),
    )

    # Backfill não destrutivo: uma linha de identidade por combinação já
    # observada em catalog_versions; canonical_catalog_id herda o que já
    # estava is_canonical=true (preserva exatamente a decisão da migração
    # anterior, nunca escolhe de novo nem inventa).
    bind.execute(
        sa.text(
            """
            INSERT INTO catalog_identities
                (game_patch, data_dragon_version, mapping_version, canonical_catalog_id)
            SELECT DISTINCT game_patch, data_dragon_version, mapping_version, NULL::uuid
            FROM catalog_versions
            """
        )
    )
    bind.execute(
        sa.text(
            """
            UPDATE catalog_identities ci
            SET canonical_catalog_id = cv.id
            FROM catalog_versions cv
            WHERE cv.game_patch = ci.game_patch
              AND cv.data_dragon_version = ci.data_dragon_version
              AND cv.mapping_version = ci.mapping_version
              AND cv.is_canonical = true
            """
        )
    )

    op.drop_index("ux_catalog_versions_canonical_identity", table_name="catalog_versions")
    op.drop_column("catalog_versions", "is_canonical")


def downgrade() -> None:
    op.add_column(
        "catalog_versions",
        sa.Column("is_canonical", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.alter_column("catalog_versions", "is_canonical", server_default=None)

    bind = op.get_bind()
    bind.execute(
        sa.text(
            """
            UPDATE catalog_versions cv
            SET is_canonical = true
            FROM catalog_identities ci
            WHERE ci.canonical_catalog_id = cv.id
            """
        )
    )

    op.create_index(
        "ux_catalog_versions_canonical_identity",
        "catalog_versions",
        ["game_patch", "data_dragon_version", "mapping_version"],
        unique=True,
        postgresql_where=sa.text("is_canonical"),
    )

    op.drop_table("catalog_identities")
