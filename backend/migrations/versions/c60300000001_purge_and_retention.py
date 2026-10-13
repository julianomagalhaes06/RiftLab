"""Purge of a match and its derivatives, tombstones and dataset invalidation (CC-603)."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "c60300000001"
down_revision = "c50500000001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint(op.f("ck_import_jobs_kind_valid"), "import_jobs", type_="check")
    op.create_check_constraint("kind_valid", "import_jobs", "kind IN ('IMPORT','PREPARE','PURGE')")
    op.create_index(
        "ux_purge_jobs_active_match",
        "import_jobs",
        ["requested_match_id"],
        unique=True,
        postgresql_where=sa.text("kind = 'PURGE' AND state IN ('QUEUED','RUNNING','RETRY_WAIT')"),
    )
    op.create_table(
        "purge_tombstones",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("match_ref", sa.String(length=64), nullable=False),
        sa.Column("mode", sa.String(length=8), nullable=False),
        sa.Column("reason", sa.String(length=20), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=True),
        sa.Column("removed", postgresql.JSONB(), nullable=False),
        sa.Column(
            "purged_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("match_ref", name="uq_purge_tombstones_match_ref"),
        sa.CheckConstraint("mode IN ('DEMO','RIOT')", name="mode_valid"),
        sa.CheckConstraint("reason IN ('USER_REQUEST','RETENTION')", name="reason_valid"),
    )
    op.add_column("ml_dataset_manifests", sa.Column("invalidated_at", sa.DateTime(timezone=True)))
    op.add_column("ml_dataset_manifests", sa.Column("invalidation_reason", sa.String(length=40)))
    op.add_column(
        "ml_dataset_manifests",
        sa.Column(
            "match_list_known", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
    )
    op.create_table(
        "ml_dataset_manifest_matches",
        sa.Column(
            "manifest_id",
            sa.Uuid(),
            sa.ForeignKey(
                "ml_dataset_manifests.id",
                name="fk_ml_dataset_manifest_matches_manifest_id_ml_dataset_manifests",
            ),
            primary_key=True,
        ),
        sa.Column("match_id", sa.String(), primary_key=True),
    )
    op.create_index(
        "ix_ml_dataset_manifest_matches_match_id", "ml_dataset_manifest_matches", ["match_id"]
    )
    op.drop_constraint(
        op.f("ck_ml_model_events_ck_ml_model_events_action_known"),
        "ml_model_events",
        type_="check",
    )
    op.create_check_constraint(
        "action_known",
        "ml_model_events",
        "action IN ('REGISTER','PROMOTE','RETIRE','ROLLBACK','INVALIDATE')",
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_ml_model_events_action_known"), "ml_model_events", type_="check")
    op.create_check_constraint(
        op.f("ck_ml_model_events_ck_ml_model_events_action_known"),
        "ml_model_events",
        "action IN ('REGISTER','PROMOTE','RETIRE','ROLLBACK')",
    )
    op.drop_index("ix_ml_dataset_manifest_matches_match_id", "ml_dataset_manifest_matches")
    op.drop_table("ml_dataset_manifest_matches")
    op.drop_column("ml_dataset_manifests", "match_list_known")
    op.drop_column("ml_dataset_manifests", "invalidation_reason")
    op.drop_column("ml_dataset_manifests", "invalidated_at")
    op.drop_table("purge_tombstones")
    op.drop_index("ux_purge_jobs_active_match", "import_jobs")
    op.drop_constraint(op.f("ck_import_jobs_kind_valid"), "import_jobs", type_="check")
    op.create_check_constraint("kind_valid", "import_jobs", "kind IN ('IMPORT','PREPARE')")
