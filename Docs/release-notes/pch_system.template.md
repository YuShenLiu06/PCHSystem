> ⚠️ 本插件需配合 **PCHSystem 后端**使用，单独安装 `.mcdr` 无法工作。

## 这是什么

PCH System 是 PCHSystem 的游戏内客户端（MCDReforged 插件）：材料协作收集、项目三阶段进度管理、积分 / 称号、通知投递等全在游戏内 `!!PCH` 命令完成。所有功能经 HTTP 调用后端，**必须先部署后端**。

## 首次部署（全栈 · 推荐）

```bash
git clone https://github.com/YuShenLiu06/PCHSystem.git
cd PCHSystem
bash Scripts/install.sh
```

脚本一键完成：docker / 镜像自适应 → 生成 `.env` → 起 postgres+backend+web → 数据库迁移 → 前端构建 → 拷贝插件 + 生成 config.json。完成后游戏内 `!!MCDR plugin reload pch_system` → `!!PCH status`（自检）→ `!!PCH login`。

## 升级（已部署）

```bash
bash Scripts/update.sh
```

## 依赖

- **MCDReforged ≥ 2.14.0**
- 插件依赖：`uuid_api_remake`、`minecraft_data_api`（`install.sh` 会检测并提示安装）
- **PCHSystem 后端（必需）** + 前端（可选）：见上「首次部署」

## 破坏性变更

<!-- 若本版本含 BREAKING CHANGE，在此说明升级注意事项 / 配置迁移；无则删此段 -->

---

## 更新内容
