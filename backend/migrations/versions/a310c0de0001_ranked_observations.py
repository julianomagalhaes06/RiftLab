"""Immutable observations of own ranked position (CC-310)."""

import sqlalchemy as sa
from alembic import op

revision = "a310c0de0001"
down_revision = "d6a1f3b9c2e7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ranked_observations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("subject_hash", sa.String(64)),
        sa.Column("account_key", sa.String(64), nullable=False),
        sa.Column("queue", sa.String(32), nullable=False),
        sa.Column("season", sa.String(20), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("state", sa.String(12), nullable=False),
        sa.Column("tier", sa.String(16)),
        sa.Column("division", sa.String(4)),
        sa.Column("league_points", sa.Integer()),
        sa.Column("wins", sa.Integer()),
        sa.Column("losses", sa.Integer()),
        sa.Column("error_code", sa.String(40)),
        sa.Column("origin", sa.String(30), nullable=False),
        sa.CheckConstraint(
            "state IN ('READY','UNRANKED','FAILED')",
            name="ck_ranked_observations_ranked_state_valid",
        ),
        sa.CheckConstraint(
            "league_points IS NULL OR league_points >= 0",
            name="ck_ranked_observations_ranked_lp_positive",
        ),
    )
    op.create_index(
        "ix_ranked_account_queue_season_time",
        "ranked_observations",
        ["account_key", "queue", "season", "captured_at", "id"],
    )


def downgrade() -> None:
    op.drop_index("ix_ranked_account_queue_season_time", table_name="ranked_observations")
    op.drop_table("ranked_observations")
