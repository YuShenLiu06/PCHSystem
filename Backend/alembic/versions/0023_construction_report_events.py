"""construction.report_events（玩家可见上报事件流水，迭代 5）

Revision ID: 0023_construction_report_events
Revises: 0022_clamp_overcap_net
Create Date: 2026-07-28

玩家可见的完整上报事件流水（``accepted`` + **所有 skip 原因**）。``submit_report``
逐条循环产出的 ``PlacementOutcome`` 都落一行（仅 bound 玩家：未绑 Web 账号 /
玩家不存在 → 不落，因 ``account_id`` 是 ``/me/report-events`` 查询锚）。

与 ``placement_snapshots`` 的区别：
- ``snapshots``：每次 report 成功后为本轮 accepted 的 account 各写一条累计净放置
  （折线图数据源）；不含 skip 信息。
- ``report_events``：逐条 outcome 落一行（accepted + 所有 skip），玩家个人时间线
  「我的上报历史」消费——让玩家看到「为什么我的上报被拒」。

列：
- ``sheet_id``：归因失败 / 客户端 mod 全局关闭等场景可能为 None → nullable。
- ``account_id``：仅 bound 玩家落事件（NOT NULL，``/me/report-events`` 查询锚）。
- ``registry_id``：理论上必有（outcome 都带）；允许 null 防御性。
- ``action``：``accepted`` | ``skipped``（与 ``PlacementOutcome.action`` 同口径）。
- ``reason``：accepted=""；skipped 为中文 reason（如 ``方块不在项目材料清单内``）。
- ``net_delta``：accepted=本次计入；skipped=被拒/尝试量（部分接受场景的 over 部分）。
- ``recorded_at``：默认 ``now()``，按时间倒序供时间线消费。

R-1：落 PostgreSQL（后端独占）；R-5：``account_id`` 锚 Web 账号。Append-only（无
UPDATE/DELETE 端点），与 ``placement_snapshots`` 同为展示用辅助表（权威源仍是
``placement_records``）。
"""
import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision = '0023_construction_report_events'
down_revision = '0022_clamp_overcap_net'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "report_events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column(
            "sheet_id",
            sa.BigInteger(),
            sa.ForeignKey("sheets.sheets.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "account_id",
            sa.BigInteger(),
            sa.ForeignKey("users.web_accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("player_uuid", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("registry_id", sa.Text(), nullable=True),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("net_delta", sa.Integer(), nullable=True),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_report_events"),
        sa.CheckConstraint(
            "action IN ('accepted', 'skipped')",
            name="ck_report_events_action",
        ),
        schema="construction",
    )
    # /me/report-events 查询：按 account 倒序取最近 N 条
    op.create_index(
        "ix_report_events_account_time",
        "report_events",
        ["account_id", sa.text("recorded_at DESC")],
        schema="construction",
    )
    # 按 sheet 回查（预留：项目维度审计 / 归档产物消费）
    op.create_index(
        "ix_report_events_sheet_time",
        "report_events",
        ["sheet_id", "recorded_at"],
        schema="construction",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_report_events_sheet_time",
        schema="construction",
        table_name="report_events",
    )
    op.drop_index(
        "ix_report_events_account_time",
        schema="construction",
        table_name="report_events",
    )
    op.drop_table("report_events", schema="construction")
