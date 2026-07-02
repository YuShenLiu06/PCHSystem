"""add revoked_at to auth_tokens and partial index on active tokens

Revision ID: 0003_auth_tokens_revoked_at
Revises: 0002_auth_jwt
Create Date: 2026-07-02
"""
import sqlalchemy as sa
from alembic import op

revision = "0003_auth_tokens_revoked_at"
down_revision = "0002_auth_jwt"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "auth_tokens",
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        schema="users",
    )
    op.execute(
        "CREATE INDEX ix_auth_tokens_player_active "
        "ON users.auth_tokens (player_uuid) "
        "WHERE used_at IS NULL AND revoked_at IS NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS users.ix_auth_tokens_player_active")
    op.drop_column("auth_tokens", "revoked_at", schema="users")