<script setup lang="ts">
/**
 * 创建日期：2026-07-18
 * 默认布局：顶部 NavBar + 中部 router-view + 底部 TabBar
 * - NavBar 标题取自路由 meta.title；返回按钮在非首页可返回时显示
 * - TabBar 4 个标签：工作台 / 影像 / 文书 / 我的
 * - 适配 iPhone X+ 安全区（刘海与底部 home indicator）
 */
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { useAppStore } from '@/stores/app'
import { hapticLight } from '@/utils/haptic'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const appStore = useAppStore()

const title = computed(() => (route.meta.title as string) || '监理经办')

// TabBar 配置：工作台 / 工作记录 / 影像 / 我的
const tabs = [
  { name: '工作台', to: '/', icon: 'wap-home-o' },
  { name: '工作记录', to: '/work-records', icon: 'records' },
  { name: '影像', to: '/images', icon: 'photo-o' },
  { name: '我的', to: '/profile', icon: 'user-o' },
] as const

const activeTab = computed(() => {
  // 命中 TabBar 路径或其子路径
  const matched = tabs.find((t) => t.to === '/' ? route.path === '/' : route.path.startsWith(t.to))
  return matched?.to ?? ''
})

function onTabClick(to: string) {
  hapticLight()
  if (to !== route.path) router.push(to)
}

function onBack() {
  hapticLight()
  router.back()
}

// 更多功能二级入口（响应式 ActionSheet）
const showMoreSheet = ref(false)
const moreActions = [
  { name: '我的任务', path: '/tasks' },
  { name: 'AI 巡检', path: '/inspection' },
  { name: '台账查询', path: '/ledger' },
  { name: '监理文书', path: '/documents' },
  { name: '规范库', path: '/knowledge' },
  { name: '验收流程指引', path: '/knowledge/guide' },
  { name: '通知中心', path: '/notifications' },
]

function onMenu() {
  hapticLight()
  showMoreSheet.value = true
}

function onMoreSelect(action: { name: string }) {
  const target = moreActions.find((a) => a.name === action.name)
  if (target) router.push(target.path)
}

const showBack = computed(() => route.path !== '/')

const onlineText = computed(() => (appStore.online ? '在线' : '离线'))
</script>

<template>
  <div class="default-layout">
    <van-nav-bar
      :title="title"
      fixed
      placeholder
      safe-area-inset-top
      left-arrow
      :left-text="showBack ? '返回' : ''"
      @click-left="onBack"
      right-icon="bars"
      @click-right="onMenu"
    >
      <template #left v-if="!showBack">
        <van-icon name="shop-o" />
        <span class="nav-shop">{{ auth.user?.project_name || '监理平台' }}</span>
      </template>
    </van-nav-bar>

    <main class="layout-main">
      <router-view v-slot="{ Component }">
        <keep-alive :include="['Home']">
          <component :is="Component" />
        </keep-alive>
      </router-view>
    </main>

    <van-tabbar :model-value="activeTab" route placeholder safe-area-inset-bottom>
      <van-tabbar-item
        v-for="tab in tabs"
        :key="tab.to"
        :name="tab.to"
        :icon="tab.icon"
        :dot="tab.to === '/' && appStore.pendingSyncCount > 0"
        @click="onTabClick(tab.to)"
      >
        {{ tab.name }}
      </van-tabbar-item>
    </van-tabbar>

    <!-- 离线状态条 -->
    <div v-if="!appStore.online" class="offline-bar">
      <van-icon name="warning-o" /> 当前离线，数据将在联网后自动同步（待同步 {{ appStore.pendingSyncCount }} 条）
    </div>

    <!-- 更多功能 ActionSheet -->
    <van-action-sheet
      v-model:show="showMoreSheet"
      title="更多功能"
      :actions="moreActions.map(a => ({ name: a.name }))"
      close-on-click-action
      cancel-text="取消"
      @select="onMoreSelect"
    />
  </div>
</template>

<style scoped>
.default-layout {
  display: flex;
  flex-direction: column;
  min-height: 100vh;
  min-height: 100dvh;
  background: var(--app-bg);
}
.layout-main {
  flex: 1;
  padding-bottom: env(safe-area-inset-bottom);
}
.nav-shop {
  margin-left: 4px;
  font-size: 14px;
}
.offline-bar {
  position: fixed;
  left: 0;
  right: 0;
  bottom: 50px;
  padding: 6px 12px;
  font-size: 12px;
  color: #fff;
  background: #ed6a0c;
  text-align: center;
  z-index: 99;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
}
</style>
