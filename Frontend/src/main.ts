import { createApp } from 'vue'
import { createPinia } from 'pinia'
import ElementPlus from 'element-plus'

// 样式层叠顺序（不可调换）：
// 1. tokens        —— --pch-* 定义
// 2. EP 基础 + 暗色 —— 提供 --el-* 默认值
// 3. overrides     —— 把 --el-* 指向 --pch-*，并覆盖组件样式
// 4. base          —— reset / 排版 / 布局原语 / signature
import './styles/tokens.css'
import 'element-plus/dist/index.css'
import 'element-plus/theme-chalk/dark/css-vars.css'
import './styles/element-overrides.css'
import './styles/base.css'

import App from './App.vue'
import { router } from './router'
import { initTheme } from './composables/useTheme'

// 挂载前定主题，避免首帧闪色（FOUC）
initTheme()

createApp(App).use(createPinia()).use(router).use(ElementPlus).mount('#app')
