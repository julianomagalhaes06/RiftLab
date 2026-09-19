"""decision extractions and points

CC-204: pontos de decisão persistidos, imutáveis e versionados, SEPARADOS de
`decision_states` (que não é reescrita: nenhum UPDATE, nenhuma coluna nova).

- `decision_extractions`: marca por (partida, tupla de versões) gravada na
  MESMA transação dos pontos; guarda a contagem de pontos/elegíveis (distingue
  "extraída com zero pontos" de "não extraída"). A tupla tem colunas
  ANULÁVEIS (classificação, curadoria, catálogo); no PostgreSQL, NULLs são
  distintos em UNIQUE por padrão, então a restrição usa NULLS NOT DISTINCT
  (PostgreSQL 15+; o compose usa 17). FK composta para a marca de reconstrução:
  não existe extração sem reconstrução na mesma versão.
- `decision_points`: uma linha por (extração, estado). FK composta para a
  identidade COMPLETA do estado (id, partida, participante, evento, versão),
  o que exige o índice único novo `uq_decision_states_id_identity` em
  `decision_states` (só um índice; nenhuma linha é alterada).
- Índice de paginação: (partida, participante, extração, tempo, source_index, id).
- Imutabilidade: gatilho BEFORE UPDATE rejeita qualquer UPDATE nas duas
  tabelas (DELETE segue permitido para o expurgo futuro).

Revision ID: b8d4f1a6c3e9
Revises: f7b3e8d2a5c4
Create Date: 2026-09-19 15:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b8d4f1a6c3e9"
down_revision: str | None = "f7b3e8d2a5c4"
branch_labels: Sequence[str] | str | None = None
depends_on: Sequence[str] | str | None = None

_IMMUTABLE_FUNCTION = "forbid_update_immutable_row"


def upgrade() -> None:
    op.create_unique_constraint(
        op.f("uq_decision_states_id_identity"),
        "decision_states",
        ["id", "match_id", "participant_id", "event_source_index", "state_version"],
    )

    op.create_table(
        "decision_extractions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("match_id", sa.String(), nullable=False),
        sa.Column("reconstruction_version", sa.Integer(), nullable=False),
        sa.Column("selection_version", sa.String(), nullable=False),
        sa.Column("feature_schema_version", sa.String(), nullable=False),
        sa.Column("scope_version", sa.String(), nullable=False),
        sa.Column("classification_version", sa.String(), nullable=True),
        sa.Column("curation_rules_version", sa.String(), nullable=True),
        sa.Column("catalog_id", sa.Uuid(), nullable=True),
        sa.Column("point_count", sa.Integer(), nullable=False),
        sa.Column("eligible_count", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "point_count >= 0 AND eligible_count >= 0 AND eligible_count <= point_count",
            name=op.f("ck_decision_extractions_counts_consistent"),
        ),
        sa.ForeignKeyConstraint(
            ["match_id"], ["matches.match_id"], name=op.f("fk_decision_extractions_match")
        ),
        sa.ForeignKeyConstraint(
            ["match_id", "reconstruction_version"],
            ["match_reconstructions.match_id", "match_reconstructions.reconstruction_version"],
            name=op.f("fk_decision_extractions_reconstruction"),
        ),
        sa.ForeignKeyConstraint(
            ["catalog_id"], ["catalog_versions.id"], name=op.f("fk_decision_extractions_catalog")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_decision_extractions")),
        sa.UniqueConstraint(
            "match_id",
            "reconstruction_version",
            "selection_version",
            "feature_schema_version",
            "scope_version",
            "classification_version",
            "curation_rules_version",
            "catalog_id",
            name=op.f("uq_decision_extractions_version_tuple"),
            postgresql_nulls_not_distinct=True,
        ),
    )

    op.create_table(
        "decision_points",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("extraction_id", sa.Uuid(), nullable=False),
        sa.Column("decision_state_id", sa.Uuid(), nullable=False),
        sa.Column("match_id", sa.String(), nullable=False),
        sa.Column("participant_id", sa.Integer(), nullable=False),
        sa.Column("event_source_index", sa.Integer(), nullable=False),
        sa.Column("event_timestamp_ms", sa.Integer(), nullable=False),
        sa.Column("reconstruction_version", sa.Integer(), nullable=False),
        sa.Column("selection_version", sa.String(), nullable=False),
        sa.Column("feature_schema_version", sa.String(), nullable=False),
        sa.Column("scope_version", sa.String(), nullable=False),
        sa.Column("classification_version", sa.String(), nullable=True),
        sa.Column("curation_rules_version", sa.String(), nullable=True),
        sa.Column("catalog_id", sa.Uuid(), nullable=True),
        sa.Column("target_item_id", sa.Integer(), nullable=False),
        sa.Column("target_class", sa.String(), nullable=True),
        sa.Column("main_item_ordinal", sa.Integer(), nullable=True),
        sa.Column("eligible", sa.Boolean(), nullable=False),
        sa.Column("reasons", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("budget", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("features", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "(eligible AND features IS NOT NULL) OR (NOT eligible AND features IS NULL)",
            name=op.f("ck_decision_points_eligible_iff_features"),
        ),
        sa.CheckConstraint(
            "main_item_ordinal IS NULL OR main_item_ordinal >= 1",
            name=op.f("ck_decision_points_ordinal_positive"),
        ),
        sa.CheckConstraint(
            "event_timestamp_ms >= 0", name=op.f("ck_decision_points_timestamp_non_negative")
        ),
        sa.ForeignKeyConstraint(
            ["extraction_id"],
            ["decision_extractions.id"],
            name=op.f("fk_decision_points_extraction"),
        ),
        sa.ForeignKeyConstraint(
            [
                "decision_state_id",
                "match_id",
                "participant_id",
                "event_source_index",
                "reconstruction_version",
            ],
            [
                "decision_states.id",
                "decision_states.match_id",
                "decision_states.participant_id",
                "decision_states.event_source_index",
                "decision_states.state_version",
            ],
            name=op.f("fk_decision_points_state_identity"),
        ),
        sa.ForeignKeyConstraint(
            ["catalog_id"], ["catalog_versions.id"], name=op.f("fk_decision_points_catalog")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_decision_points")),
        sa.UniqueConstraint(
            "extraction_id", "decision_state_id", name=op.f("uq_decision_points_state")
        ),
    )
    op.create_index(
        "ix_decision_points_pagination",
        "decision_points",
        [
            "match_id",
            "participant_id",
            "extraction_id",
            "event_timestamp_ms",
            "event_source_index",
            "id",
        ],
    )

    op.execute(
        f"""
        CREATE FUNCTION {_IMMUTABLE_FUNCTION}() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'linha imutavel: % nao aceita UPDATE', TG_TABLE_NAME
                USING ERRCODE = 'integrity_constraint_violation';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    for table in ("decision_extractions", "decision_points"):
        op.execute(
            f"CREATE TRIGGER trg_{table}_immutable BEFORE UPDATE ON {table} "
            f"FOR EACH ROW EXECUTE FUNCTION {_IMMUTABLE_FUNCTION}()"
        )


def downgrade() -> None:
    for table in ("decision_points", "decision_extractions"):
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_immutable ON {table}")
    op.execute(f"DROP FUNCTION IF EXISTS {_IMMUTABLE_FUNCTION}()")
    op.drop_index("ix_decision_points_pagination", table_name="decision_points")
    op.drop_table("decision_points")
    op.drop_table("decision_extractions")
    op.drop_constraint("uq_decision_states_id_identity", "decision_states", type_="unique")
