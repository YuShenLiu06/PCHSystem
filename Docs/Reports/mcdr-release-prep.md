# `.mcdr` 自动发布 prep 计划（CI 之前要备好的事）

> 配套 [`mcdr-publishing-strategy.md`](./mcdr-publishing-strategy.md)（讲打包格式/catalogue 合规）。本文讲：**在 CI 自动产出并分发 `.mcdr` 之前，仓库内必须先补齐的运行时与文档**，因为 pch_system **强依赖后端、前端可选**——单独的 `.mcdr` 装上会哑火。
>
> 状态（2026-07-12 更新）：**#1（前后端嗅探）+ #2（后端 `/info`）已落地**；#3~#5 静态文档与 §4 CI 仍为 prep 待办。

---

## 1. 依赖拓扑（红线）

| 组件 | 必需？ | 说明 |
|---|---|---|
| **Backend**（FastAPI + PostgreSQL） | **必需** | 插件所有功能经 HTTP API 走后端；无后端 = 插件完全不可用 |
| **pch_system 插件**（`.mcdr` 或源码目录） | 必需（游戏内通道） | 玩家侧全走 `!!PCH` 命令；无插件则游戏内无法交互 |
| **Frontend**（Vue web） | **可选** | 仅管理后台/可视化；不装也能用，玩家全走 `!!` 命令 |

→ **结论**：`.mcdr` 不是自包含产物。三种合法部署形态见 §3 #3。

---

## 2. 现状缺口（已核实，2026-07-11）

| 缺口 | 位置 | 影响 |
|---|---|---|
| 后端**无版本端点**；`FastAPI(version="0.1.0")` **陈旧写死**（实际 `pyproject.toml` = `0.6.0`） | `Backend/app/main.py:11` | 插件无法日志打印「已连后端 vX.Y.Z」；兼容性无锚点 |
| 插件 `on_load` **不探测后端可达性** | `McdrPlugin/pch_system/pch_system/__init__.py:49` | 第一次碰后端要等玩家敲 `!!PCH login` 或 notifier 轮询 |
| 后端不可达时**静默失败**：`request_login_url` 捕获 `RequestException` → `_log.error` + `return None` | `McdrPlugin/pch_system/pch_system/client.py:45` | 只装 `.mcdr` 的玩家敲 `!!PCH login` 静默失败，得翻 MCDR 日志；**不会被告知「需先部署后端」** |
| 无「部署矩阵」文档；`.mcdr` release-notes 无固定模板 | `README.md` / `CONTRIBUTING.md` / `.github/` | catalogue/release 下载者看不到「需后端 + 最低版本」 |

---

## 3. prep 清单（本轮/紧跟 prep PR 落地）

> 原则：让插件**自带「我需要一个后端」的自检与自述**——运行时（#1+#2）+ 静态文档（#3+#4+#5）两条线。

### #1 插件 `on_load` 增后端可达性自检（最高价值）— ✅ 已落地 2026-07-12

> **落地**：`McdrPlugin/pch_system/pch_system/health.py`（探针 + 渲染 + `run_console_check`）+ `__init__.py:_start_health_check`（`@new_thread`）+ `commands.py:_status`（游戏内 `!!PCH status`）。**超出原设计的扩展**：除后端探针外，新增 ① 前端嗅探（靠 #2 `/info` 回传的 `web_base_url`）② 「未配置 / 离线 / 在线-低版本」分档链接（release / RUNBOOK / frontend.md）③ **令牌真校验**（`probe_token` 探 service-token 保护端点 `/notifications/pending`，401=不一致 error / 非 401=一致 ok，取代占位串启发式——后者无法识别「两边都用占位」与「单边占位代写必 401」）④ 插件自身版本置顶（`resolve_plugin_meta` → `get_plugin_metadata`）+ 作者页脚 `作者：YuShen`。`probe_backend` / `probe_token` 独立放 `health.py` 而非 `client.py`（职责分离：client=auth 写通道，health=自检只读探针）。下方原计划保留作设计溯源。

- **动文件**：`McdrPlugin/pch_system/pch_system/__init__.py`（`on_load` 末尾调用）+ `client.py` 新增 `probe_backend(cfg) -> (ok: bool, info: dict|None)`。
- **行为**：
  - best-effort `GET {api_url}/info`（用 `cfg.http_timeout_seconds`，复用 #2 的新端点；若 404 回退 `/healthz`，兼容旧后端）。
  - **成功** → `serv.logger.info("已连接 PCHSystem 后端 %s (v%s)", api_url, info["version"])`；若 `info["version"]` < 插件声明的最低兼容后端版本（见 #5）→ 额外 `log.warning`。
  - **失败（超时/连接拒绝/401）** → `serv.logger.error(...)` 一段**可操作**文案：`pch_system 必须配合 PCHSystem 后端使用 → https://github.com/YuShenLiu06/PCHSystem （bash Scripts/install.sh）；请检查 <MCDR>/config/pch_system/config.json 的 api_url / service_token`。
- **约束**：
  - **不得阻塞 `on_load`**：探针放 `@new_thread`（对齐 RS-6，沿用 `_start_notifier` 的 `@new_thread` 模式），或挂进 notifier 首轮；on_load 本身只 fire-and-forget。
  - best-effort：任何异常都吞掉只 log，**绝不让插件加载失败**（否则 reload 炸）。
  - S-1：`serv.logger` / `@new_thread` 用法实现时再联网核对（<https://docs.mcdreforged.com/zh-cn/latest/code_references/ServerInterface.html>）。
- **验证**：mc-test 改 `config.json` 的 `api_url` 成不可达地址 → reload → 看 MCDR 日志有清晰 error 文案；改回正确地址 → reload → 日志「已连接后端 vX.Y.Z」。

