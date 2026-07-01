import { createRouter, createWebHistory } from 'vue-router'
import { useAuthStore } from '../stores/auth'

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/auth', component: () => import('../views/AuthExchange.vue'), meta: { public: true } },
    { path: '/me', component: () => import('../views/Me.vue') },
    { path: '/', redirect: '/me' },
  ],
})

router.beforeEach((to) => {
  const auth = useAuthStore()
  if (!to.meta.public && !auth.isAuthenticated) return '/auth'
})
