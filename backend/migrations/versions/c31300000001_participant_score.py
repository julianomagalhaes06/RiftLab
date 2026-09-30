"""Nullable factual score from completed matches (CC-313)."""

import sqlalchemy as sa
from alembic import op

revision = "c31300000001"
down_revision = "a310c0de0001"
branch_labels = None
depends_on = None

_COUNTS = ("kills", "deaths", "assists", "total_minions_killed", "neutral_minions_killed")


def upgrade() -> None:
    op.add_column("participants", sa.Column("win", sa.Boolean(), nullable=True))
    for name in _COUNTS:
        op.add_column("participants", sa.Column(name, sa.Integer(), nullable=True))
        op.create_check_constraint(
            f"ck_participants_{name}_non_negative",
            "participants",
            f"{name} IS NULL OR {name} >= 0",
        )


def downgrade() -> None:
    for name in reversed(_COUNTS):
        op.drop_constraint(f"ck_participants_{name}_non_negative", "participants", type_="check")
        op.drop_column("participants", name)
    op.drop_column("participants", "win")
