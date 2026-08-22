"""积分层 API（/v1/scoring）。

端点契约：
- ``POST /v1/scoring/credit`` / ``POST /v1/scoring/debit``：批量积分写入，
  **仅 service-token**（MCDR / 结算任务等系统调用；红线：不对 JWT 开放，
  玩家不能自助加减分）。同批逐条独立处理（单 session 单事务）：玩家不存在 /
  未绑 WebAccount / sheet 不存在 / 幂等键冲突 / 余额不足（debit 默认拒绝透支）
  逐条转 skip，不影响其他条目；全部处理完统一 commit，业务写 + 通知同事务原子
  （RS-9）。流水唯一写入口 = ``score_service.write_ledger``（``scoring.score_ledger``
  append-only，R-2/RS-2）。
- ``POST /v1/scoring/admin/adjust``：管理员（服主）积分调控，**仅特权 JWT**
  （admin/owner；积分管理面板经环境变量同步的托管账号登录调用）。**admin ≠
  service-token**——本端点不认 ``X-Service-Token``（系统组件记账走
  credit/debit；service-token 调用 → 401）；普通玩家 JWT → 403。与
  credit/debit 共用同一条批量管线，差异仅两点：reason 放开到全集（**方向由
  reason 符号定**——入账 reason 加、出账 reason 减，单端点双向）；
  ``allow_overdraft`` 语义同 debit（默认 False）。操作者经审计日志
  ``operator=jwt-account:<id>`` 标签记录。
- ``GET /v1/scoring/admin/players``：特权玩家联想（面板调分/筛选选人用，
  鉴权同 admin/adjust：仅特权 JWT）。
- ``GET /v1/scoring/admin/balances``：所有玩家（WebAccount）当前积分余额
  排名（面板「玩家积分」tab），鉴权同 admin/adjust：仅特权 JWT；
  balance = SUM(delta)（R-2 重建），排序 balance DESC + account_id 稳定序。
- ``GET /v1/scoring/ledger``：流水查询，**多角色**——service-token 或 admin/owner
  JWT 可查全局（可按 ``player_uuid`` 收敛到单账号，或 ``account_id`` 直按账号
  收敛——余额下钻入口，特权专用、与 ``player_uuid`` 互斥 422）；普通玩家
  JWT 只能查自身 account（他人 uuid → 403；传 ``account_id`` → 403，自账号
  放行为将来预留）。H-2：Authorization 头存在（即便非法）只走 JWT
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
from fastapi import APIRouter, Depends, HTTPException, Query, Security, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import app.api.deps as deps
from app.core.db import get_session
from app.core.jwt import decode_token
from app.models.scoring import ScoreLedger
from app.models.sheet import Sheet
from app.models.user import WebAccount
from app.repositories import player_repo, score_repo, web_account_repo
from app.schemas.player import PlayerBrief
from app.schemas.scoring import (
    AdminAdjustBatchRequest,
    CreditBatchRequest,
    DebitBatchRequest,
    ScoreBalanceRow,
    ScoreBalancesPage,
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


async def _account_from_authorization(
    authorization: str, session: AsyncSession
) -> WebAccount:
    """Authorization 头 → WebAccount（H-2：只走 JWT 通道，绝不降级 service-token）。

    Bearer 解析 → decode access token（sub=account_id）→ 查 WebAccount；
    各级失败均 401（文案与 ledger 既有契约一致）。
    """
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
    return account


async def require_ledger_access(
    x_service_token: str | None = Security(deps.service_token_scheme),
    authorization: str | None = Security(deps.bearer_scheme),
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

    account = await _account_from_authorization(authorization, session)
    if account.role in PRIVILEGED_ROLES:
        return LedgerAccess(is_privileged=True, account_id=None)
    return LedgerAccess(is_privileged=False, account_id=account.id)


# ---------------------------------------------------------------------------
# 特权写通道依赖（admin/adjust · admin/players）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PrivilegedAccess:
    """特权端点权限解析结果（admin/adjust / admin/players **仅特权 JWT**）。

    ``operator`` = ``jwt-account:<id>``（审计日志操作者标签，面板调用不传
    ``operator_uuid``，操作者经此标签记录）。
    """

    operator: str


async def require_privileged_access(
    authorization: str | None = Security(deps.bearer_scheme),
    session: AsyncSession = Depends(get_session),
) -> PrivilegedAccess:
    """特权端点鉴权（admin/adjust / admin/players）：**仅 admin/owner JWT**。

    **admin ≠ service-token**：管理端点不认 ``X-Service-Token``（系统组件
    记账走 credit/debit；service-token 调 admin 端点与无凭证同罪 → 401
    ``missing authorization``）。缺 ``Authorization`` → 401；解 token 查
    WebAccount（各级失败 401）；role ∈ {admin, owner} → 通过，**非特权
    角色 → 403**（与 ledger 的降级自查不同：写通道直接拒绝）。
    """
    if authorization is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "missing authorization"
        )
    account = await _account_from_authorization(authorization, session)
    if account.role not in PRIVILEGED_ROLES:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "forbidden")
    return PrivilegedAccess(operator=f"jwt-account:{account.id}")


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
    """accepted 条目落通知（同事务，RS-9：回滚则通知不落库）。

    note 非空时拼进文案（``reason: note``）；标点用 ASCII——notification
    入口的 ``_clean_text`` 白名单不含 U+FF00 全角符号区，全角括号/冒号会被
    剔除成连体文本。
    """
    title, category = (
        ("积分扣除", "scoring_debit") if is_debit else ("积分入账", "scoring_credit")
    )
    reason_text = f"{item.reason}: {item.note}" if item.note else item.reason
    body = f"{title} {entry.delta:+.2f}({reason_text}), 当前余额 {entry.balance_after:.2f}"
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
    operator: str | None = None,
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
    # 审计日志（结构化、不含 token；item 维度字段；operator = JWT 通道操作者标签）
    logger.info(
        "scoring_write path=%s account_id=%s reason=%s delta=%s replayed=%s operator=%s",
        path,
        player.web_account_id,
        item.reason,
        signed_delta,
        written.replayed,
        operator,
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
    operator: str | None = None,
) -> ScoreBatchResult:
    """逐条处理 + 单事务单 commit（在 model_validate 之后提交，避免序列化过期）。"""
    results = [
        await _process_one(
            session,
            item,
            path=path,
            notify=notify,
            allow_overdraft=allow_overdraft,
            operator=operator,
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


@router.post("/credit", response_model=ScoreBatchResult, summary="批量积分新增（仅 service-token）")
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


@router.post("/debit", response_model=ScoreBatchResult, summary="批量积分扣除（仅 service-token，可开透支）")
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


@router.post("/admin/adjust", response_model=ScoreBatchResult, summary="管理员积分调控（仅特权 JWT，方向由 reason 定）")
async def admin_adjust(
    body: AdminAdjustBatchRequest,
    access: PrivilegedAccess = Depends(require_privileged_access),
    session: AsyncSession = Depends(get_session),
) -> ScoreBatchResult:
    """管理员（服主）积分调控（**仅特权 JWT**：admin/owner——积分管理面板经
    环境变量同步的托管账号登录调用；普通玩家 JWT 403、service-token 401）。

    与 credit/debit 共用批量管线，差异：reason 放开全集（方向由 reason 符号定，
    单端点双向）；``allow_overdraft`` 语义同 debit。操作者经审计日志
    ``operator=jwt-account:<id>`` 标签记录（面板不传 ``operator_uuid``；
    ``note`` 由调用方按条提供）。
    """
    return await _process_batch(
        session,
        body.items,
        path="/v1/scoring/admin/adjust",
        notify=body.notify,
        allow_overdraft=body.allow_overdraft,
        operator=access.operator,
    )


@router.get("/admin/players", response_model=list[PlayerBrief], summary="特权玩家联想（面板选人）")
async def admin_search_players(
    _access: PrivilegedAccess = Depends(require_privileged_access),
    q: str = Query(default="", description="玩家名 / 昵称前缀（大小写不敏感，至少 1 字符）"),
    limit: int = Query(default=10, ge=1, le=20),
    session: AsyncSession = Depends(get_session),
) -> list[PlayerBrief]:
    """特权玩家联想（积分管理面板调分/筛选选人用；仅特权 JWT，鉴权同
    admin/adjust——service-token 401）。

    与 ``GET /players`` 同源（``player_repo.search_for_manager``）但走特权鉴权：
    托管 admin 账号无绑定玩家，调不了需玩家身份的 ``get_current_player`` 通道。
    仅返回已绑 WebAccount 的玩家。
    """
    players = await player_repo.search_for_manager(session, q, limit)
    display_names = await web_account_repo.resolve_display_names(
        session, [p.uuid for p in players]
    )
    return [
        PlayerBrief(
            player_uuid=p.uuid,
            player_name=p.current_name,
            display_name=display_names.get(p.uuid, p.current_name),
        )
        for p in players
    ]


# ---------------------------------------------------------------------------
# GET /admin/balances
# ---------------------------------------------------------------------------


@router.get("/admin/balances", response_model=ScoreBalancesPage, summary="全账号余额排名（玩家积分 tab）")
async def admin_balances(
    _access: PrivilegedAccess = Depends(require_privileged_access),
    session: AsyncSession = Depends(get_session),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
) -> ScoreBalancesPage:
    """所有玩家当前积分余额排名（积分管理面板「玩家积分」tab）。

    仅特权 JWT（鉴权同 admin/adjust：无凭证/service-token → 401、普通玩家
    403）。行 = 有绑定玩家的 WebAccount（R-5 归属锚）；``balance`` = SUM(delta)
    （R-2 append-only 重建，与最新 balance_after 恒一致）；排序 balance DESC +
    account_id 稳定序。聚合与 display_name 回退链细节见
    ``score_repo.list_balances``。
    """
    rows, total = await score_repo.list_balances(session, page=page, limit=limit)
    return ScoreBalancesPage(
        items=[
            ScoreBalanceRow(
                account_id=r.account_id,
                display_name=r.display_name,
                player_names=list(r.player_names),
                balance=r.balance,
                entries_count=r.entries_count,
                last_entry_at=r.last_entry_at,
            )
            for r in rows
        ],
        total=total,
        page=page,
        limit=limit,
    )


# ---------------------------------------------------------------------------
# GET /ledger
# ---------------------------------------------------------------------------


async def _resolve_scope(
    session: AsyncSession,
    access: LedgerAccess,
    player_uuid: UUID | None,
    account_id: int | None = None,
) -> int | None:
    """ledger 作用域解析：

    - 特权 + 无过滤 → 全局（None）；特权 + ``player_uuid`` → 解析目标账号
      （玩家不存在/未绑定 → 404）；特权 + ``account_id`` → 账号存在性校验后
      按该账号收敛（不存在 → 404；余额下钻入口）。
    - 普通 + 无过滤 → 自身 account；普通 + ``player_uuid`` → 解析后 ≠ 自身
      → 403；普通 + ``account_id`` → 403（显式拒绝优于静默忽略；
      ``account_id`` = 自身账号时放行为**将来预留语义，暂未实现**）。
    - ``player_uuid`` 与 ``account_id`` 互斥（语义重叠）→ 422。
    """
    if player_uuid is not None and account_id is not None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "player_uuid and account_id are mutually exclusive",
        )
    if account_id is not None:
        if not access.is_privileged:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "forbidden")
        if await session.get(WebAccount, account_id) is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "account not found")
        return account_id
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


@router.get("/ledger", response_model=ScoreLedgerPage, summary="积分流水分页查询（多角色）")
async def list_ledger(
    access: LedgerAccess = Depends(require_ledger_access),
    session: AsyncSession = Depends(get_session),
    player_uuid: UUID | None = None,
    account_id: int | None = Query(default=None, ge=1),
    since: datetime | None = None,
    until: datetime | None = None,
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
) -> ScoreLedgerPage:
    """积分流水查询（多角色，见 ``require_ledger_access``）。

    作用域过滤：``player_uuid``（解析玩家 → 账号）或 ``account_id``（特权
    专用直按账号收敛，余额下钻入口；与 ``player_uuid`` 互斥 422；普通玩家
    传此参数 403——自账号放行为将来预留）。时间过滤：``since`` 起（>=）、
    ``until`` 止（<，开区间）；排序 id DESC、分页语义由
    ``score_repo.list_entries`` 保证。
    """
    scope_account_id = await _resolve_scope(session, access, player_uuid, account_id)
    entries, total = await score_repo.list_entries(
        session,
        account_id=scope_account_id,
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
