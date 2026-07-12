# MCDR 插件发布策略 · 决策与计划报告

> **状态**：发布**延后**——先完成所有功能，再启动"去喷化发布"。
> **最后更新**：2026-07-12（令牌真校验 `probe_token` + 插件版本/作者页脚；详见 §5 / §6.2）
> **范围**：本文记录 (1) 考据结论、(2) 发布决策、(3) 就绪后的执行计划。供未来重启此议题时直接取用，避免重复论证。

---

## 1. 背景（一句话）

`pch_system` 是 **PCHSystem 后端的"游戏端客户端"**（R-7 瘦客户端），所有命令（login/bind/sheet/project/score/title/notify）都是对后端的 HTTP 调用。希望将来把它发到 [MCDR 官方 catalogue](https://github.com/MCDReforged/PluginCatalogue) 获得曝光、吸引贡献者；**后端个人自用，不对外提供服务。**

---

## 2. 决策摘要（TL;DR）

| 项 | 决策 |
|---|---|
| **何时发布 catalogue** | **延后**——先做完所有功能，再启动发布。 |
| **发布路线** | "**去喷化发布**"：启动自检 + 诚实简介 + `!!PCH setup` 向导。 |
| **为什么不能直接发** | 插件是私有后端的客户端；catalogue 模型无 client/server 槽位；静默坏掉必被喷。 |
| **CI / 镜像** | 全三端 CI；**不发** GHCR 镜像。 |
| **非容器** | 文档 + 脚手手稿（systemd / nginx）。 |
| **MCDR tag 约定** | 改为 `pch_system-vX.Y.Z`（catalogue 合法格式 4，前向兼容）。 |
| **暂不做** | lite 模式（不推翻 R-1/R-5/R-7）；docker 镜像发布。 |

---

## 3. 已核实的官方事实（S-1 已联网核实，附 URL）

### 3.1 打包 / 元数据
- **打包命令**：`mcdreforged pack -i <插件根目录>`（= `python -m mcdreforged pack`），读 `mcdreforged.plugin.json`，产出 `.mcdr`。
  - 来源：<https://docs.mcdreforged.com/zh-cn/dev/cli/pack.html>
- **`mcdreforged.plugin.json` 字段**：`id` / `version` / `name` / `description`(str 或 lang→str) / `author` / `link` / `dependencies`(plugin_id→版本要求) / `entrypoint` / `archive_name`(打包产物名) / `resources`(额外打入的文件列表)。
  - `id`：小写字母+数字+下划线，1–64 字符；不可改。
  - 来源：<https://docs.mcdreforged.com/en/latest/plugin_dev/metadata.html>
- **插件格式**：solo(`.py`) / **packed(`.mcdr`/`.pyz`)** / directory。**只有 packed 能进 catalogue**。
  - 来源：<https://docs.mcdreforged.com/en/latest/plugin_dev/plugin_format.html>

### 3.2 catalogue 机制
- 官方仓库：<https://github.com/MCDReforged/PluginCatalogue>（`master` 分支，GitHub Action 每小时自动更新 2 次）。
- **`plugin_info.json` schema**（在 catalogue 仓库的 `plugins/<plugin_id>/` 下）：`id` / `authors` / `repository`(插件自己的 github 仓库) / `branch` / `labels` / `introduction`(lang→md 路径，至少一种语言)。
- **labels 全量 5 个**：`information` / `tool` / `management` / `api` / `handler`。**无 client/server/service/integration 类目**；`api` 指"作为库给进程内其它插件调用"，**不是**服务 API。
- **Release 自动探测条件**（catalogue 从你仓库的 GitHub Release 抓 `.mcdr` 下载链接）：
  1. Release **非 pre-release**；
  2. **tag 名匹配版本**，仅四种合法格式：
     - `<version>`（`1.2.3`）
     - `v<version>`（`v1.2.3`）
     - `<plugin_id>-<version>`（`pch_system-1.2.3`）
     - `<plugin_id>-v<version>`（`pch_system-v1.2.3`）
  3. 附件含 `.mcdr` 或 `.pyz`。
- **提交方式**：Fork catalogue → 在 `plugins/<plugin_id>/` 建 `plugin_info.json` + introduction → PR。维护者按"自动检查 + 贡献指南 + 个人判断"审核。
  - 来源：<https://docs.mcdreforged.com/zh-cn/latest/plugin_dev/plugin_catalogue.html>、<https://github.com/MCDReforged/PluginCatalogue/blob/master/CONTRIBUTING_zh_cn.md>

### 3.3 官方 Docker 镜像（与"后端"无关，给 MCDR 自己用）
- `mcdreforged/mcdreforged`、`mcdreforged/mcdreforged-temurin`（带 JDK，跑 MC 服用）。可作 `TestServer/` 镜像的 base。
  - 来源：<https://docs.mcdreforged.com/zh-cn/latest/docker.html>

### 3.4 我们插件当前的关键缺口（已探查代码）
- `McdrPlugin/pch_system/mcdreforged.plugin.json`：`link` **指向错误仓库**（`gubaiovo/MCDR_uuid_api_remake`）；**缺 `archive_name` / `resources`**。
- `McdrPlugin/pch_system/tests/test_scanner.py`：**散落在打包根**，会被打入 `.mcdr`，需移走。
- 插件依赖另两个 MCDR 插件 `minecraft_data_api`、`uuid_api_remake`（非 pip 包）。
- 仓库**完全没有 `.github/`**，无任何 CI。
- 现有 tag 约定 `mcdr-vX.Y.Z` **不命中 catalogue 任何一种合法格式**（解析出的 plugin_id 是 `mcdr` ≠ `pch_system`），Release 不会被探测。
- **⚠️ 待最终确认**：内置 `!!MCDR plugin install <id>` **不自动安装依赖插件**（生态里另需 [MCDReforgedPluginManager](https://github.com/Ivan-1F/MCDReforgedPluginManager) 补这个能力）。此结论来自 catalogue 文档 + PluginManager 的存在性；`!!MCDR` 指令页（`commands.html`）此刻 404/500 拉不到，**发布前需复核**。

### 3.5 标准范式参考（PiPInstaller-MCDR）
- 布局：`src/` 放 `mcdreforged.plugin.json` + `requirements.txt` + 内层包；`pyproject.toml` 把 `mcdreforged` 作 dev 依赖，`uv run mcdreforged pack -i src`。
- `release.yml`：tag push → 校验 tag → `mcdreforged pack` → 从 `CHANGELOG.md` 抽 release notes → `softprops/action-gh-release@v2` 发版挂 `.mcdr`。
- `ci.yml`：lint + pack 上传 artifact。
  - 来源：<https://github.com/Mooling0602/PiPInstaller-MCDR>

---

## 4. 为什么"直接发"有问题（catalogue 模型考据）

### 4.1 不是明文禁止，是模型里没有这个槽位
catalogue 文档、CONTRIBUTING、labels、plugin_info.json schema 里**没有任何一句"禁止前后端分离 / 禁止 client/server"**。但模型结构上：

| 模型要素 | 能表达 | 表达不了 |
|---|---|---|
| `plugin_info.json` | id / authors / repository(插件自己的仓库) / labels / introduction | 无"依赖某后端服务""配对服务"字段 |
| `labels`（5 个） | information/tool/management/api/handler | 无 client/server/service/integration |
| `dependencies`（plugin.json） | 按 plugin_id 依赖其它 MCDR 插件 | 不能写服务 URL |
| Release 探测 | 一个 release 一个 `.mcdr` 附件 | 无"服务端另发"概念 |
| `!!MCDR plugin install` | 自动解析整条依赖闭包（catalogue 插件 + pip 包） | 闭包外的外部服务装不了 |

**根因**：catalogue 是 MCDR 运行时模型的镜像——一台 MC 服 ↔ 一个 MCDR 进程 ↔ 一堆加载进**同一进程**的插件，插件间靠 `get_plugin_instance` / `api` / `dependencies` **进程内协作**。所有可装的东西都在 MCDR 进程内。一个 client/server 拆分天然打破：server 那半不是 MCDR 插件，在 catalogue 里没有 id、没有 `.mcdr`、不能当依赖；`!!MCDR plugin install` 装出的插件满足不了自己的依赖闭包，破坏"装完即用"承诺。

### 4.2 CONTRIBUTING 里离它最近的明文（人审门槛）
- **"你的插件是否为打包插件？——单文件插件和文件夹插件不可入库"**。
- **"在相应字段中明确声明前置插件和 Python 包"**——依赖只有"前置插件"和"Python 包"两类，**无"外部服务"**。
- **"不要将仅供自用或完全不实用的插件提交到插件库"** + **"是否更适合 PyPI？"**。
- **"维护者将根据自动检查结果、该指南和个人判断给出反馈"**。

### 4.3 喷的机制 = 静默坏掉
用户喷的触发点几乎从来不是"需要配置"，而是**装完 → 没反应、没报错、啥也不说**：
- ❌ 静默坏掉 → "破插件""骗人"。
- ✅ 启动时控制台醒目报错 + 仓库链接 + 配置指引 → "哦，是我少装了东西"。

**小众技术圈（MCDR 用户跑服+用 MCDR，技术素养高）对"需要折腾"容忍度其实不低，前提是把"需要折腾"讲明白。** 喷的风险主要来自静默，不是来自重。

### 4.4 三端拆分≠插件过重（一个反直觉）
把后端折进插件**只会让插件更重**——积分/状态机/流水/RBAC/JWT/归档全搬进来，还得塞 SQLite+迁移+并发，同时丢掉 R-1 审计流水、R-5 Web 身份锚、多服、wiki。**三端拆分是让插件保持瘦（R-7）的原因，不是让它变重的原因。** "重"的是运营者侧（pg+backend+frontend）的一次性部署面，不是玩家侧（两条命令）、不是插件本身。

---

## 5. "去喷化"发布路线（决策已定，待功能完成后执行）

把"静默坏掉"换成"大声引导"，三件套：

1. **`on_load` 启动自检**（最关键，**✅ 已实现 2026-07-12**）：落地于 `McdrPlugin/pch_system/pch_system/health.py` + 后端 `GET /info`（返 `version` + `web_base_url`）。状态分档：
   - **后端未配置**（`service_token` 仍是默认占位 `change_me_service_token`）→ release 链接 + `bash Scripts/install.sh` + config.json 提示；
   - **后端离线**（已配置但探针失败）→ RUNBOOK + release；
   - **后端在线** → 打真版本（`importlib.metadata`），低于 `MIN_BACKEND_VERSION` 则 warn；
   - **令牌**（仅后端在线才探）：真打 service-token 保护端点 `GET /notifications/pending?player_uuid=<nil>` 带 `X-Service-Token`——后端 `require_service_token`（`compare_digest`）先于 `_require_player` 解析，故 **401 = token 与后端不一致**（error + RUNBOOK），非 401（404 nil player / 200）= 一致（ok），探针网络异常 = 未知（warn）。**不再靠 `service_token` 占位串启发式猜**——那无法区分「两边都用占位（能用）」与「插件占位/后端真值（代写必 401）」，改由后端 401 真信号裁决；
   - **前端**：仅后端在线时按 `/info` 回传的 `web_base_url` 嗅探；不可达 → frontend.md + release（前端「已装 vs 离线」HTTP 无法可靠区分，诚实兜底）。
   - **插件自身**：始终置顶一行 `pch_system v<version>`（`PluginServerInterface.get_plugin_metadata` 取 `mcdreforged.plugin.json`，best-effort 回落「版本未知」）；报告末尾固定作者页脚 `作者：<author>`（元数据 `.authors`，回落 `YuShen`）。

   控制台 `serv.logger` 一份（URL 可复制）+ 游戏内 `!!PCH status` 一份（RText 可点击链接、随时复检）；探针 `@new_thread` best-effort，不阻塞/炸 `on_load`。
2. **catalogue 简介前置诚实**：`description` 写"PCHSystem 游戏端：积分/项目/表单协作（**需自部署后端**）"；`introduction` 第一行 `⚠️ 非即装即用，需配合后端`。
3. **`!!PCH setup` 向导**：交互式收 `api_url` + `service_token` → 测连通 → 写 `config.json`。

---

## 6. 就绪后执行计划（checklist）

> 触发条件：所有功能完成、准备发布。

### 6.1 插件元数据修正
- [ ] `McdrPlugin/pch_system/mcdreforged.plugin.json`：`link` 改为本仓库正确 URL；加 `archive_name`（如 `pch_system-v{version}`）；按需加 `resources`。
- [ ] 把散落的 `McdrPlugin/pch_system/tests/test_scanner.py` 移到 `McdrPlugin/tests/`，避免被打入包。
- [ ] 复核 §3.4「依赖插件是否随 `!!MCDR plugin install` 自动装」；若否，README/简介写明需先装 `minecraft_data_api`、`uuid_api_remake`（或推荐装 MCDReforgedPluginManager）。

### 6.2 启动自检（去喷化核心）— ✅ 已实现 2026-07-12
- [x] `on_load` 自检：`McdrPlugin/pch_system/pch_system/health.py`（`probe_backend` 探 `/info`、404 回退 `/healthz`；`probe_frontend` 按 `web_base_url` 探；`classify` 状态分档；`run_console_check` `@new_thread` best-effort 吞异常）+ `__init__.py:_start_health_check`。
- [x] 后端 `GET /info`：`Backend/app/main.py`，公开无鉴权，返 `{name, version, status, web_base_url}`；`version` 走 `importlib.metadata.version("pchsystem-backend")`，与 OpenAPI `/docs` 同源（`FastAPI(version=...)` 同步修掉写死的 `"0.1.0"`）。
- [x] `!!PCH status` 游戏内命令（`commands.py:_status`，`@new_thread` + `src.reply` 可点击链接，控制台/玩家通用）。
- [x] 令牌真校验：`probe_token` 探 `/notifications/pending`（service-token 保护端点），401=不一致 error / 非 401=一致 ok / 异常=未知 warn（取代 `service_token` 占位串启发式——后者无法识别「两边都用占位（能用）」与「单边占位（代写必 401）」）。
- [x] 插件自身版本 + 作者页脚：`resolve_plugin_meta`（`get_plugin_metadata("pch_system")`，S-1 已核实）→ 报告置顶 `pch_system v<ver>` finding + 末尾 `作者：<author>`。
- [ ] `!!PCH setup` 交互式向导（收 `api_url`+`token` 写 config）—— 仍待办（本次只做「检测+提醒」）。

### 6.3 `!!PCH setup` 向导
- [ ] 新增命令子树（`Literal("setup")`），交互收 api_url + token，调 `/health` 自检，写 `config/pch_system/config.json`，成功后回执。

### 6.4 CI（全三端，不发镜像）
- [ ] `.github/workflows/ci.yml`：
  - **Backend**：`services: postgres:16` → alembic 迁移 → `pytest tests/`（测试是集成测试，需活 PG）。
  - **Frontend**：`npm ci && npm run build`（含 `vue-tsc`）+ `npx vitest run`。
  - **MCDR**：`cd McdrPlugin && pip install -r requirements.txt && pytest`（`tests/_stubs.py` 已 stub 掉 MCDR 运行时，无需真 MCDR）。

### 6.5 release.yml（`.mcdr` 自动产物）
- [ ] `.github/workflows/release.yml`：on `pch_system-v*` tag → `pip install mcdreforged` → `mcdreforged pack -i McdrPlugin/pch_system` → `softprops/action-gh-release@v2` 挂 `./*.mcdr` + 从 `CHANGELOG.md` 抽 notes。
- [ ] backend / frontend 的 tag（`backend-v*` / `frontend-v*`）：只生成 release notes，不发镜像、不打 artifact（部署从源码 compose build）。

### 6.6 tag 约定
- [ ] `CONTRIBUTING.md` §4：`mcdr-vX.Y.Z` → `pch_system-vX.Y.Z`（catalogue 合法、前向兼容）。

### 6.7 catalogue 提交（外部 PR，手动一次性）
- [ ] Fork `MCDReforged/PluginCatalogue`，在 `plugins/pch_system/` 建 `plugin_info.json`（id/authors/repository/branch/labels=intro+tool/introduction）+ `introduction.md`（前置诚实）。
- [ ] 提 PR，附本仓库链接，等维护者审核。

### 6.8 运营者首次部署引导
- [ ] 容器路径：`scripts/bootstrap.sh`（或 `install.sh`）——检查 docker/compose → 从 `.env.example` 生成 `.env`（提示改密钥）→ `docker compose up -d --build` → 等 `pg_isready` → 打印后端 `api_url` + 提示签发 `MCDR_SERVICE_TOKEN`。
- [ ] 非容器路径：`Docs/RUNBOOK.md` 加章节 + 后端 systemd unit、前端 nginx/Caddy 配置脚手手稿。
- [ ] README 顶部钉死定位："PCHSystem——自部署的 MC 积分/项目系统；本仓库的游戏端组件，需配合后端。"

---

## 7. 明确排除 / 暂不做

- **不进 catalogue**——直到所有功能完成。
- **不发 GHCR docker 镜像**——部署从源码 compose build。
- **不做 lite 模式**——不把后端折进插件、不换 SQLite、不砍 Web 端（即不推翻 R-1/R-5/R-7）。这是另一个项目级决策，不混进本次发布。

---

## 8. 参考来源

- 插件仓库文档：<https://docs.mcdreforged.com/zh-cn/latest/plugin_dev/plugin_catalogue.html>
- 元数据文档：<https://docs.mcdreforged.com/en/latest/plugin_dev/metadata.html>
- 插件格式文档：<https://docs.mcdreforged.com/en/latest/plugin_dev/plugin_format.html>
- pack CLI：<https://docs.mcdreforged.com/zh-cn/dev/cli/pack.html>
- 官方 Docker 镜像：<https://docs.mcdreforged.com/zh-cn/latest/docker.html>
- catalogue 贡献指南：<https://github.com/MCDReforged/PluginCatalogue/blob/master/CONTRIBUTING_zh_cn.md>
- catalogue 仓库：<https://github.com/MCDReforged/PluginCatalogue>
- 范式参考（PiPInstaller）：<https://github.com/Mooling0602/PiPInstaller-MCDR>
- 依赖管理参考（PluginManager）：<https://github.com/Ivan-1F/MCDReforgedPluginManager>
