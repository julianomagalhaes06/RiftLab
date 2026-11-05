"""Visitante (card #14) e dono dos julgamentos por rubrica (card #11).

`users.is_guest`: visitante sem senha, restrito à demonstração. `analysis_feedback.user_id`
nulo = julgamento feito sem sessão; apagar o usuário apaga os julgamentos dele.
"""

import sqlalchemy as sa
from alembic import op

revision = "s20140000001"
down_revision = "s20110000001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("is_guest", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "analysis_feedback",
        sa.Column(
            "user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=True
        ),
    )
    op.create_index("ix_analysis_feedback_user_id", "analysis_feedback", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_analysis_feedback_user_id", table_name="analysis_feedback")
    op.drop_column("analysis_feedback", "user_id")
    op.drop_column("users", "is_guest")
