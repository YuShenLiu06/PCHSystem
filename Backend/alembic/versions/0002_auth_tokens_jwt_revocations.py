"""create auth_tokens and jwt_revocations

Revision ID: 0002_auth_jwt
Revises: 0001_users_players
"""
import sqlalchemy as sa
from alembic import op

revision = "0002_auth_jwt"
down_revision = "0001_users_players"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "auth_tokens",
        sa.Column("token", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("player_uuid", sa.dialects.postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.players.uuid"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("issued_ip", sa.String(64), nullable=True),
        sa.Column("exchanged_ip", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        schema="users",
    )
    op.create_index("ix_auth_tokens_player_expires", "auth_tokens",
                    ["player_uuid", "expires_at"], schema="users")

    op.create_table(
        "jwt_revocations",
        sa.Column("jti", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("player_uuid", sa.dialects.postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.players.uuid"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        schema="users",
    )
    op.create_index("ix_jwt_revocations_player_expires", "jwt_revocations",
                    ["player_uuid", "expires_at"], schema="users")


def downgrade() -> None:
    op.drop_table("jwt_revocations", schema="users")
    op.drop_table("auth_tokens", schema="users")
