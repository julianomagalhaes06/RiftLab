"""ML model registry: dataset manifests, model versions and promotion events (CC-405)."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "c40500000001"
down_revision = "c31400000001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ml_dataset_manifests",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("task", sa.String(), nullable=False),
        sa.Column("patch", sa.String(), nullable=False),
        sa.Column("description", sa.String(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("sha256", name="uq_ml_dataset_manifests_sha256"),
    )
    op.create_table(
        "ml_model_versions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("task", sa.String(), nullable=False),
        sa.Column("scope", sa.String(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("artifact_path", sa.String(), nullable=True),
        sa.Column("artifact_sha256", sa.String(length=64), nullable=False),
        sa.Column("feature_names", JSONB(), nullable=False),
        sa.Column("metrics", JSONB(), nullable=False),
        sa.Column(
            "dataset_manifest_id",
            sa.Uuid(),
            sa.ForeignKey(
                "ml_dataset_manifests.id",
                name="fk_ml_model_versions_dataset_manifest_id_ml_dataset_manifests",
            ),
            nullable=False,
        ),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("kind IN ('BASELINE', 'MODEL')", name="ck_ml_model_versions_kind_known"),
        sa.CheckConstraint(
            "status IN ('REGISTERED', 'ACTIVE', 'RETIRED')",
            name="ck_ml_model_versions_status_known",
        ),
        sa.CheckConstraint(
            "(kind = 'MODEL') = (artifact_path IS NOT NULL)",
            name="ck_ml_model_versions_artifact_iff_model",
        ),
    )
    op.create_index(
        "uq_ml_model_versions_one_active",
        "ml_model_versions",
        ["task", "scope"],
        unique=True,
        postgresql_where=sa.text("status = 'ACTIVE'"),
    )
    op.create_table(
        "ml_model_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "model_version_id",
            sa.Uuid(),
            sa.ForeignKey(
                "ml_model_versions.id", name="fk_ml_model_events_model_version_id_ml_model_versions"
            ),
            nullable=False,
        ),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("detail", sa.String(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "action IN ('REGISTER', 'PROMOTE', 'RETIRE', 'ROLLBACK')",
            name="ck_ml_model_events_action_known",
        ),
    )


def downgrade() -> None:
    op.drop_table("ml_model_events")
    op.drop_index("uq_ml_model_versions_one_active", table_name="ml_model_versions")
    op.drop_table("ml_model_versions")
    op.drop_table("ml_dataset_manifests")
