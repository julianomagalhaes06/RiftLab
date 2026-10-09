"""Learning attempts: choice before reveal (CC-304)."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "c30400000001"
down_revision = "c30300000001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "learning_attempts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "decision_id",
            sa.Uuid(),
            sa.ForeignKey(
                "decision_points.id", name="fk_learning_attempts_decision_id_decision_points"
            ),
            nullable=False,
        ),
        sa.Column("idempotency_key", sa.String(length=200), nullable=False),
        sa.Column("request_sha256", sa.String(length=64), nullable=False),
        sa.Column("objective", JSONB(), nullable=False),
        sa.Column("options", JSONB(), nullable=False),
        sa.Column("reason_codes", JSONB(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("selected_item_id", sa.Integer(), nullable=True),
        sa.Column("skipped", sa.Boolean(), nullable=False),
        sa.Column("rationale", sa.String(length=1000), nullable=True),
        sa.Column("hint_level", sa.Integer(), nullable=False),
        sa.Column(
            "analysis_id",
            sa.Uuid(),
            sa.ForeignKey("analyses.id", name="fk_learning_attempts_analysis_id_analyses"),
            nullable=True,
        ),
        sa.Column("revealed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("exercise_version", sa.String(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("idempotency_key", name="uq_learning_attempts_idempotency_key"),
        sa.CheckConstraint(
            "status IN ('OPEN', 'ANSWERED', 'REVEALED')", name="ck_learning_attempts_status_known"
        ),
        sa.CheckConstraint(
            "hint_level BETWEEN 0 AND 2", name="ck_learning_attempts_hint_level_range"
        ),
    )


def downgrade() -> None:
    op.drop_table("learning_attempts")
