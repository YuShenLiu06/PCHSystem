#!/usr/bin/env python3
"""第三方客户端 · 批量提交测试。

演示对 `POST /sheets/{sheet_id}/submit-batch` 的两类集成方调用，对应
[`Docs/architecture/api/submit-extension.md`] §2 双鉴权：

- **服务端通道**（服务端 mod / 服主脚本，代玩家写）：`X-Service-Token` + `X-Player-UUID`
- **客户端通道**（玩家客户端 mod，只写自己）：`Authorization: Bearer <jwt>`

仅依赖 Python 标准库（urllib），无需 pip 装包。后端跑在 :8000 时直接可用。

用法：
  # 服务端通道（代玩家写，需玩家 UUID 已在后端库）
  python Scripts/test-submit-batch.py --sheet 1 \\
      --items minecraft:iron_ingot:10 minecraft:oak_log:64 \\
      --token svc --uuid <player_uuid>

  # 客户端通道（玩家 JWT，只能写自己）
  python Scripts/test-submit-batch.py --sheet 1 \\
      --items minecraft:iron_ingot:10 --auth jwt --jwt <access_jwt>

  # 造一张演示表 + 玩家（写入 pch-test-pg，便于立刻看到效果）
  python Scripts/test-submit-batch.py --seed
"""
import argparse
import json
import sys
import urllib.error
import urllib.request

DEFAULT_API = "http://localhost:8000"

# 回执 action → 标记（终端可读）
_MARK = {"delivered": "✅", "contributed": "➕", "skipped": "⏭️"}


def parse_items(specs: list[str]) -> list[dict]:
    """`registry_id:qty` → `{"registry_id", "qty"}`。"""
    items = []
    for spec in specs:
        rid, sep, qty = spec.rpartition(":")
        if not sep:
            sys.exit(f"bad item spec '{spec}'，需 registry_id:qty（如 minecraft:iron_ingot:10）")
        try:
            items.append({"registry_id": rid.strip(), "qty": int(qty)})
        except ValueError:
            sys.exit(f"bad qty in '{spec}'，需整数")
    return items


def call_submit_batch(api: str, sheet_id: int, items: list[dict], auth: dict) -> dict:
    """发 POST /sheets/{id}/submit-batch，返回解析后的 BatchSubmitResult。"""
    body = json.dumps({"items": items}).encode()
    headers = {"Content-Type": "application/json", **auth}
    url = f"{api.rstrip('/')}/sheets/{sheet_id}/submit-batch"
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")
        sys.exit(f"HTTP {e.code} {url}\n{detail}")
    except urllib.error.URLError as e:
        sys.exit(f"连不上 {url}：{e.reason}（后端起没？uvicorn 应在 :8000）")


def render_receipt(result: dict) -> None:
    """按行渲染回执（totals + outcomes）。"""
    totals = result.get("totals", {})
    print(
        f"sheet={result.get('sheet_id')} actor={result.get('actor_uuid')} "
        f"totals: 交付 {totals.get('delivered', 0)} / 上交 {totals.get('contributed', 0)} / 跳过 {totals.get('skipped', 0)}"
    )
    for o in result.get("outcomes", []):
        action = o.get("action")
        mark = _MARK.get(action, "?")
        mode = "lock" if o.get("mode") == 0 else "progress"
        line = (
            f"  {mark} row#{o.get('row_id')} {o.get('item_name')} "
            f"[{o.get('registry_id')}] {mode} → {action}"
        )
        if action != "skipped":
            line += f" qty={o.get('qty')} (累计 {o.get('delivered_qty')}/{o.get('need_qty')})"
        else:
            line += f" reason={o.get('reason')}"
        print(line)


def build_auth(args) -> dict:
    """按通道拼鉴权头。服务端通道双头（无 Authorization）；客户端通道 Bearer。"""
    if args.auth == "service":
        if not args.uuid:
            sys.exit("服务端通道需 --uuid <player_uuid>（X-Player-UUID）")
        return {"X-Service-Token": args.token, "X-Player-UUID": args.uuid}
    if not args.jwt:
        sys.exit("客户端通道需 --jwt <access_jwt>")
    return {"Authorization": f"Bearer {args.jwt}"}


