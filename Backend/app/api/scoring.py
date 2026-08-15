"""积分层 API（/v1/scoring）。

端点契约：
- ``POST /v1/scoring/credit`` / ``POST /v1/scoring/debit``：批量积分写入，
  **仅 service-token**（MCDR / 结算任务等系统调用；红线：不对 JWT 开放，
  玩家不能自助加减分）。同批逐条独立处理（单 session 单事务）：玩家不存在 /
  未绑 WebAccount / sheet 不存在 / 幂等键冲突 / 余额不足（debit 默认拒绝透支）
  逐条转 skip，不影响其他条目；全部处理完统一 commit，业务写 + 通知同事务原子
  （RS-9）。流水唯一写入口 = ``score_service.write_ledger``（``scoring.score_ledger``
  append-only，R-2/RS-2）。
- ``POST /v1/scoring/admin/adjust``：管理员（服主）积分调控，**仅 service-token**
  （admin 面板 / 服主脚本持 token 调用；面板经环境变量配置，不对 JWT 开放）。
  与 credit/debit 共用同一条批量管线，差异仅两点：reason 放开到全集
  （**方向由 reason 符号定**——入账 reason 加、出账 reason 减，单端点双向）；
  ``allow_overdraft`` 语义同 debit（默认 False）。
- ``GET /v1/scoring/ledger``：流水查询，**多角色**——service-token 或 admin/owner
  JWT 可查全局（可按 ``player_uuid`` 收敛到单账号）；普通玩家 JWT 只能查自身
  account（他人 uuid → 403）。H-2：Authorization 头存在（即便非法）只走 JWT
  通道报 401，绝不静默降级 service-token。

service-token 对比统一引用 ``app.api.deps._settings``（调用时取模块属性，
不本地缓存 settings），保证测试对 ``deps._settings`` 的 patch 生效。
"""
import logging
import secrets
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

import jwt as pyjwt
from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import app.api.deps as deps
from app.core.db import get_session
from app.core.jwt import decode_token
from app.models.scoring import ScoreLedger
from app.models.sheet import Sheet
from app.models.user import WebAccount
from app.repositories import player_repo, score_repo
from app.schemas.scoring import (
    AdminAdjustBatchRequest,
    CreditBatchRequest,
    DebitBatchRequest,
    ScoreBatchResult,
    ScoreItem,
    ScoreItemResult,
    ScoreLedgerEntry,
    ScoreLedgerPage,
)
from app.services import notification_service, score_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/scoring", tags=["scoring"])

PRIVILEGED_ROLES = {"admin", "owner"}


# ---------------------------------------------------------------------------
# GET /ledger 多角色权限依赖
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LedgerAccess:
    """ledger 查询权限解析结果。

    - 特权（service-token / admin·owner JWT）：``is_privileged=True``，
      ``account_id=None`` 表示作用域待定（无 player_uuid → 全局）。
    - 普通玩家 JWT：``is_privileged=False``，``account_id`` = 自身账号
      （ledger 归属锚 = WebAccount，R-5）。
    """

    is_privileged: bool
    account_id: int | None


async def require_ledger_access(
    x_service_token: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
    session: AsyncSession = Depends(get_session),
) -> LedgerAccess:
    """ledger 多角色鉴权：

    ① 无 ``Authorization``：校验 ``X-Service-Token``（``secrets.compare_digest``
       对比 ``deps._settings.mcdr_service_token``，参照 ``deps.require_service_token``）
       → 特权；缺失/错误 → 401。
    ② 有 ``Authorization``（H-2 只走 JWT 不降级）：解 access token（sub=account_id）
       → 查 WebAccount（不存在 401）；role ∈ {admin, owner} → 特权，
       否则普通（限自身 account）。
    """
    if authorization is None:
        if not x_service_token or not secrets.compare_digest(
            x_service_token, deps._settings.mcdr_service_token
        ):
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED, "invalid service token"
            )
        return LedgerAccess(is_privileged=True, account_id=None)

    if not authorization.startswith("Bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing bearer")
    token = authorization.removeprefix("Bearer ").strip()
    try:
        payload = decode_token(token)
    except pyjwt.PyJWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid token")
    if payload.get("type") != "access":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "wrong token type")
    sub = payload.get("sub")
    if not isinstance(sub, str):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid token subject")
    try:
        account_id = int(sub)
    except ValueError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid token subject")
    account = (
        await session.execute(select(WebAccount).where(WebAccount.id == account_id))
    ).scalar_one_or_none()
    if account is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "account not found")
    if account.role in PRIVILEGED_ROLES:
        return LedgerAccess(is_privileged=True, account_id=None)
    return LedgerAccess(is_privileged=False, account_id=account.id)


