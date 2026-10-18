"""Inventário final oficial e histórico de saúde da herança."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "c80100000001"
down_revision = "c71700000001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "match_reconstructions",
        sa.Column("rules_provenance", postgresql.JSONB(), nullable=True),
    )
    op.create_table(
        "participant_final_items",
        sa.Column("match_id", sa.String(), nullable=False),
        sa.Column("participant_id", sa.Integer(), nullable=False),
        sa.Column("items", postgresql.JSONB(), nullable=False),
        sa.Column("trinket_item_id", sa.Integer()),
        sa.Column("role_bound_item_id", sa.Integer()),
        sa.Column("raw_hash", sa.String(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("match_id", "participant_id"),
        sa.ForeignKeyConstraint(
            ["match_id", "participant_id"], ["participants.match_id", "participants.participant_id"]
        ),
        sa.CheckConstraint(
            "jsonb_typeof(items) = 'array' AND jsonb_array_length(items) <= 6",
            name="final_items_array",
        ),
    )
    op.create_table(
        "patch_rule_health",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("game_patch", sa.String(), nullable=False),
        sa.Column("queue", sa.Integer()),
        sa.Column("application", sa.String(), nullable=False),
        sa.Column("source_patch", sa.String(), nullable=False),
        sa.Column("rules_fingerprint", sa.String(64), nullable=False),
        sa.Column("reconstruction_version", sa.Integer(), nullable=False),
        sa.Column("clean_participants", sa.Integer(), nullable=False),
        sa.Column("failures", sa.Integer(), nullable=False),
        sa.Column("contributing_matches", sa.Integer(), nullable=False),
        sa.Column("wilson_upper", sa.Float()),
        sa.Column("verdict", sa.String(), nullable=False),
        sa.Column("is_invalidation", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "computed_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.clock_timestamp(),
            nullable=False,
        ),
        sa.CheckConstraint("application IN ('VALIDATED', 'INHERITED')", name="health_application"),
        sa.CheckConstraint("queue IS NULL OR queue IN (420, 440)", name="health_queue"),
        sa.CheckConstraint(
            "reconstruction_version > 0 AND clean_participants >= 0 AND failures >= 0 "
            "AND failures <= clean_participants AND contributing_matches >= 0 "
            "AND contributing_matches <= clean_participants",
            name="health_counts",
        ),
        sa.CheckConstraint("verdict IN ('ACCEPT', 'REJECT', 'NO_SAMPLE')", name="health_verdict"),
        sa.CheckConstraint(
            "NOT is_invalidation OR (verdict = 'NO_SAMPLE' AND clean_participants = 0 "
            "AND failures = 0 AND contributing_matches = 0 AND wilson_upper IS NULL)",
            name="health_invalidation_shape",
        ),
        sa.CheckConstraint(
            "wilson_upper IS NULL OR (wilson_upper >= 0 AND wilson_upper <= 1)",
            name="health_wilson",
        ),
        sa.CheckConstraint("length(rules_fingerprint) = 64", name="health_fingerprint"),
        sa.CheckConstraint("length(source_patch) > 0", name="health_source_patch"),
    )
    op.create_index("ix_patch_rule_health_game_patch", "patch_rule_health", ["game_patch"])
    for table in ("participant_final_items", "patch_rule_health"):
        op.execute(
            f"CREATE TRIGGER trg_{table}_immutable BEFORE UPDATE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION forbid_update_immutable_row()"
        )


def downgrade() -> None:
    op.drop_table("patch_rule_health")
    op.drop_table("participant_final_items")
    op.drop_column("match_reconstructions", "rules_provenance")
