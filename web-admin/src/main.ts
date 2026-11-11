import { createApp } from 'vue'
import { createPinia } from 'pinia'
import 'element-plus/dist/index.css'
import * as ElementPlusIconsVue from '@element-plus/icons-vue'
import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { BarChart as EBarChart, LineChart as ELineChart, PieChart as EPieChart } from 'echarts/charts'
import {
  GridComponent,
  TooltipComponent,
  LegendComponent,
  TitleComponent,
  DataZoomComponent,
} from 'echarts/components'
import VChart from 'vue-echarts'
import App from './App.vue'
import router from './router'
// 顺序要紧：theme.css 覆盖 element-plus 的 CSS 变量，必须在它之后引入
import './styles/main.css'
import './styles/theme.css'
import { setPermVersionHandler } from './api'
import { useAuthStore } from './stores/auth'

// 按需注册 echarts 组件
use([
  CanvasRenderer,
  EBarChart,
  ELineChart,
  EPieChart,
  GridComponent,
  TooltipComponent,
  LegendComponent,
  TitleComponent,
  DataZoomComponent,
])

const app = createApp(App)

for (const [key, component] of Object.entries(ElementPlusIconsVue)) {
  app.component(key, component)
}

// 全局注册 vue-echarts 组件
app.component('VChart', VChart)

app.use(createPinia())
app.use(router)

// Step 3：权限配置版本变化时，自动重新拉取当前用户权限。
// 由 api 拦截器在响应头 `X-Perm-Version` 与本地快照不一致时触发，
// 于是管理员改完角色权限，在线用户的下一个请求就会刷新菜单（无需重登）。
setPermVersionHandler(() => useAuthStore().fetchUserInfo())

app.mount('#app')
