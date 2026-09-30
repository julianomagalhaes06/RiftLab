"""Immutable Data Dragon patch resolution and explicit current patch."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "c31400000001"
down_revision = "c31300000001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "patch_resolutions",
        sa.Column("game_patch", sa.String(), primary_key=True),
        sa.Column("data_dragon_version", sa.String(), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("versions_sha256", sa.String(length=64), nullable=False),
        sa.Column("rule_version", sa.String(), nullable=False),
    )
    op.create_table(
        "current_patch",
        sa.Column("singleton", sa.String(), primary_key=True),
        sa.Column("game_patch", sa.String(), nullable=False),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("versions_sha256", sa.String(length=64), nullable=False),
        sa.Column("adjacent_patches", JSONB(), nullable=False),
    )
    op.execute("""
        CREATE FUNCTION reject_patch_resolution_update() RETURNS trigger AS $$
        BEGIN RAISE EXCEPTION 'patch resolution is immutable'; END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("""
        CREATE TRIGGER patch_resolution_immutable BEFORE UPDATE ON patch_resolutions
        FOR EACH ROW EXECUTE FUNCTION reject_patch_resolution_update()
    """)


def downgrade() -> None:
    op.drop_table("current_patch")
    op.drop_table("patch_resolutions")
    op.execute("DROP FUNCTION reject_patch_resolution_update()")
