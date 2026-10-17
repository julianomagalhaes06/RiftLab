"""Gold per participant per timeline frame, for the match gold chart."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "c71700000001"
down_revision = "c60300000001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "match_frames",
        sa.Column("match_id", sa.String(), nullable=False),
        sa.Column("frames", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("match_id", name="pk_match_frames"),
        sa.ForeignKeyConstraint(["match_id"], ["matches.match_id"], name="fk_match_frames_match"),
    )


def downgrade() -> None:
    op.drop_table("match_frames")
