<script setup lang="ts">
/**
 * 创建日期：2026-07-18
 * 工作台首页：用户/项目/在线状态 + 4 数字卡片 + 8 宫格快捷入口 + 更多功能 + 最近通知
 * 更新日期：2026-07-19 补齐工作记录/任务/巡检/台账快捷入口与待办徽章
 */
import { ref, computed, onMounted, onActivated } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { useAppStore } from '@/stores/app'
import { taskApi, workRecordApi } from '@/api'
import { formatFromNow } from '@/utils/format'
import { hapticLight } from '@/utils/haptic'

defineOptions({ name: 'Home' })

const router = useRouter()
const auth = useAuthStore()
const appStore = useAppStore()

const stats = ref({
  todo: 0,
  unread: 0,
  todayInspection: 0,
  todayHazard: 0,
})
const notifications = ref<any[]>([])
const loading = ref(false)

const statusText = computed(() => (appStore.online ? '在线' : '离线'))
const statusColor = computed(() => (appStore.online ? '#07c160' : '#ed6a0c'))

// 8 宫格快捷入口
interface QuickAction {
  icon: string
  text: string
  to: string
  color: string
  badge?: boolean
}
const quickActions: QuickAction[] = [
  { icon: 'photograph', text: '拍照取证', to: '/images/capture', color: '#1989fa' },
  { icon: 'records', text: '工作记录', to: '/work-records', color: '#07c160' },
  { icon: 'description', text: '监理文书', to: '/documents', color: '#7232dd' },
  { icon: 'warning-o', text: '隐患整改', to: '/hazards', color: '#ff976a' },
  { icon: 'shield-o', text: 'AI 巡检', to: '/inspection', color: '#1989fa' },
  { icon: 'search', text: '规范查询', to: '/knowledge', color: '#07c160' },
  { icon: 'todo-list-o', text: '我的任务', to: '/tasks', color: '#ff976a', badge: true },
  { icon: 'balance-list-o', text: '台账查询', to: '/ledger', color: '#7232dd' },
]

// 更多功能（二级入口）
const moreEntries = [
  { text: '验收流程指引', to: '/knowledge/guide' },
  { text: '通知中心', to: '/notifications' },
]

async function loadStats() {
  loading.value = true
  try {
    const [todoRes, workRes, notifRes] = await Promise.allSettled([
      taskApi.todoStats(),
      workRecordApi.stats(),
      taskApi.notifications(),
    ])
    if (todoRes.status === 'fulfilled') {
      const t = todoRes.value as any
      stats.value.todo = t?.pending_receive ?? t?.todo_count ?? t?.pending ?? 0
    }
    if (workRes.status === 'fulfilled') {
      const w = workRes.value as any
      stats.value.todayInspection = w?.today_inspection ?? w?.today_count ?? 0
      stats.value.todayHazard = w?.today_hazard ?? 0
    }
    if (notifRes.status === 'fulfilled') {
      const n = notifRes.value as any
      notifications.value = (n?.notifications || n?.items || []).slice(0, 5)
      stats.value.unread = (n?.notifications || n?.items || [])
        .filter((item: any) => item.read !== true && item.is_read !== true)
        .length
    }
  } finally {
    loading.value = false
  }
}

function go(to: string) {
  hapticLight()
  router.push(to)
}

onMounted(loadStats)
onActivated(loadStats)
</script>

