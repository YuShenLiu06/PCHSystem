import { ref, readonly } from 'vue'

/**
 * 主题模式。`auto` = 跟随系统 `prefers-color-scheme`（默认）；
 * `dark`/`light` = 用户显式选择，写入 localStorage 后不再跟随系统。
 */
export type ThemeMode = 'auto' | 'dark' | 'light'

const STORAGE_KEY = 'pch-theme'
const DARK_QUERY = '(prefers-color-scheme: dark)'

/** 解析出的实际外观（喂给 `<html data-theme>` 与 BrandLogo 选图）。 */
export type Appearance = 'dark' | 'light'

const mode = ref<ThemeMode>('auto')
const appearance = ref<Appearance>('dark')
let mediaHandler: (() => void) | null = null

function readStoredMode(): ThemeMode {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (raw === 'dark' || raw === 'light' || raw === 'auto') return raw
  } catch {
    // localStorage 不可用（隐私模式/禁用）→ 退回 auto，不阻断渲染
  }
  return 'auto'
}

function systemAppearance(): Appearance {
  // 老环境 / jsdom 无 matchMedia → 默认暗色（与 wiki 一致）
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return 'dark'
  return window.matchMedia(DARK_QUERY).matches ? 'dark' : 'light'
}

function resolve(m: ThemeMode): Appearance {
  return m === 'auto' ? systemAppearance() : m
}

function apply(next: Appearance): void {
  appearance.value = next
  const root = document.documentElement
  // 亮色显式标记；暗色是 :root 默认，移除属性即可（与 tokens.css 选择器对应）
  if (next === 'light') root.setAttribute('data-theme', 'light')
  else root.removeAttribute('data-theme')
  // EP 暗色变量靠 html.dark 生效（theme-chalk/dark/css-vars.css）
  root.classList.toggle('dark', next === 'dark')
}

/** 设置模式：持久化 + 立即应用。`auto` 也持久化（区分「没选过」与「显式选跟随」）。 */
export function setThemeMode(next: ThemeMode): void {
  mode.value = next
  try {
    localStorage.setItem(STORAGE_KEY, next)
  } catch {
    // 存不下不影响本次会话生效
  }
  apply(resolve(next))
}

/** 顶栏按钮循环：auto → light → dark → auto（三态可达，不藏 auto）。 */
export function cycleThemeMode(): ThemeMode {
  const order: readonly ThemeMode[] = ['auto', 'light', 'dark']
  const next = order[(order.indexOf(mode.value) + 1) % order.length]
  setThemeMode(next)
  return next
}

/**
 * 初始化（`main.ts` 挂载前调一次）：读存储 → 应用 → 监听系统变化。
 * 系统变化仅在 `auto` 下跟随；用户显式选过就不再被系统覆盖。
 */
export function initTheme(): void {
  mode.value = readStoredMode()
  apply(resolve(mode.value))

  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return
  const mq = window.matchMedia(DARK_QUERY)

  // HMR 重跑时先移除旧监听器，避免 listener 堆积（生产 SPA 只初始化一次）
  if (mediaHandler) {
    if (typeof mq.removeEventListener === 'function') mq.removeEventListener('change', mediaHandler)
    else if (typeof mq.removeListener === 'function') mq.removeListener(mediaHandler)
  }

  const onChange = (): void => {
    if (mode.value === 'auto') apply(systemAppearance())
  }
  mediaHandler = onChange
  // Safari <14 只有 addListener；两者都试，避免旧 iOS 静默失效
  if (typeof mq.addEventListener === 'function') mq.addEventListener('change', onChange)
  else if (typeof mq.addListener === 'function') mq.addListener(onChange)
}

export function useTheme() {
  return {
    mode: readonly(mode),
    appearance: readonly(appearance),
    setThemeMode,
    cycleThemeMode,
  }
}
