import type { AccountBrief, PlayerBrief } from '../api/identity'

/**
 * 解析登录欢迎语展示名（#35）：昵称 → 用户名 → 游戏名 → 空串。
 *
 * - display_name 优先：玩家自定义昵称（sheets 三端显示名主源）
 * - username 兜底：永久账号 Web 登录名（密码登录主用例——本次 bug 即此处取错读成 player.name）
 * - player.name 最终兜底：临时账号（!!PCH login 进来）username/display_name 均为 null，只剩游戏名
 * - 全空返回 ''（理论不发生，避免 `欢迎，undefined`）
 *
 * 用 `||` 而非 `??`：display_name 经 PATCH 可设为空串，`??` 仅兜底 null/undefined 会把 '' 当有效名
 * 返回（显示「欢迎，」），`||` 连空串一起兜底，与文档承诺的回退链一致。
 */
export function resolveDisplayName(
  account: AccountBrief,
  player: PlayerBrief | null,
): string {
  return account.display_name || account.username || player?.name || ''
}
