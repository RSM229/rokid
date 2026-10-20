/**
 * 创建日期：2026-07-18
 * 应用入口：注册 Vant（按需）、Pinia、Router，挂载全局样式
 */
import { createApp } from 'vue'
import { createPinia } from 'pinia'
import router from './router'
import App from './App.vue'

// Vant 基础样式（组件通过 unplugin-vue-components 按需引入）
import 'vant/lib/index.css'
import './styles/vant-theme.css'
import './styles/main.css'

const app = createApp(App)
app.use(createPinia())
app.use(router)
app.mount('#app')

if (import.meta.env.PROD && 'serviceWorker' in navigator) {
  window.addEventListener('load', async () => {
    try {
      const registration = await navigator.serviceWorker.register('/sw.js', { scope: '/' })
      // 浏览器自身也会检查更新；这里在每次页面加载时主动检查，缩短发布生效时间。
      await registration.update()
    } catch (error) {
      console.error('PWA Service Worker 注册失败', error)
    }
  })
}
