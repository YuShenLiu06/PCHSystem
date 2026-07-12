// 构建时由 vite define 注入（见 vite.config.ts）——把 __APP_VERSION__ 替换为
// package.json 的 version 字符串。dev/build 均生效；未注入（如纯 vitest）回落 'dev'。
declare const __APP_VERSION__: string | undefined

/** 前端版本号（需求 2：App.vue 导航栏显示，对齐插件 `!!PCH status` 的 v<version> 风格） */
export const APP_VERSION: string =
  typeof __APP_VERSION__ === 'string' ? __APP_VERSION__ : 'dev'
