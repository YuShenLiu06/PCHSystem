"""web 前端可达性 + 版本探测（后端 ``/info`` 与 ``/auth/token`` 共用）。

``web_base_url`` 是面向玩家浏览器的地址（常含 localhost），后端/插件在容器内探它会命中
容器自身 → 误报不可达。改由后端探 compose 服务名（``WEB_PROBE_URL``，同网络可靠），
顺带读 web 托管的 ``version.json`` 拿前端版本号（供 ``!!PCH status`` 显示）。
"""
from dataclasses import dataclass

import httpx


@dataclass(frozen=True)
class WebProbe:
    online: bool | None         # True=web 在跑 / False=连不上 / None=未配 WEB_PROBE_URL
    version: str | None = None  # version.json 的 version；web 在跑但无 version.json 时 None


async def probe_web(web_probe_url: str) -> WebProbe:
    """探 web 可达性 + 版本。

    ``web_probe_url`` 空 → ``WebProbe(None, None)``（调用方回退自探 ``web_base_url``）。
    GET ``{web_probe_url}/version.json``：收到任意 HTTP 响应 = web 在跑（``online=True``），
    2xx 且 JSON 含 version → 记下版本；连接异常 → ``online=False``。
    best-effort，timeout 2s，绝不抛。
    """
    if not web_probe_url:
        return WebProbe(online=None, version=None)
    base = web_probe_url.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            resp = await client.get(f"{base}/version.json")
    except Exception:
        return WebProbe(online=False, version=None)
    version: str | None = None
    if 200 <= resp.status_code < 400:
        try:
            version = resp.json().get("version")
        except Exception:
            version = None
    return WebProbe(online=True, version=version)