# ---------------------------------------------------------------------------
# 批量写（credit / debit 共用逐条处理）
# ---------------------------------------------------------------------------


def _skip(player_uuid: UUID, reason: str) -> ScoreItemResult:
    """构造单条 skip 结果。"""
    return ScoreItemResult(
        player_uuid=player_uuid, accepted=False, skip_reason=reason
    )


async def _notify_score_change(
    session: AsyncSession, item: ScoreItem, entry: ScoreLedger, *, is_debit: bool
) -> None:
    """accepted 条目落通知（同事务，RS-9：回滚则通知不落库）。"""
    title, category = (
        ("积分扣除", "scoring_debit") if is_debit else ("积分入账", "scoring_credit")
    )
    body = f"{title} {entry.delta:+.2f}（{item.reason}），当前余额 {entry.balance_after:.2f}"
    payload = {
        "amount": str(item.amount),
        "reason": item.reason,
        "balance_after": str(entry.balance_after),
        "sheet_id": item.sheet_id,
        "operator_uuid": (
            str(item.operator_uuid) if item.operator_uuid is not None else None
        ),
    }
    await notification_service.notify(
        session, item.player_uuid, category, title, body, payload
    )


async def _process_one(
    session: AsyncSession,
    item: ScoreItem,
    *,
    path: str,
    notify: bool,
    allow_overdraft: bool,
) -> ScoreItemResult:
    """单条 item 六步处理（契约 ①~⑤ + 审计日志）；未预期异常不捕（500 回滚）。

    delta 方向统一由 reason 符号决定（``LEDGER_REASON_SIGN``）：credit/debit
    端点的 reason 枚举本就按方向收紧，与端点语义一致；admin/adjust 放开全集。
    """
    # ① 玩家存在性
    player = await player_repo.get_by_uuid(session, item.player_uuid)
    if player is None:
        return _skip(item.player_uuid, "player not found")
    # ② 绑定校验（积分归属锚 = WebAccount，R-5）
    if player.web_account_id is None:
        return _skip(item.player_uuid, "player not bound to a web account")
    # ③ 可选关联 sheet 存在性
    if item.sheet_id is not None:
        found_sheet = (
            await session.execute(select(Sheet.id).where(Sheet.id == item.sheet_id))
        ).scalar_one_or_none()
        if found_sheet is None:
            return _skip(item.player_uuid, "sheet not found")
    # ④ 写流水（唯一写入口；delta 符号 = reason 方向）
    is_debit = score_service.LEDGER_REASON_SIGN[item.reason] == -1
    signed_delta = -item.amount if is_debit else item.amount
    try:
        written = await score_service.write_ledger(
            session,
            account_id=player.web_account_id,
            delta=signed_delta,
            reason=item.reason,
            sheet_id=item.sheet_id,
            operator_uuid=item.operator_uuid,
            idempotency_key=item.idempotency_key,
            note=item.note,
            allow_overdraft=allow_overdraft,
        )
    except score_service.ScoreIdempotencyConflict:
        return _skip(item.player_uuid, "idempotency key conflict")
    except score_service.InsufficientBalance:
        return _skip(item.player_uuid, "insufficient balance")
    # ⑤ 通知（accepted 且请求开启；幂等重放抑制——MCDR 重试语义下副作用不重复；同事务）
    if notify and not written.replayed:
        await _notify_score_change(
            session, item, written.entry, is_debit=is_debit
        )
    # 审计日志（结构化、不含 token；item 维度字段）
    logger.info(
        "scoring_write path=%s account_id=%s reason=%s delta=%s replayed=%s",
        path,
        player.web_account_id,
        item.reason,
        signed_delta,
        written.replayed,
    )
    return ScoreItemResult(
        player_uuid=item.player_uuid,
        accepted=True,
        entry=ScoreLedgerEntry.model_validate(written.entry),
        idempotent_replay=written.replayed,
    )


