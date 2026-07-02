# 开发指令速查表

> 日常开发高频指令速查。完整流程与排错见各服务 README / CLAUDE.md。
> MCDR **API** 速查（命令节点 / RText / 权限 …）另见 [`../McdrPlugin/mcdr-api-cheatsheet.md`](../McdrPlugin/mcdr-api-cheatsheet.md)，本表只收**运维 / 调试指令**。

---

## MCDR / 测试服

### 接管 MCDR 控制台

```bash
docker attach pchsystem-mc-test-1
```

进入 MCDR 交互控制台，可直接输入 `!!help`、`!!MCDR reload plugin htcmc_auth` 等命令。

| 操作 | 按键 | 说明 |
|---|---|---|
| 脱离控制台（保持服务运行） | `Ctrl+P` 然后 `Ctrl+Q` | 推荐，安全退出 attach |
| 停止服务 | `Ctrl+C` | **禁止** —— 会 SIGINT 杀掉 MCDR / MC 服务端 |

> ⚠️ **雷点**：脱离只能用 `Ctrl+P, Ctrl+Q`；误按 `Ctrl+C` 会直接关服导致玩家掉线。
> attach 后若被日志刷屏，按一次回车即可重新看到 MCDR 输入提示。
