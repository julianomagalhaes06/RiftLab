"""Execuções da sincronização do histórico da conta própria (CC-901)."""

import sqlalchemy as sa
from alembic import op

revision = "c90100000001"
down_revision = "c80100000001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "history_sync_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("account_key", sa.String(64), nullable=False),
        sa.Column("state", sa.String(12), nullable=False),
        sa.Column(
            "requested_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("error_code", sa.String(40)),
        sa.Column("retry_after_seconds", sa.Float()),
        sa.Column("listed", sa.Integer(), server_default="0", nullable=False),
        sa.Column("new_enqueued", sa.Integer(), server_default="0", nullable=False),
        sa.Column("already_known", sa.Integer(), server_default="0", nullable=False),
        sa.Column("skipped_purged", sa.Integer(), server_default="0", nullable=False),
        sa.CheckConstraint(
            "state IN ('QUEUED','RUNNING','SUCCEEDED','FAILED')", name="history_sync_state"
        ),
        sa.CheckConstraint(
            "listed >= 0 AND new_enqueued >= 0 AND already_known >= 0 AND skipped_purged >= 0",
            name="history_sync_counts",
        ),
    )
    op.create_index(
        "ix_history_sync_account_requested", "history_sync_runs", ["account_key", "requested_at"]
    )
    op.create_index(
        "uq_history_sync_active",
        "history_sync_runs",
        ["account_key"],
        unique=True,
        postgresql_where=sa.text("state IN ('QUEUED','RUNNING')"),
    )


def downgrade() -> None:
    op.drop_index("uq_history_sync_active", table_name="history_sync_runs")
    op.drop_index("ix_history_sync_account_requested", table_name="history_sync_runs")
    op.drop_table("history_sync_runs")
