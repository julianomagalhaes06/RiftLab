"""Fim do modo visitante: a leitura passou a ser pública sem conta.

Os visitantes eram contas temporárias (um dia) só da demonstração; saem com as sessões e as
tentativas deles (exclusão em cascata). A coluna `users.is_guest` deixa de existir.
"""

import sqlalchemy as sa
from alembic import op

revision = "s20150000001"
down_revision = "s20140000001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DELETE FROM users WHERE is_guest")
    op.drop_column("users", "is_guest")


def downgrade() -> None:
    op.add_column(
        "users",
        sa.Column("is_guest", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
