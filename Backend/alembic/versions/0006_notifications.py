"""create notifications schema and notifications table

Revision ID: 0006_notifications
Revises: 0005_sheets_collab
Create Date: 2026-07-02

统一通知抽象层（spec 2026-07-02-sheets-mcdr-bridge-design §B）：
- schema notifications + 表 notifications.notifications
- recipient_uuid FK users.players.uuid ON DELETE CASCADE
- payload jsonb（结构化 {sheet_id, sheet_title, row_id, item_name, actor_uuid, actor_name, old, new}）
- delivered_at null=未投递；read_at null=未读
- 索引 (recipient_uuid, delivered_at) 服务 MCDR 轮询拉取
"""
import sqlalchemy as sa
from alembic import op

revision = "0006_notifications"
down_revision = "0005_sheets_collab"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS notifications")

    op.create_table(
        "notifications",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "recipient_uuid",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.players.uuid", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column(
            "payload",
            sa.dialects.postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        schema="notifications",
    )
    op.create_index(
        "ix_notifications_recipient_delivered",
        "notifications",
        ["recipient_uuid", "delivered_at"],
        schema="notifications",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_notifications_recipient_delivered",
        table_name="notifications",
        schema="notifications",
    )
    op.drop_table("notifications", schema="notifications")
    op.execute("DROP SCHEMA IF EXISTS notifications")
