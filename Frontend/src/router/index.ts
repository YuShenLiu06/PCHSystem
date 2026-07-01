import { createRouter, createWebHistory } from 'vue-router'

// F3 will populate real routes (/auth, /me). F1 only needs the export to satisfy main.ts import.
export const router = createRouter({
  history: createWebHistory(),
  routes: [{ path: '/', component: { template: '<div>placeholder</div>' } }],
})
