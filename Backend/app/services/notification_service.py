"""统一通知 service（通知抽象层入口）。

契约（详见 ``Docs/architecture/services/notification-service.md``）：
- ``notify(session, ...)`` **必须**在调用方（写端点）事务的同一 session 内调用，
  保证「业务改库 + 记通知」原子；事务回滚则通知不落库（R-10 单库事务一致性）。
- ``category`` 用 String，按 ``<domain>_<event>`` 命名扩展（首期 sheets_*）。
- ``Notifier`` Protocol 为投递通道扩展点：首期 ``DbNotifier`` no-op（落库即可被拉取）；
  预留 ``WebhookNotifier`` / ``DiscordNotifier``。
- 入口对 title/body 做限长 + 控制字符清洗，payload 序列化后 >8KB 截断（M-2/M-3）。
"""
import json
from typing import Any, Protocol, runtime_checkable
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.notification import Notification
from app.repositories import notification_repo

_TITLE_MAX = 200
_BODY_MAX = 500
_PAYLOAD_MAX_BYTES = 8 * 1024


def _clean_text(value: str, limit: int) -> str:
    """剔除控制字符，保留：\\n、可见 ASCII(0x20-0x7e)、常用 CJK 区间
    （全角符号/兼容 0xa0-0x24ff、CJK 扩展A 0x3400-0x4dbf、基本汉字 0x4e00-0x9fff、
    兼容表意 0xf900-0xfaff），再截断到 limit。"""
    cleaned_chars = []
    for ch in value:
        o = ord(ch)
        if (
            ch == "\n"
            or (0x20 <= o < 0x7F)
            or (0xA0 <= o <= 0x24FF)
            or (0x3400 <= o <= 0x4DBF)
            or (0x4E00 <= o <= 0x9FFF)
            or (0xF900 <= o <= 0xFAFF)
        ):
            cleaned_chars.append(ch)
    return "".join(cleaned_chars)[:limit]


def _clamp_payload(payload: dict[str, Any] | None) -> dict[str, Any]:
    """payload 序列化后超过 8KB 则截断为 {truncated: True, original_size}（防爆库）。"""
    if payload is None:
        return {}
    serialized = json.dumps(payload, ensure_ascii=False)
    if len(serialized.encode("utf-8")) <= _PAYLOAD_MAX_BYTES:
        return payload
    return {"truncated": True, "original_size_bytes": len(serialized)}


@runtime_checkable
class Notifier(Protocol):
    """投递通道扩展点。落库已由 service 负责，Notifier 负责额外通道（如 webhook）。"""

    def notify(self, record: Notification) -> None:  # pragma: no cover - 协议
        ...


class DbNotifier:
    """首期 no-op：通知已落库即可被 ``GET /notifications/pending`` 拉取。"""

    def notify(self, record: Notification) -> None:
        return None


# 预留扩展点：未来加 WebhookNotifier / DiscordNotifier 到此列表即可生效。
NOTIFIERS: list[Notifier] = [DbNotifier()]


async def notify(
    session: AsyncSession,
    recipient_uuid: UUID,
    category: str,
    title: str,
    body: str,
    payload: dict[str, Any] | None = None,
) -> Notification:
    """落库一条通知（同调用方事务）并触发已注册 Notifier。

    不 commit：由调用方在写端点成功路径同一事务内 commit；若调用方 rollback，
    本条通知随之回滚（一致性，R-10）。
    """
    record = await notification_repo.create(
        session,
        recipient_uuid=recipient_uuid,
        category=category,
        title=_clean_text(title, _TITLE_MAX),
        body=_clean_text(body, _BODY_MAX),
        payload=_clamp_payload(payload),
    )
    # flush 后 record.id 已就绪，供 Notifier 使用（如 webhook 推送结构化负载）
    for notifier in NOTIFIERS:
        notifier.notify(record)
    return record


async def fetch_pending(
    session: AsyncSession, recipient_uuids: UUID | list[UUID], limit: int = 50
) -> list[Notification]:
    """拉取未投递通知。``recipient_uuids`` 接受单 UUID（向后兼容）或列表（账号级聚合）。"""
    if isinstance(recipient_uuids, UUID):
        uuids: list[UUID] = [recipient_uuids]
    else:
        uuids = list(recipient_uuids)
    return await notification_repo.fetch_pending(session, uuids, limit)


async def mark_delivered(
    session: AsyncSession,
    ids: list[int],
    recipient_uuids: UUID | list[UUID],
) -> int:
    """标投递：接受单 UUID（向后兼容）或列表（账号级聚合）。"""
    return await notification_repo.mark_delivered(session, ids, recipient_uuids)


async def mark_read(
    session: AsyncSession,
    notification_id: int,
    recipient_uuids: UUID | list[UUID],
) -> bool:
    """标已读：接受单 UUID（向后兼容）或列表（账号级聚合）。"""
    return await notification_repo.mark_read(session, notification_id, recipient_uuids)


async def fetch_by_id_or_none(
    session: AsyncSession,
    recipient_id: int,
    recipient_uuids: UUID | list[UUID],
) -> Notification | None:
    """读取单条并校验归属（防越权 read 返回他人通知）。

    接受单 UUID 或列表；归属列表中任一 UUID 即放行。
    """
    record = await notification_repo.get_by_id(session, recipient_id)
    if record is None:
        return None
    if isinstance(recipient_uuids, UUID):
        allowed: list[UUID] = [recipient_uuids]
    else:
        allowed = list(recipient_uuids)
    if record.recipient_uuid not in allowed:
        return None
    return record
