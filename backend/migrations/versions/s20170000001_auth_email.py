"""Contas por e-mail confirmado com código (cadastro, login e recuperação de senha).

As contas de `users` se identificavam só por um nome de usuário, sem e-mail para confirmar,
e saem aqui com tudo o que depende delas (sessões, vínculo Riot, tentativas e julgamentos, em
cascata). `users.email` guarda o endereço em minúsculas e é único; `email_verified_at` nulo é
conta ainda não confirmada, que não entra. `auth_email_codes` tem no máximo um código vivo
por conta e finalidade, guardado só como hash.
"""

import sqlalchemy as sa
from alembic import op

revision = "s20170000001"
down_revision = "s20160000001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DELETE FROM users")
    op.drop_column("users", "username_key")
    op.drop_column("users", "username")
    op.add_column("users", sa.Column("email", sa.String(254), nullable=False))
    op.add_column(
        "users", sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_unique_constraint("uq_users_email", "users", ["email"])
    op.create_table(
        "auth_email_codes",
        sa.Column(
            "user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column("purpose", sa.String(16), primary_key=True),
        sa.Column("code_hash", sa.String(255), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("purpose IN ('VERIFY','RESET')", name="purpose_valid"),
    )


def downgrade() -> None:
    op.drop_table("auth_email_codes")
    op.execute("DELETE FROM users")
    op.drop_constraint("uq_users_email", "users", type_="unique")
    op.drop_column("users", "email_verified_at")
    op.drop_column("users", "email")
    op.add_column("users", sa.Column("username", sa.String(32), nullable=False))
    op.add_column("users", sa.Column("username_key", sa.String(32), nullable=False))
    op.create_unique_constraint("users_username_key_key", "users", ["username_key"])