async def _process_batch(
    session: AsyncSession,
    items: Sequence[ScoreItem],
    *,
    path: str,
    notify: bool,
    allow_overdraft: bool,
) -> ScoreBatchResult:
    """逐条处理 + 单事务单 commit（在 model_validate 之后提交，避免序列化过期）。"""
    results = [
        await _process_one(
            session,
            item,
            path=path,
            notify=notify,
            allow_overdraft=allow_overdraft,
        )
        for item in items
    ]
    accepted_count = sum(1 for r in results if r.accepted)
    await session.commit()
    return ScoreBatchResult(
        results=results,
        accepted_count=accepted_count,
        skipped_count=len(results) - accepted_count,
    )


@router.post("/credit", response_model=ScoreBatchResult)
async def credit_batch(
    body: CreditBatchRequest,
    _ok: None = Depends(deps.require_service_token),
    session: AsyncSession = Depends(get_session),
) -> ScoreBatchResult:
    """批量积分入账（仅 service-token；credit 恒为正 delta，无透支概念）。"""
    return await _process_batch(
        session,
        body.items,
        path="/v1/scoring/credit",
        notify=body.notify,
        allow_overdraft=False,
    )


@router.post("/debit", response_model=ScoreBatchResult)
async def debit_batch(
    body: DebitBatchRequest,
    _ok: None = Depends(deps.require_service_token),
    session: AsyncSession = Depends(get_session),
) -> ScoreBatchResult:
    """批量积分扣除（仅 service-token；默认拒绝透支，可显式 allow_overdraft）。"""
    return await _process_batch(
        session,
        body.items,
        path="/v1/scoring/debit",
        notify=body.notify,
        allow_overdraft=body.allow_overdraft,
    )


@router.post("/admin/adjust", response_model=ScoreBatchResult)
async def admin_adjust(
    body: AdminAdjustBatchRequest,
    _ok: None = Depends(deps.require_service_token),
    session: AsyncSession = Depends(get_session),
) -> ScoreBatchResult:
    """管理员（服主）积分调控（仅 service-token；admin 面板 / 服主脚本用，
    面板经环境变量配置凭证，不对 JWT 开放）。

    与 credit/debit 共用批量管线，差异：reason 放开全集（方向由 reason 符号定，
    单端点双向）；``allow_overdraft`` 语义同 debit。审计字段 ``operator_uuid`` /
    ``note`` 由调用方按条提供（面板侧应记录操作者）。
    """
    return await _process_batch(
        session,
        body.items,
        path="/v1/scoring/admin/adjust",
        notify=body.notify,
        allow_overdraft=body.allow_overdraft,
    )


# ---------------------------------------------------------------------------
# GET /ledger
# ---------------------------------------------------------------------------


async def _resolve_scope(
    session: AsyncSession, access: LedgerAccess, player_uuid: UUID | None
) -> int | None:
    """ledger 作用域解析：

    - 特权 + 无 uuid → 全局（None）；特权 + 有 uuid → 解析目标账号
      （玩家不存在/未绑定 → 404）。
    - 普通 + 无 uuid → 自身 account；普通 + 有 uuid → 解析后 ≠ 自身 → 403。
    """
    if player_uuid is None:
        return None if access.is_privileged else access.account_id
    player = await player_repo.get_by_uuid(session, player_uuid)
    if player is None or player.web_account_id is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "player not found")
    if not access.is_privileged and player.web_account_id != access.account_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "forbidden")
    return player.web_account_id


def _as_utc(value: datetime) -> datetime:
    """naive datetime 一律按 UTC 解释（DB 列为 timestamptz；aware 原样返回）。"""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


@router.get("/ledger", response_model=ScoreLedgerPage)
async def list_ledger(
    access: LedgerAccess = Depends(require_ledger_access),
    session: AsyncSession = Depends(get_session),
    player_uuid: UUID | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
) -> ScoreLedgerPage:
    """积分流水查询（多角色，见 ``require_ledger_access``）。

    时间过滤：``since`` 起（>=）、``until`` 止（<，开区间）；排序 id DESC、
    分页语义由 ``score_repo.list_entries`` 保证。
    """
    account_id = await _resolve_scope(session, access, player_uuid)
    entries, total = await score_repo.list_entries(
        session,
        account_id=account_id,
        since=_as_utc(since) if since is not None else None,
        until=_as_utc(until) if until is not None else None,
        page=page,
        limit=limit,
    )
    return ScoreLedgerPage(
        items=[ScoreLedgerEntry.model_validate(e) for e in entries],
        total=total,
        page=page,
        limit=limit,
    )
