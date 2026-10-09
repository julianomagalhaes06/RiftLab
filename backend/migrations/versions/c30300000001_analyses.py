"""Immutable analyses of a decision point (CC-303)."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "c30300000001"
down_revision = "c40500000001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "analyses",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "decision_id",
            sa.Uuid(),
            sa.ForeignKey("decision_points.id", name="fk_analyses_decision_id_decision_points"),
            nullable=False,
        ),
        sa.Column("idempotency_key", sa.String(length=200), nullable=False),
        sa.Column("request_sha256", sa.String(length=64), nullable=False),
        sa.Column("engine", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column(
            "model_version_id",
            sa.Uuid(),
            sa.ForeignKey(
                "ml_model_versions.id", name="fk_analyses_model_version_id_ml_model_versions"
            ),
            nullable=True,
        ),
        sa.Column("result", JSONB(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("idempotency_key", name="uq_analyses_idempotency_key"),
    )
    op.execute("""
        CREATE FUNCTION reject_analysis_update() RETURNS trigger AS $$
        BEGIN RAISE EXCEPTION 'analysis is immutable'; END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("""
        CREATE TRIGGER analysis_immutable BEFORE UPDATE ON analyses
        FOR EACH ROW EXECUTE FUNCTION reject_analysis_update()
    """)


def downgrade() -> None:
    op.drop_table("analyses")
    op.execute("DROP FUNCTION reject_analysis_update()")
