#!/usr/bin/env python3
"""施工进度上报端到端测试脚本（兼上报源参考实现）。

替代尚未实现的 MCDR 默认方块追踪器（construction-progress.md §5，待 S-1 单独 PR），
做 ``POST /v1/construction/report`` 端到端验证，并演示上报源如何调用 API。

零三方依赖（仅 Python stdlib）—— 便于二次开发者直接参考移植到任意语言。

用法：
    python3 Scripts/test-construction-report.py [--api URL] [--env .env]

默认 API = http://localhost:8002（worktree 独立栈端口）。从 .env 读
``MCDR_SERVICE_TOKEN``（service-token 通道）+ ``JWT_SECRET``（铸 mod-token JWT）。

流程：
  1. bootstrap：经 ``/auth/token``（service-token 代建）+ ``/auth/exchange`` 自举
     2 个玩家 + 1 个 constructing 项目（owner JWT 建表 + advance）。
  2. service-token 多玩家上报（mcdr/official 源）→ 打印 outcomes。
  3. ``GET /active-sheets`` 启发式归因查询。
  4. 玩家切到 local + JWT[mod_id] 上报（演示客户端 mod 通道）。
  5. ``GET /{sheet_id}/progress`` 进度查询。
  6. 场景 5（迭代 2）：建空清单 sheet → 上报清单外方块（minecraft:dirt）→ 期望
     ``outcomes[0].action=='skipped'`` 且 ``reason=='方块不在项目材料清单内'``。
  7. 场景 6（迭代 2）：再次查 progress 验证响应含 ``material_completion``（材料完成度）
     + ``timeline``（时序快照）两新字段，并打印展示。

鉴权契约（C-1/C-3/C-9/C-10，详见 Docs/architecture/api/construction.md）。
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid


# ===========================================================================
# HTTP + JWT 工具（stdlib only）
# ===========================================================================

def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def mint_jwt(secret: str, claims: dict) -> str:
    """手铸 HS256 JWT（参考实现；真实客户端用 pyjwt 等库）。"""
    header = {"alg": "HS256", "typ": "JWT"}
    now = int(time.time())
    payload = {
        **claims,
        "iat": now,
        "exp": now + 3600,
        "jti": str(uuid.uuid4()),
    }
    h = _b64url(json.dumps(header, separators=(",", ":")).encode())
    p = _b64url(json.dumps(payload, separators=(",", ":")).encode())
    sig = hmac.new(secret.encode(), f"{h}.{p}".encode(), hashlib.sha256).digest()
    return f"{h}.{p}.{_b64url(sig)}"


def decode_jwt_payload(token: str) -> dict:
    """解 JWT payload（不验签 —— 仅为取 sub/active_uuid 铸 mod-token）。"""
    p = token.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(p + "=" * (-len(p) % 4)))


def http_request(
    method: str,
    url: str,
    *,
    body: dict | None = None,
    headers: dict | None = None,
    timeout: float = 10.0,
) -> tuple[int, dict | str]:
    """发 JSON 请求，返 (status, parsed_json_or_text)。"""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url, data=data, method=method, headers={"Content-Type": "application/json", **(headers or {})}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode()
            try:
                return resp.status, json.loads(raw)
            except json.JSONDecodeError:
                return resp.status, raw
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, raw


# ===========================================================================
# .env 读取（最小实现，不引 python-dotenv）
# ===========================================================================

def load_env(path: str) -> dict[str, str]:
    env: dict[str, str] = {}
    if not os.path.exists(path):
        return env
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env


# ===========================================================================
# 主流程
# ===========================================================================

def main() -> int:
    ap = argparse.ArgumentParser(description="施工进度上报端到端测试脚本")
    ap.add_argument("--api", default="http://localhost:8002", help="后端 API 根地址")
    ap.add_argument("--env", default=None, help=".env 路径（默认沿目录向上找）")
    args = ap.parse_args()

    # 找 .env
    env_path = args.env
    if env_path is None:
        cur = os.path.dirname(os.path.abspath(__file__))
        for _ in range(5):
            cand = os.path.join(cur, ".env")
            if os.path.exists(cand):
                env_path = cand
                break
            cur = os.path.dirname(cur)
    env = load_env(env_path or "")
    svc_token = env.get("MCDR_SERVICE_TOKEN") or os.environ.get("MCDR_SERVICE_TOKEN")
    jwt_secret = env.get("JWT_SECRET") or os.environ.get("JWT_SECRET")
    if not svc_token:
        print("✗ 未找到 MCDR_SERVICE_TOKEN（.env 或环境变量）", file=sys.stderr)
        return 2

    api = args.api.rstrip("/")
    svc_h = {"X-Service-Token": svc_token}
    print(f"=== 施工上报测试脚本 → {api} ===\n")

    # --- 1. bootstrap：建 2 玩家 + 1 constructing 项目 ---
    p1_uuid = uuid.uuid4()
    p2_uuid = uuid.uuid4()

    def bootstrap_player(puuid: uuid.UUID, name: str) -> str:
        """/auth/token（建玩家）→ /auth/exchange → 返 access JWT。"""
        code, body = http_request(
            "POST", f"{api}/auth/token",
            body={"uuid": str(puuid), "name": name},
            headers=svc_h,
        )
        if code != 200:
            raise SystemExit(f"✗ /auth/token 失败 {code}: {body}")
        login_token = body["token"] if "token" in body else body.get("login_url", "").split("token=")[-1]
        code, body = http_request("POST", f"{api}/auth/exchange", body={"token": login_token})
        if code != 200:
            raise SystemExit(f"✗ /auth/exchange 失败 {code}: {body}")
        return body["access_token"]

    p1_jwt = bootstrap_player(p1_uuid, "Alice")
    p2_jwt = bootstrap_player(p2_uuid, "Bob")
    print(f"✓ 已建玩家 Alice={p1_uuid}  Bob={p2_uuid}")

    # 建表 + advance → constructing
    code, body = http_request(
        "POST", f"{api}/sheets",
        body={"title": "施工上报测试项目"},
        headers={"Authorization": f"Bearer {p1_jwt}"},
    )
    assert code == 201, (code, body)
    sheet_id = body["id"]
    code, _ = http_request(
        "POST", f"{api}/sheets/{sheet_id}/advance?to=constructing",
        headers={"Authorization": f"Bearer {p1_jwt}"},
    )
    assert code == 200, (code, _)
    print(f"✓ 已建施工中项目 id={sheet_id}\n")

    # --- 2. service-token 多玩家上报 ---
    print("--- 场景 1：service-token 多玩家上报（mcdr/official 源）---")
    code, body = http_request(
        "POST", f"{api}/v1/construction/report",
        body={
            "sheet_id": sheet_id,
            "placements": [
                {"player_uuid": str(p1_uuid), "registry_id": "minecraft:stone", "placed_qty": 32, "broken_qty": 4},
                {"player_uuid": str(p2_uuid), "registry_id": "minecraft:oak_log", "placed_qty": 16, "broken_qty": 0},
            ],
        },
        headers=svc_h,
    )
    print(f"HTTP {code}  attribution={body.get('attribution_source')}  totals={body.get('totals')}")
    for o in body.get("outcomes", []):
        print(f"  {o['action']:9s} {o['player_uuid'][:8]}… {o['registry_id']} net={o['net_delta']}"
              + (f"  reason={o['reason']}" if o['reason'] else ""))
    print()

    # --- 3. active-sheets 归因查询 ---
    print("--- 场景 2：GET /active-sheets（启发式归因查询）---")
    code, body = http_request(
        "GET", f"{api}/v1/construction/active-sheets",
        headers={**svc_h, "X-Player-UUID": str(p1_uuid)},
    )
    print(f"HTTP {code}  heuristic_eligible={body.get('heuristic_eligible')}  sheets={[s['title'] for s in body.get('sheets', [])]}\n")

    # --- 4. JWT[mod_id] 上报（客户端 mod 通道）---
    print("--- 场景 3：JWT[mod_id] 客户端 mod 上报（先切源）---")
    # 玩家 2 切到 local mod（用 web JWT）
    code, body = http_request(
        "POST", f"{api}/v1/construction/source/switch-self",
        body={"mode": "local", "source_id": "demo-client-mod"},
        headers={"Authorization": f"Bearer {p2_jwt}"},
    )
    print(f"切源 HTTP {code} → active={body.get('source_type')}/{body.get('source_id')}")
    # 铸 mod-token（复用 p2 的 sub + active_uuid，加 mod_id）
    p2_claims = decode_jwt_payload(p2_jwt)
    mod_token = mint_jwt(jwt_secret or "dev-secret", {
        "sub": p2_claims["sub"],
        "role": p2_claims.get("role", "user"),
        "type": "access",
        "active_uuid": p2_claims["active_uuid"],
        "mod_id": "demo-client-mod",  # C-10
    })
    code, body = http_request(
        "POST", f"{api}/v1/construction/report",
        body={
            "sheet_id": sheet_id,
            "placements": [
                # payload player_uuid 被 server 强制覆盖为 active_uuid（C-10）
                {"player_uuid": str(uuid.uuid4()), "registry_id": "minecraft:glass", "placed_qty": 8, "broken_qty": 2},
            ],
        },
        headers={"Authorization": f"Bearer {mod_token}"},
    )
    print(f"HTTP {code}  totals={body.get('totals')}  outcome.player_uuid={body['outcomes'][0]['player_uuid'][:8]}…（应 = Bob）\n")

    # --- 5. 进度查询 ---
    print("--- 场景 4：GET /{sheet_id}/progress（进度展示）---")
    code, body = http_request(
        "GET", f"{api}/v1/construction/{sheet_id}/progress",
        headers={**svc_h, "X-Player-UUID": str(p1_uuid)},
    )
    print(f"HTTP {code}")
    for t in body.get("account_totals", []):
        print(f"  账号 {t['display_name']}: placed={t['placed_qty']} broken={t['broken_qty']} net={t['net_qty']}")
    print(f"  明细 {len(body.get('breakdown', []))} 条\n")

    # --- 6. 场景 5（迭代 2）：上报清单外方块 → skip ---
    # 建一个空清单 sheet（不带 sheet_rows）→ advance → constructing → 上报 minecraft:dirt（不在清单）
    print("--- 场景 5（迭代 2）：上报清单外方块 → skip ---")
    code, body = http_request(
        "POST", f"{api}/sheets",
        body={"title": "施工上报测试项目-空清单"},
        headers={"Authorization": f"Bearer {p1_jwt}"},
    )
    assert code == 201, (code, body)
    empty_sheet_id = body["id"]
    code, _ = http_request(
        "POST", f"{api}/sheets/{empty_sheet_id}/advance?to=constructing",
        headers={"Authorization": f"Bearer {p1_jwt}"},
    )
    assert code == 200, (code, _)
    print(f"✓ 已建空清单项目 id={empty_sheet_id}（无任何 sheet_rows）")

    # service-token 上报 minecraft:dirt（清单外）→ 应 skip
    code, body = http_request(
        "POST", f"{api}/v1/construction/report",
        body={
            "sheet_id": empty_sheet_id,
            "placements": [
                {"player_uuid": str(p1_uuid), "registry_id": "minecraft:dirt",
                 "placed_qty": 10, "broken_qty": 0},
            ],
        },
        headers=svc_h,
    )
    print(f"HTTP {code}  totals={body.get('totals')}")
    outcomes = body.get("outcomes", [])
    if outcomes:
        o = outcomes[0]
        print(f"  {o['action']:9s} {o['player_uuid'][:8]}… {o['registry_id']} net={o['net_delta']}"
              + (f"  reason={o['reason']}" if o['reason'] else ""))
        # 迭代 2 契约：清单外方块必 skip，reason 字面量固定
        assert o["action"] == "skipped", f"✗ 期望 action=skipped，实际 {o['action']}"
        assert o["reason"] == "方块不在项目材料清单内", f"✗ 期望 reason='方块不在项目材料清单内'，实际 {o['reason']!r}"
        print(f"✓ 断言通过：清单外方块 skip 且 reason 字面量匹配")
    print()

    # --- 7. 场景 6（迭代 2）：progress 响应含 material_completion + timeline 字段 ---
    print("--- 场景 6（迭代 2）：GET /{sheet_id}/progress 含 material_completion + timeline ---")
    code, body = http_request(
        "GET", f"{api}/v1/construction/{sheet_id}/progress",
        headers={**svc_h, "X-Player-UUID": str(p1_uuid)},
    )
    print(f"HTTP {code}")
    # 字段存在性校验（迭代 2 新增字段）
    assert "material_completion" in body, "✗ 响应缺 material_completion 字段"
    assert "timeline" in body, "✗ 响应缺 timeline 字段"
    print(f"  material_completion: {len(body['material_completion'])} 项")
    for mc in body["material_completion"]:
        pct = "—" if mc["completion_pct"] is None else f"{mc['completion_pct']}%"
        print(f"    {mc['registry_id']:24s} need={mc['need_qty']:>4} net={mc['net_qty']:>4} 完成度={pct}")
    print(f"  timeline: {len(body['timeline'])} 条（limit 200 升序）")
    for ts in body["timeline"][:3]:  # 仅展示前 3 条避免刷屏
        print(f"    account={ts['account_id']} total_net={ts['total_net']} at={ts['recorded_at']}")
    if len(body["timeline"]) > 3:
        print(f"    ...（共 {len(body['timeline'])} 条）")
    print(f"✓ 断言通过：progress 响应含两新字段\n")

    print("=== 完成。参考：上报源实现契约见 Docs/architecture/api/construction.md §默认追踪器实现契约 ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
