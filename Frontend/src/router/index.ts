import { createRouter, createWebHistory } from 'vue-router'
import { useAuthStore } from '../stores/auth'

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    // 公开路由（无需登录即可访问）
    { path: '/auth', component: () => import('../views/AuthExchange.vue'), meta: { public: true } },
    { path: '/login', component: () => import('../views/identity/Login.vue'), meta: { public: true } },
    { path: '/register', component: () => import('../views/identity/Register.vue'), meta: { public: true } },
    // 以下身份相关路由均需登录（除登录/注册类外无 meta.public）
    // /bind/confirm 需永久账号 JWT（输入游戏内 !!PCH bind 给的短码）；/bind/claim 需临时会话 JWT
    { path: '/bind/confirm', component: () => import('../views/identity/BindConfirm.vue') },
    { path: '/bind/claim', component: () => import('../views/identity/ClaimBind.vue') },
    // 需登录路由（身份页统一在 /me：账号信息 + 绑定 UUID 列表 + 绑定新身份入口）
    { path: '/me', component: () => import('../views/Me.vue') },
    { path: '/sheets', component: () => import('../views/sheets/SheetList.vue') },
    { path: '/sheets/:id', component: () => import('../views/sheets/SheetEditor.vue') },
    { path: '/parsing/litematic', component: () => import('../views/parsing/LitematicImport.vue') },
    { path: '/', redirect: '/me' },
  ],
})

router.beforeEach((to) => {
  const auth = useAuthStore()
  if (!to.meta.public && !auth.isAuthenticated) return '/auth'
})
