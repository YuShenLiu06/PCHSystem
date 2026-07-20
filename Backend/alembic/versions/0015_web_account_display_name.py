"""Web 账号自定义昵称 display_name（sheets 多 UUID 统一：显示名主源）

为 users.web_accounts 增加 display_name（玩家自定义昵称）。sheets 三端
（前端 / MCDR / 归档 md）在「同一 Web 账号多 UUID 统一」后，owner / claimant /
contributor / 通知 actor 的显示名统一取此字段；为空时由应用层回退到该账号下
last_seen_at 最新 UUID 的 current_name（player_repo 维护，NOT NULL DEFAULT now()）。

注意：本迁移只动 users.web_accounts；sheets 业务表（owner_uuid / claimant_uuid /
sheet_row_contributors.player_uuid）零改动——多 UUID 统一全部在查询/权限/显示层完成
（与 aggregate_contributor_totals 现有 GROUP BY COALESCE(web_account_id, uuid) 一脉相承）。

Revision ID: 0015_web_account_display_name
Revises: 0014_web_accounts_bind
Create Date: 2026-07-19
"""
from alembic import op


# revision identifiers, used by Alembic.
revision = '0015_web_account_display_name'
down_revision = '0014_web_accounts_bind'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # display_name 仅展示用（被 SELECT），非检索/JOIN 键 → 不加索引（省写入开销）。
    # CHECK：空串视同 NULL（应用层 COALESCE 一致性，避免 btrim 后空串污染显示）。
    op.execute("""
        ALTER TABLE users.web_accounts
            ADD COLUMN display_name text NULL,
            ADD CONSTRAINT ck_web_accounts_display_name_not_blank
                CHECK (display_name IS NULL OR length(btrim(display_name)) > 0);
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE users.web_accounts
            DROP CONSTRAINT ck_web_accounts_display_name_not_blank,
            DROP COLUMN display_name;
    """)
