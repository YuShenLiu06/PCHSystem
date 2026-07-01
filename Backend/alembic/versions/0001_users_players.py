"""create users schema and players table

Revision ID: 0001_users_players
Revises:
Create Date: 2026-07-01
"""
import sqlalchemy as sa
from alembic import op

revision = "0001_users_players"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS users")
    op.create_table(
        "players",
        sa.Column(
            "uuid",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
        ),
        sa.Column("current_name", sa.String(64), nullable=False),
        sa.Column(
            "role",
            sa.String(16),
            nullable=False,
            server_default="user",
        ),
        sa.Column(
            "whitelist_state",
            sa.String(16),
            nullable=False,
            server_default="active",
        ),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        schema="users",
    )


def downgrade() -> None:
    op.drop_table("players", schema="users")
    op.execute("DROP SCHEMA IF EXISTS users")
