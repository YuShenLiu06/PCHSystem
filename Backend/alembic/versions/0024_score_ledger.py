"""scoring.score_ledger（积分流水，积分层首批落地）

Revision ID: 0024_score_ledger
Revises: 0023_construction_report_events
Create Date: 2026-08-15

积分流水表——**append-only**（R-2）：只允许 INSERT，行级 UPDATE/DELETE 由触发器
``scoring.prevent_ledger_modify`` 强制拒绝（项目首个触发器）。``balance_after``
必填，流水链可审计重建余额与榜单（R-1 derived view）。

列语义（契约见 ``Docs/architecture/flows/scoring-settlement.md`` §6）：
- ``account_id``：归属锚 = Web 账号（R-5，离线改名换 UUID 积分不丢）。
  FK ``ondelete=RESTRICT``（非 CASCADE）：append-only 审计行绝不能被账号删除连坐抹掉。
- ``delta``：增减量，credit 恒正 / debit 恒负（CHECK ``delta <> 0``，方向由
  ``score_service.write_ledger`` 的 reason 方向守卫保证）。
- ``reason``：6 值枚举 CHECK——入账 ``collect``/``build_a``/``leader_bonus``/``settle``；
  出账 ``manual_adj``/``season_reset``。
- ``balance_after``：本条落账后余额，由 ``write_ledger`` 入口内计算（锁内 SELECT
  最新余额 + delta，禁止外部传入）。
- ``sheet_id``：弱引用（无 FK）——append-only 审计行不能被 CASCADE 连坐删，
  RESTRICT 又会反向阻塞 sheet 清理；存在性由 API 层校验。
- ``operator_uuid``：出账（manual_adj）时记录发起的管理员/服主 UUID。
- ``idempotency_key``：调用方防重放键（MCDR HTTP 重试，R-12）；作用域
  ``(account_id, idempotency_key)`` partial unique，同 key 同 payload 幂等回放。
- ``note``：运维备注（出账追溯「为什么扣」）。

触发器**只拦行级 UPDATE/DELETE，不拦 TRUNCATE**：测试基建（conftest
``_TRUNCATE_SQL``）依赖 TRUNCATE 清库；TRUNCATE 防护属运维权限范畴（R-2 的
「角色权限」半边），留待后续。
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID as PG_UUID


# revision identifiers, used by Alembic.
revision = '0024_score_ledger'
down_revision = '0023_construction_report_events'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS scoring")
    op.create_table(
        "score_ledger",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column(
            "account_id",
            sa.BigInteger(),
            sa.ForeignKey("users.web_accounts.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("delta", sa.Numeric(18, 2), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("balance_after", sa.Numeric(18, 2), nullable=False),
        sa.Column("sheet_id", sa.BigInteger(), nullable=True),
        sa.Column("operator_uuid", PG_UUID(as_uuid=True), nullable=True),
        sa.Column("idempotency_key", sa.Text(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_score_ledger"),
        sa.CheckConstraint("delta <> 0", name="ck_score_ledger_delta_nonzero"),
        sa.CheckConstraint(
            "reason IN ('collect','build_a','leader_bonus','settle',"
            "'manual_adj','season_reset')",
            name="ck_score_ledger_reason",
        ),
        schema="scoring",
    )
    # write_ledger 余额读取（最新一条 balance_after）+ /ledger 按玩家查询主索引
    op.create_index(
        "ix_score_ledger_account_id_desc",
        "score_ledger",
        ["account_id", sa.text("id DESC")],
        schema="scoring",
    )
    # 按原因回查（对账 / 赛季重置审计）
    op.create_index(
        "ix_score_ledger_reason",
        "score_ledger",
        ["reason"],
        schema="scoring",
    )
    # 按项目回查（settle 幂等查重 (account_id, sheet_id, reason='settle')）
    op.create_index(
        "ix_score_ledger_sheet",
        "score_ledger",
        ["sheet_id"],
        schema="scoring",
        postgresql_where=sa.text("sheet_id IS NOT NULL"),
    )
    # 幂等防重放：同账号同 key 唯一（partial——未带 key 的写入不受约束）
    op.create_index(
        "uq_score_ledger_idem",
        "score_ledger",
        ["account_id", "idempotency_key"],
        unique=True,
        schema="scoring",
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )
    # R-2 append-only：行级 UPDATE/DELETE 触发器强制拒绝（不拦 TRUNCATE，见文件头）
    op.execute(
        """
        CREATE FUNCTION scoring.prevent_ledger_modify() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'score_ledger is append-only (R-2): % forbidden', TG_OP;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER prevent_ledger_modify
        BEFORE UPDATE OR DELETE ON scoring.score_ledger
        FOR EACH ROW EXECUTE FUNCTION scoring.prevent_ledger_modify()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS prevent_ledger_modify ON scoring.score_ledger")
    op.execute("DROP FUNCTION IF EXISTS scoring.prevent_ledger_modify()")
    op.drop_index("uq_score_ledger_idem", schema="scoring", table_name="score_ledger")
    op.drop_index("ix_score_ledger_sheet", schema="scoring", table_name="score_ledger")
    op.drop_index("ix_score_ledger_reason", schema="scoring", table_name="score_ledger")
    op.drop_index(
        "ix_score_ledger_account_id_desc", schema="scoring", table_name="score_ledger"
    )
    op.drop_table("score_ledger", schema="scoring")
    op.execute("DROP SCHEMA IF EXISTS scoring")