<template>
  <div class="home-page">
    <!-- 顶部信息 -->
    <div class="hero">
      <div class="hero-row">
        <div class="hero-user">
          <van-image
            round
            width="44"
            height="44"
            :src="auth.user?.avatar_url"
            fit="cover"
          >
            <template #error>
              <div class="avatar-fallback">{{ auth.user?.name?.charAt(0) || '监' }}</div>
            </template>
          </van-image>
          <div class="hero-info">
            <div class="hero-name">{{ auth.user?.name || '监理员' }}</div>
            <div class="hero-meta">
              <van-tag plain type="primary" size="medium">{{ auth.user?.role || '-' }}</van-tag>
              <span class="hero-project">{{ auth.user?.project_name || '未绑定项目' }}</span>
            </div>
          </div>
        </div>
        <div class="hero-status" :style="{ color: statusColor }">
          <span class="dot" :style="{ background: statusColor }"></span>{{ statusText }}
        </div>
      </div>
    </div>

    <!-- 数字卡片 -->
    <div class="stat-grid">
      <div class="stat-card" @click="go('/tasks')">
        <div class="stat-num">{{ stats.todo }}</div>
        <div class="stat-label">待办任务</div>
      </div>
      <div class="stat-card" @click="go('/notifications')">
        <div class="stat-num">{{ stats.unread }}</div>
        <div class="stat-label">未读通知</div>
      </div>
      <div class="stat-card" @click="go('/images')">
        <div class="stat-num">{{ stats.todayInspection }}</div>
        <div class="stat-label">今日巡检</div>
      </div>
      <div class="stat-card" @click="go('/hazards')">
        <div class="stat-num text-danger">{{ stats.todayHazard }}</div>
        <div class="stat-label">今日隐患</div>
      </div>
    </div>

    <!-- 快捷入口（8 宫格） -->
    <div class="section">
      <div class="section-title">快捷入口</div>
      <div class="quick-grid">
        <div
          v-for="a in quickActions"
          :key="a.to"
          class="quick-item"
          @click="go(a.to)"
        >
          <div class="quick-icon" :style="{ background: a.color }">
            <van-icon :name="a.icon" size="22" color="#fff" />
            <span v-if="a.badge && stats.todo > 0" class="quick-badge">{{ stats.todo }}</span>
          </div>
          <span class="quick-text">{{ a.text }}</span>
        </div>
      </div>
    </div>

    <!-- 更多功能 -->
    <div class="section">
      <div class="section-title">更多功能</div>
      <van-cell-group inset>
        <van-cell
          v-for="m in moreEntries"
          :key="m.to"
          :title="m.text"
          is-link
          @click="go(m.to)"
        />
      </van-cell-group>
    </div>

    <!-- 最近通知 -->
    <div class="section">
      <div class="section-title">
        最近通知
        <span class="more" @click="go('/notifications')">查看全部</span>
      </div>
      <van-cell-group inset>
        <van-skeleton title :row="2" :loading="loading" v-if="loading && !notifications.length">
          <template #template></template>
        </van-skeleton>
        <van-empty
          v-else-if="!notifications.length"
          description="暂无通知"
          image="search"
        />
        <van-cell
          v-for="n in notifications"
          :key="n.id"
          :title="n.title || n.message || '任务通知'"
          :label="formatFromNow(n.created_at || n.ts)"
          is-link
          @click="go('/notifications')"
        >
          <template #icon>
              <van-tag v-if="!n.read" type="danger" class="cell-tag">新</van-tag>
            </template>
        </van-cell>
      </van-cell-group>
    </div>
  </div>
</template>

<style scoped>
.home-page {
  padding-bottom: 16px;
}
.hero {
  background: linear-gradient(135deg, #1989fa, #0f6fd1);
  color: #fff;
  padding: 14px 16px calc(env(safe-area-inset-top) + 14px);
}
.hero-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.hero-user {
  display: flex;
  align-items: center;
  gap: 12px;
}
.avatar-fallback {
  width: 100%;
  height: 100%;
  border-radius: 50%;
  background: rgba(255, 255, 255, 0.25);
  color: #fff;
  font-size: 18px;
  font-weight: 600;
  display: flex;
  align-items: center;
  justify-content: center;
}
.hero-info {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.hero-name {
  font-size: 17px;
  font-weight: 600;
}
.hero-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
}
.hero-project {
  opacity: 0.9;
}
.hero-status {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  background: rgba(255, 255, 255, 0.18);
  padding: 4px 10px;
  border-radius: 12px;
}
.dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
}
.stat-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 10px;
  padding: 0 12px;
  margin-top: -22px;
}
.stat-card {
  background: #fff;
  border-radius: 12px;
  padding: 14px;
  text-align: center;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.04);
}
.stat-num {
  font-size: 24px;
  font-weight: 700;
  color: #1989fa;
}
.stat-label {
  margin-top: 4px;
  font-size: 12px;
  color: #969799;
}
.section {
  margin-top: 18px;
}
.section-title {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin: 0 16px 8px;
  font-size: 14px;
  font-weight: 600;
}
.more {
  font-size: 12px;
  color: #1989fa;
  font-weight: 400;
}
.quick-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 8px;
  padding: 0 12px;
}
.quick-item {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 6px;
  padding: 12px 4px;
  background: #fff;
  border-radius: 10px;
}
.quick-icon {
  position: relative;
  width: 40px;
  height: 40px;
  border-radius: 10px;
  display: flex;
  align-items: center;
  justify-content: center;
}
.quick-badge {
  position: absolute;
  top: -6px;
  right: -6px;
  min-width: 16px;
  height: 16px;
  padding: 0 4px;
  background: #ee0a24;
  color: #fff;
  border-radius: 8px;
  font-size: 10px;
  line-height: 16px;
  text-align: center;
}
.quick-text {
  font-size: 12px;
  color: #323233;
}
.cell-tag {
  margin-right: 6px;
}
.text-danger {
  color: #ee0a24;
}
</style>
