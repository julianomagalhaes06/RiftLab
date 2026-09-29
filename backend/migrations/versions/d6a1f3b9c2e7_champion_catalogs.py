"""champion catalogs (CC-208): nomes e retratos oficiais por versão do Data Dragon

Revision ID: d6a1f3b9c2e7
Revises: c5e9a2d7f1b8
Create Date: 2026-09-29 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d6a1f3b9c2e7"
down_revision: str | None = "c5e9a2d7f1b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "champion_catalogs",
        sa.Column("data_dragon_version", sa.String(), nullable=False),
        sa.Column("locale", sa.String(), nullable=False),
        sa.Column("raw_sha256", sa.String(), nullable=False),
        sa.Column("rejected_entries", sa.Integer(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_champion_catalogs")),
    )
    op.create_index(
        "uq_champion_catalogs_version_locale",
        "champion_catalogs",
        ["data_dragon_version", "locale"],
        unique=True,
    )
    op.create_table(
        "champions",
        sa.Column("catalog_id", sa.Uuid(), nullable=False),
        sa.Column("champion_key", sa.Integer(), nullable=False),
        sa.Column("champion_text_id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("image_file", sa.String(), nullable=False),
        sa.ForeignKeyConstraint(
            ["catalog_id"],
            ["champion_catalogs.id"],
            name=op.f("fk_champions_catalog_id_champion_catalogs"),
        ),
        sa.PrimaryKeyConstraint("catalog_id", "champion_key", name=op.f("pk_champions")),
    )


def downgrade() -> None:
    op.drop_table("champions")
    op.drop_index("uq_champion_catalogs_version_locale", table_name="champion_catalogs")
    op.drop_table("champion_catalogs")
