import { readFileSync, writeFileSync, mkdirSync } from 'node:fs'
import { defineConfig } from 'vitest/config'
import vue from '@vitejs/plugin-vue'

// 构建时注入前端版本号（package.json version），供 App.vue 显示（需求 2）
const pkg = JSON.parse(readFileSync(new URL('./package.json', import.meta.url), 'utf-8'))

// https://vite.dev/config/
export default defineConfig({
  plugins: [
    vue(),
    {
      name: 'write-version-json',
      closeBundle() {
        // build 后写 dist/version.json，供 backend /info 探测前端版本号
        //（!!PCH status 显示前端 v<version>）。nginx 托管在根路径。
        mkdirSync('dist', { recursive: true })
        writeFileSync('dist/version.json', JSON.stringify({ version: pkg.version }))
      },
    },
  ],
  define: {
    // 构建时把 __APP_VERSION__ 替换为 package.json version 字符串（需求 2）
    __APP_VERSION__: JSON.stringify(pkg.version),
  },
  server: {
    // 通过外部域名（反代 / tunnel，如 dev-git.u3071783.nyat.app）访问 dev server 时需显式信任，
    // 否则 Vite 默认拦截（防 DNS rebinding）。仅 dev；生产走 vite build 静态产物，不受此影响。
    allowedHosts: ['dev-git.u3071783.nyat.app'],
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
        rewrite: (p) => p.replace(/^\/api/, ''),
      },
    },
  },
  preview: {
    // `vite preview` 不继承 server.proxy；为本地验证 prod 构建复刻同一代理。
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
        rewrite: (p) => p.replace(/^\/api/, ''),
      },
    },
  },
  test: {
    environment: 'jsdom',
  },
})
