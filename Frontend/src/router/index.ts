import { createRouter, createWebHistory } from 'vue-router'
import { useAuthStore } from '../stores/auth'

function isAdmin(): boolean {
  const auth = useAuthStore()
  return auth.account?.role === 'admin' || auth.account?.role === 'owner'
}

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    // 公开路由（无需登录即可访问）
    { path: '/auth', component: () => import('../views/AuthExchange.vue'), meta: { public: true } },
    { path: '/login', component: () => import('../views/identity/Login.vue'), meta: { public: true } },
    // /register 需登录（注册=临时账号转永久，须先有临时会话 JWT；未登录直访由守卫重定向 /auth，
    // 避免空 Authorization 头触发后端 401 "missing authorization"）
    { path: '/register', component: () => import('../views/identity/Register.vue') },
    // 以下身份相关路由均需登录（除 /auth /login 外无 meta.public）
    // /bind/confirm 需永久账号 JWT（输入游戏内 !!PCH bind 给的短码）；/bind/claim 需临时会话 JWT
    { path: '/bind/confirm', component: () => import('../views/identity/BindConfirm.vue') },
    { path: '/bind/claim', component: () => import('../views/identity/ClaimBind.vue') },
    // 需登录路由（身份页统一在 /me：账号信息 + 绑定 UUID 列表 + 绑定新身份入口）
    { path: '/me', component: () => import('../views/Me.vue') },
    { path: '/sheets', component: () => import('../views/sheets/SheetList.vue') },
    { path: '/sheets/:id', component: () => import('../views/sheets/SheetEditor.vue') },
    { path: '/parsing/batch', component: () => import('../views/parsing/BatchImport.vue') },
    // admin 模块（仅可见性；真实拒绝靠后端 RBAC 403，R-9/RS-2）
    {
      path: '/admin/construction',
      component: () => import('../views/admin/ConstructionSettings.vue'),
      meta: { requiresAdmin: true },
    },
    { path: '/', redirect: '/me' },
  ],
})

router.beforeEach((to) => {
  const auth = useAuthStore()
  if (!to.meta.public && !auth.isAuthenticated) return '/auth'
  // admin 守卫：非 admin/owner 访问 admin 路由 → 回 /me（后端 RBAC 仍会 403 兜底）
  if (to.meta.requiresAdmin && !isAdmin()) return '/me'
})