### #2 后端补 `GET /info` + 修陈旧 version — ✅ 已落地 2026-07-12

> **落地**：`Backend/app/main.py` — `_backend_version()` 走 `importlib.metadata.version("pchsystem-backend")`，`FastAPI(version=_backend_version())` 同步修掉写死的 `"0.1.0"`；`GET /info` 公开返 `{name, version, status, web_base_url}`（`web_base_url` 为 #1 前端嗅探喂料，超出原设计的 `{name, version, status}`）。`/healthz` 契约不变。下方原计划保留作设计溯源。

- **动文件**：`Backend/app/main.py`（`create_app` 内）。
- **行为**：新增 `GET /info` → `{"name":"HTCMC PCHSystem","version":"<真值>","status":"ok"}`，**公开无需鉴权**（同 `/healthz`）。
- **version 来源**：`importlib.metadata.version("pchsystem-backend")`（容器内已装包；权威源 `Backend/pyproject.toml:7 version="0.6.0"`）。把 `FastAPI(version="0.1.0")` 一并改成同一来源。
- **不要破坏 `/healthz`**：保留 `{"status":"ok"}` 原样（compose healthcheck + `install.sh`/`update.sh` 的 `wait_http_ok /healthz` 都依赖它）。
- **验证**：`curl localhost:8000/info` → 返回真版本；`/healthz` 不变。

### #3 部署矩阵文档（直接对应「backend 必需、frontend 可选」）

- **动文件**：根 `README.md` 新增一节「部署形态」+ `CONTRIBUTING.md` §4 引用。
- **内容表**：

  | 形态 | 组件 | 怎么装 | 适用 |
  |---|---|---|---|
  | 全栈（默认） | backend + frontend(web) + plugin | `git clone` → `bash Scripts/install.sh`（COMPOSE_PROFILES=web） | 单机一站式 |
  | 最小可用 | backend + plugin（无 web） | 同上 + `--no-web`，或 `.env` 清空 `COMPOSE_PROFILES` | 不需 Web，玩家全走 `!!` 命令 |
  | **仅 `.mcdr`** | plugin only | 下载 `pch_system-v*.mcdr` → 丢 `plugins/` → reload | **⚠️ 不可独立工作**——必须有一个**可达的**后端（自建或共享），并在 `config.json` 配好 `api_url`/`service_token` |

### #4 `.mcdr` release-notes 模板（给 CI PR 兜底文案）

- **动文件**：新增 `Docs/release-notes/pch_system.template.md`（CI PR 的 workflow 把它拼进 release body）+ 可选 `.github/release.yml`（GitHub 自动生成 notes 配置）。
- **必含字段**：依赖（需 PCHSystem 后端）+ 最低兼容后端版本 + install.sh 链接 + 前端可选说明 + config.json 两个必填键（`api_url`/`service_token`）。

### #5 插件静态自述（catalogue 可见性）

- **动文件**：`McdrPlugin/pch_system/mcdreforged.plugin.json` 的 `description`（加「需 PCHSystem 后端」一句）；新增 `McdrPlugin/pch_system/README.md`（pack 时经 `resources` 打进 `.mcdr`，MCDR catalogue 展示）。
- **最低兼容后端版本**：在插件内声明一个常量（如 `MIN_BACKEND_VERSION = "0.6.0"`），供 #1 的探针对比。

---

## 4. 推迟到 CI PR（下一轮）

- GitHub Actions：`push tag pch_system-v*` → `pip install mcdreforged` → `mcdreforged pack -i McdrPlugin/pch_system -n "{id}-{version}"` → 产出 `pch_system-v*.mcdr` → 附到 release（用 #4 模板拼 body）。
- 补 `mcdreforged.plugin.json` 的 `archive_name`（命名稳定化）。
- catalogue 提交决策（是否进 MCDR PluginCatalogue；进则需 packed 格式 + 合规 tag，已具备）。
- `backend-v*` / `frontend-v*` tag：不产二进制 asset，仅 tag 源码（compose 从源码 build）。

---

## 5. 落地顺序建议

1. ✅ **#2**（后端 `/info` + 修 version）→ 给插件探针提供靶子。**已落地 2026-07-12**。
2. ✅ **#1**（插件 on_load 自检）→ 消灭静默哑火；依赖 #2。**已落地 2026-07-12**（含前端嗅探扩展）。
3. **#5**（插件自述 + MIN_BACKEND_VERSION）→ `MIN_BACKEND_VERSION` 已在 `health.py` 声明；`description` / README 自述仍待办。
4. **#3**（矩阵文档）+ **#4**（release-notes 模板）→ 文案并行，待办。
5. （CI PR）自动化打包 + asset 上传，待办。

## 6. 风险与注意

- **#1 不得阻塞/炸 on_load**：best-effort + `@new_thread` + 吞异常；否则 reload 失败比哑火更糟。
- **#2 `/info` 公开版本**：内部工具，版本泄露风险可接受；若顾虑可只对带 service_token 的请求返回 version（但探针尚未拿 token 之前就要探可达性——折中：`/info` 公开 name+status，version 仅在 401 后带 token 再取，或直接公开。倾向直接公开，简单优先 KISS）。
- **兼容性窗口**：插件 ↔ 后端同仓同发，强耦合可接受；`MIN_BACKEND_VERSION` 仅作 catalogue 用户混版本的兜底，不做严格的 API 协商。
- **`/info` vs `/healthz`**：保留 `/healthz` 现状（healthcheck 契约），新增 `/info` 给探针用，互不干扰。

---

*最后更新：2026-07-12（#1 前后端嗅探 + #2 后端 `/info` 已落地；#3~#5 静态文档与 CI 待办）*
