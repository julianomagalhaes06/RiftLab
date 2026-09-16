"""catalog content identity and resolution index

CC-202: catalog_versions.sha256 passa a ser a identidade de conteúdo do
catálogo (raw+mapping+curadoria, ver catalog/domain.py) — índice único
garante sync idempotente sob concorrência real (mesmo conteúdo nunca
duplica linha; conteúdo divergente sempre gera identidade/linha nova, nunca
sobrescreve a existente). Índice composto (game_patch, data_dragon_version,
mapping_version, created_at) suporta a resolução determinística
partida -> catálogo mais recente para uma identidade já fixada pelo mapping
explícito (nunca "latest" implícito entre patches distintos).

Revision ID: f2a7c9d4e1b6
Revises: 3781e941d8c3
Create Date: 2026-09-16 10:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f2a7c9d4e1b6"
down_revision: str | None = "3781e941d8c3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index("uq_catalog_versions_sha256", "catalog_versions", ["sha256"], unique=True)
    op.create_index(
        "ix_catalog_versions_identity_created_at",
        "catalog_versions",
        ["game_patch", "data_dragon_version", "mapping_version", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_catalog_versions_identity_created_at", table_name="catalog_versions")
    op.drop_index("uq_catalog_versions_sha256", table_name="catalog_versions")
