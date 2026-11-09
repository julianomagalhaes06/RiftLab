"""Nome da conta e login social (Google e Discord).

`users.display_name` aceita nulo porque as contas anteriores não têm nome. `users.password_hash`
também: quem só entrou pelo provedor não tem senha local até pedir uma em "Esqueci minha senha".
`auth_social_identities` liga o identificador estável do provedor à conta; o e-mail fica só em
`users`, porque no provedor ele pode mudar.
"""

import sqlalchemy as sa
from alembic import op

revision = "s20180000001"
down_revision = "s20170000001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("display_name", sa.String(40), nullable=True))
    op.alter_column("users", "password_hash", existing_type=sa.String(255), nullable=True)
    op.create_table(
        "auth_social_identities",
        sa.Column("provider", sa.String(16), primary_key=True),
        sa.Column("subject", sa.String(255), primary_key=True),
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("provider IN ('google','discord')", name="provider_valid"),
    )


def downgrade() -> None:
    op.drop_table("auth_social_identities")
    # Sem senha, a conta não teria como existir no esquema anterior.
    op.execute("DELETE FROM users WHERE password_hash IS NULL")
    op.alter_column("users", "password_hash", existing_type=sa.String(255), nullable=False)
    op.drop_column("users", "display_name")
