"""Contribuição por partida para a saúde das regras (CC-906)."""

import sqlalchemy as sa
from alembic import op

revision = "c90600000001"
down_revision = "c90100000001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "match_rule_health_contributions",
        sa.Column(
            "match_id",
            sa.String(),
            sa.ForeignKey("matches.match_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("reconstruction_version", sa.Integer(), primary_key=True),
        sa.Column("game_patch", sa.String(), nullable=False),
        sa.Column("queue", sa.Integer(), nullable=False),
        sa.Column("application", sa.String()),
        sa.Column("source_patch", sa.String()),
        sa.Column("rules_fingerprint", sa.String(64)),
        sa.Column("clean_participants", sa.Integer(), nullable=False),
        sa.Column("failures", sa.Integer(), nullable=False),
        sa.Column("marker_key", sa.String(32), nullable=False),
        sa.Column("officials", sa.Integer(), nullable=False),
        sa.Column(
            "computed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "clean_participants >= 0 AND failures >= 0 AND failures <= clean_participants "
            "AND officials >= 0",
            name="contribution_counts",
        ),
        sa.CheckConstraint(
            "application IS NULL OR application IN ('VALIDATED', 'INHERITED')",
            name="contribution_application",
        ),
    )
    op.create_index(
        "ix_match_rule_health_contributions_game_patch",
        "match_rule_health_contributions",
        ["game_patch"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_match_rule_health_contributions_game_patch",
        table_name="match_rule_health_contributions",
    )
    op.drop_table("match_rule_health_contributions")
