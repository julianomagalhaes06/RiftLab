"""Feedback per analysis, reflection and transfer mode on attempts (CC-505)."""

import sqlalchemy as sa
from alembic import op

revision = "c50500000001"
down_revision = "c30400000001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "analysis_feedback",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "analysis_id",
            sa.Uuid(),
            sa.ForeignKey("analyses.id", name="fk_analysis_feedback_analysis_id_analyses"),
            nullable=False,
        ),
        sa.Column("item_id", sa.Integer(), nullable=False),
        sa.Column("reviewer_hash", sa.String(length=64), nullable=False),
        sa.Column("rubric_version", sa.String(length=40), nullable=False),
        sa.Column("grade", sa.Integer(), nullable=True),
        sa.Column("reason", sa.String(length=1000), nullable=False),
        sa.Column("idempotency_key", sa.String(length=200), nullable=False),
        sa.Column("request_sha256", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("idempotency_key", name="uq_analysis_feedback_idempotency_key"),
        sa.UniqueConstraint(
            "analysis_id",
            "item_id",
            "reviewer_hash",
            "rubric_version",
            name="uq_analysis_feedback_judgement",
        ),
        sa.CheckConstraint(
            "grade IS NULL OR grade BETWEEN 0 AND 3", name="ck_analysis_feedback_grade_range"
        ),
    )
    op.execute("""
        CREATE FUNCTION reject_feedback_update() RETURNS trigger AS $$
        BEGIN RAISE EXCEPTION 'feedback is immutable'; END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("""
        CREATE TRIGGER analysis_feedback_immutable BEFORE UPDATE ON analysis_feedback
        FOR EACH ROW EXECUTE FUNCTION reject_feedback_update()
    """)
    op.add_column(
        "learning_attempts", sa.Column("reflection", sa.String(length=1000), nullable=True)
    )
    op.add_column(
        "learning_attempts", sa.Column("reflected_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "learning_attempts",
        sa.Column("mode", sa.String(length=20), nullable=False, server_default="STUDY"),
    )
    op.create_check_constraint(
        "ck_learning_attempts_mode_known", "learning_attempts", "mode IN ('STUDY', 'TRANSFER')"
    )


def downgrade() -> None:
    op.drop_constraint("ck_learning_attempts_mode_known", "learning_attempts", type_="check")
    op.drop_column("learning_attempts", "mode")
    op.drop_column("learning_attempts", "reflected_at")
    op.drop_column("learning_attempts", "reflection")
    op.execute("DROP TRIGGER analysis_feedback_immutable ON analysis_feedback")
    op.execute("DROP FUNCTION reject_feedback_update()")
    op.drop_table("analysis_feedback")
