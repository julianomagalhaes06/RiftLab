"""catalog provenance and canonical selection

Revisão CC-202: sync_catalog persistia só o hash combinado de identidade
(catalog_versions.sha256), nunca raw_hash/curation_rules_version
separadamente — sha256 não é reversível, então essa proveniência era
irrecuperável depois de persistida. Adiciona os dois campos, mais
is_canonical (associação explícita/deliberada de qual linha resolve uma
identidade (game_patch, data_dragon_version, mapping_version) — substitui a
resolução antiga por ORDER BY created_at, que podia empatar e escolher
incidentalmente por data) e a tabela catalog_excluded_items (itens da fonte
bruta excluídos da curadoria com motivo, antes descartados ao persistir).

Backfill de linhas legadas (anteriores a esta migração) nunca inventa
proveniência real: raw_hash/curation_rules_version recebem o sentinela
explícito 'LEGACY_UNKNOWN'. is_canonical só é marcado automaticamente
quando uma identidade tem exatamente UMA linha legada (fato observável, não
suposição) — identidades com múltiplas linhas legadas para a mesma
(game_patch, data_dragon_version, mapping_version) ficam sem canônico
definido (resolve_catalog_for_match devolve MISSING_CATALOG até resolução
manual), nunca uma escolha automática entre elas.

Revision ID: a3f6b1d8c4e2
Revises: f2a7c9d4e1b6
Create Date: 2026-09-17 09:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a3f6b1d8c4e2"
down_revision: str | None = "f2a7c9d4e1b6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_LEGACY_SENTINEL = "LEGACY_UNKNOWN"


def upgrade() -> None:
    bind = op.get_bind()

    op.add_column("catalog_versions", sa.Column("raw_hash", sa.String(), nullable=True))
    op.add_column(
        "catalog_versions", sa.Column("curation_rules_version", sa.String(), nullable=True)
    )
    op.add_column(
        "catalog_versions",
        sa.Column("is_canonical", sa.Boolean(), nullable=False, server_default=sa.false()),
    )

    # Nunca inventa proveniência real para linhas anteriores a esta
    # migração — sentinela explícito, nunca um valor plausível fabricado.
    bind.execute(
        sa.text(
            """
            UPDATE catalog_versions
            SET raw_hash = :sentinel, curation_rules_version = :sentinel
            WHERE raw_hash IS NULL
            """
        ),
        {"sentinel": _LEGACY_SENTINEL},
    )
    op.alter_column("catalog_versions", "raw_hash", nullable=False)
    op.alter_column("catalog_versions", "curation_rules_version", nullable=False)
    op.alter_column("catalog_versions", "is_canonical", server_default=None)

    # is_canonical automático só quando a identidade tem exatamente uma
    # linha legada (fato observável) — nunca escolhe entre múltiplas.
    bind.execute(
        sa.text(
            """
            UPDATE catalog_versions cv
            SET is_canonical = true
            WHERE (
                SELECT COUNT(*) FROM catalog_versions cv2
                WHERE cv2.game_patch = cv.game_patch
                  AND cv2.data_dragon_version = cv.data_dragon_version
                  AND cv2.mapping_version = cv.mapping_version
            ) = 1
            """
        )
    )

    op.create_table(
        "catalog_excluded_items",
        sa.Column("catalog_id", sa.Uuid(), nullable=False),
        sa.Column("item_id", sa.Integer(), nullable=False),
        sa.Column("reason_code", sa.String(), nullable=False),
        sa.Column("detail", sa.String(), nullable=False),
        sa.ForeignKeyConstraint(
            ["catalog_id"],
            ["catalog_versions.id"],
            name=op.f("fk_catalog_excluded_items_catalog_id_catalog_versions"),
        ),
        sa.PrimaryKeyConstraint("catalog_id", "item_id", name=op.f("pk_catalog_excluded_items")),
    )

    op.drop_index("ix_catalog_versions_identity_created_at", table_name="catalog_versions")
    op.create_index(
        "ux_catalog_versions_canonical_identity",
        "catalog_versions",
        ["game_patch", "data_dragon_version", "mapping_version"],
        unique=True,
        postgresql_where=sa.text("is_canonical"),
    )


def downgrade() -> None:
    op.drop_index("ux_catalog_versions_canonical_identity", table_name="catalog_versions")
    op.create_index(
        "ix_catalog_versions_identity_created_at",
        "catalog_versions",
        ["game_patch", "data_dragon_version", "mapping_version", "created_at"],
        unique=False,
    )
    op.drop_table("catalog_excluded_items")
    op.drop_column("catalog_versions", "is_canonical")
    op.drop_column("catalog_versions", "curation_rules_version")
    op.drop_column("catalog_versions", "raw_hash")
