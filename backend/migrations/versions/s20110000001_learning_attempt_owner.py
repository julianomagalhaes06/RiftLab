"""Dono das tentativas de estudo (Sprint 2, card #11).

`learning_attempts.user_id` nulo = tentativa criada sem sessão (instalação de uma pessoa, como
antes das contas). Apagar o usuário apaga as tentativas, reflexões e respostas dele.
"""

import sqlalchemy as sa
from alembic import op

revision = "s20110000001"
down_revision = "s20120000001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "learning_attempts",
        sa.Column(
            "user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=True
        ),
    )
    op.create_index("ix_learning_attempts_user_id", "learning_attempts", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_learning_attempts_user_id", table_name="learning_attempts")
    op.drop_column("learning_attempts", "user_id")
