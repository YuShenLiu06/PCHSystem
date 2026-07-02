"""create sheets schema and sheets/sheet_rows tables

Revision ID: 0004_sheets
Revises: 0003_auth_tokens_revoked_at
Create Date: 2026-07-02
"""
import sqlalchemy as sa
from alembic import op

revision = "0004_sheets"
down_revision = "0003_auth_tokens_revoked_at"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS sheets")

    op.create_table(
        "sheets",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "owner_uuid",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.players.uuid"),
            nullable=False,
        ),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        schema="sheets",
    )

    op.create_table(
        "sheet_rows",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "sheet_id",
            sa.BigInteger(),
            sa.ForeignKey("sheets.sheets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("item_name", sa.Text(), nullable=False),
        sa.Column(
            "need_qty", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column(
            "done_flag", sa.SmallInteger(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column(
            "sort_order", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint("sheet_id", "item_name", name="uq_sheet_rows_sheet_item"),
        schema="sheets",
    )
    op.create_index(
        "ix_sheet_rows_sheet_id", "sheet_rows", ["sheet_id"], schema="sheets"
    )


def downgrade() -> None:
    op.drop_index("ix_sheet_rows_sheet_id", table_name="sheet_rows", schema="sheets")
    op.drop_table("sheet_rows", schema="sheets")
    op.drop_table("sheets", schema="sheets")
    op.execute("DROP SCHEMA IF EXISTS sheets")