def main() -> None:
    p = argparse.ArgumentParser(description="批量提交端点第三方客户端测试")
    p.add_argument("--api", default=DEFAULT_API, help=f"后端基址（默认 {DEFAULT_API}）")
    p.add_argument("--sheet", type=int, help="sheet_id")
    p.add_argument(
        "--items", nargs="+", metavar="RID:QTY",
        help="材料清单，形如 minecraft:iron_ingot:10（可多个，空格分隔）",
    )
    p.add_argument("--auth", choices=["service", "jwt"], default="service", help="鉴权通道")
    p.add_argument("--token", default="svc", help="服务端通道 X-Service-Token")
    p.add_argument("--uuid", help="服务端通道 X-Player-UUID")
    p.add_argument("--jwt", help="客户端通道 access JWT")
    p.add_argument(
        "--seed", action="store_true",
        help="造演示数据（玩家+表+lock 行+progress 行）写入 pch-test-pg，并打印调用示例",
    )
    args = p.parse_args()

    if args.seed:
        seed_demo()
        return

    if not args.sheet or not args.items:
        sys.exit("需 --sheet <id> 与 --items <rid:qty ...>（或 --seed 先造演示数据）")

    items = parse_items(args.items)
    auth = build_auth(args)
    result = call_submit_batch(args.api, args.sheet, items, auth)
    render_receipt(result)


def seed_demo() -> None:
    """造演示玩家 + 表 + 一 lock 行 + 一 progress 行（直连 pch-test-pg）。

    复用后端测试库（POSTGRES_HOST=localhost:5432, user=pch, password=pchtest, db=pchsystem）。
    幂等：同名玩家/表已存在则跳过。打印后续调用命令。
    """
    import asyncio
    import uuid as uuid_mod

    async def _run() -> None:
        # 经后端 ORM/seed helper 建数据，保证与生产路径一致（account 锚、registry_id 等）
        import os
        os.environ.setdefault("POSTGRES_PASSWORD", "pchtest")
        os.environ.setdefault("JWT_SECRET", "t")
        os.environ.setdefault("MCDR_SERVICE_TOKEN", "svc")
        from sqlalchemy.ext.asyncio import create_async_engine
        from sqlalchemy import text
        eng = create_async_engine(
            "postgresql+asyncpg://pch:pchtest@localhost:5432/pchsystem"
        )
        player_uuid = "00000000-0000-0000-0000-0000000000aa"
        async with eng.begin() as conn:
            # player（web_account_id=NULL；service-token 通道按 UUID 加载，
            # account_uuids 回退为 {player.uuid}，claimant 判定照常）
            await conn.execute(text(
                "INSERT INTO users.players(uuid, current_name, role, whitelist_state) "
                "VALUES (:u, 'DemoBatch', 'user', 'allowed') "
                "ON CONFLICT (uuid) DO UPDATE SET current_name='DemoBatch'"
            ), {"u": player_uuid})
            # sheet（owner = player_uuid，非归档）
            sid = (await conn.execute(text(
                "INSERT INTO sheets.sheets(owner_uuid, title, status) "
                "VALUES (:u, '批量提交演示表', 'collecting') "
                "ON CONFLICT DO NOTHING RETURNING id"
            ), {"u": player_uuid})).scalar()
            if sid is None:
                sid = (await conn.execute(text(
                    "SELECT id FROM sheets.sheets WHERE title='批量提交演示表' LIMIT 1"
                ))).scalar()
            # lock 行（认领人=player_uuid，need=10）
            await conn.execute(text(
                "INSERT INTO sheets.sheet_rows(sheet_id, item_name, registry_id, need_qty, mode, status, claimant_uuid, delivered_qty, sort_order) "
                "VALUES (:s, '铁锭', 'minecraft:iron_ingot', 10, 0, 'claimed', :u, 0, 0) "
                "ON CONFLICT DO NOTHING"
            ), {"s": sid, "u": player_uuid})
            # progress 行（need=100，未满）
            await conn.execute(text(
                "INSERT INTO sheets.sheet_rows(sheet_id, item_name, registry_id, need_qty, mode, status, delivered_qty, sort_order) "
                "VALUES (:s, '橡木原木', 'minecraft:oak_log', 100, 1, 'claimed', 0, 1) "
                "ON CONFLICT DO NOTHING"
            ), {"s": sid})
        await eng.dispose()
        print(f"✅ 演示数据就绪：player_uuid={player_uuid} sheet_id={sid}")
        print(f"   lock 行：铁锭 need=10（你已认领）；progress 行：橡木原木 need=100")
        print("现在跑：")
        print(
            f"   python Scripts/test-submit-batch.py --sheet {sid} "
            f"--items minecraft:iron_ingot:10 minecraft:oak_log:64 "
            f"--token svc --uuid {player_uuid}"
        )

    asyncio.run(_run())


if __name__ == "__main__":
    main()
