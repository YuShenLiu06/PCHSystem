import { defineConfig } from 'vitest/config'
import vue from '@vitejs/plugin-vue'

// https://vite.dev/config/
export default defineConfig({
  plugins: [vue()],
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
