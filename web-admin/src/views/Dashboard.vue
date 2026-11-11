<template>
  <div class="dashboard-page">
    <!-- 工作统计（对话已收敛到右下角「监理小助手」） -->
    <div class="stats-header">
      <span class="title-text">工作记录（未查看 / 未处理）</span>
      <span class="view-all" @click="goToRecordList('all')">查看全部 ›</span>
    </div>

    <div class="stats-cards">
      <div class="stat-card-item" v-for="stat in workStats" :key="stat.key" @click="goToRecordList(stat.key)">
        <div class="card-icon" :style="{ background: stat.bgColor }">
          <el-icon :size="24" :color="stat.iconColor"><component :is="stat.icon" /></el-icon>
        </div>
        <div class="card-content">
          <div class="card-title">{{ stat.label }}</div>
          <div class="card-desc">{{ stat.desc }}</div>
          <div class="card-bottom">
            <span class="card-count" :class="{ 'count-zero': stat.unviewed === 0 }">{{ stat.unviewed }}</span>
            <span class="card-unit">未查看</span>
            <span class="card-separator">/</span>
            <span class="card-count pending" :class="{ 'count-zero': stat.pending === 0 }">{{ stat.pending }}</span>
            <span class="card-unit">未处理</span>
          </div>
        </div>
      </div>
    </div>

    <div class="hazard-pending-bar" @click="goToHazardList">
      <el-icon size="20" color="#fa8c16"><Warning /></el-icon>
      <span class="hazard-pending-text">
        安全隐患待处理：<strong>{{ hazardPending }}</strong> 条
      </span>
      <el-icon size="14"><ArrowRight /></el-icon>
    </div>

    <p class="assistant-hint">右下角「监理小助手」可对话查询、生成文书；Ctrl/⌘ + K 唤起。</p>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, markRaw } from 'vue'
import { useRouter } from 'vue-router'
import { workRecordApi } from '@/api'
import {
  View, CircleCheck, Monitor, Camera, Document,
  Warning, ArrowRight,
} from '@element-plus/icons-vue'

const router = useRouter()

const workStats = ref([
  { key: 'inspection', label: '巡视记录', desc: '现场巡视检查', icon: markRaw(View), unviewed: 0, pending: 0, bgColor: '#e9effd', iconColor: '#2563EB' },
  { key: 'acceptance', label: '验收记录', desc: '工程质量验收', icon: markRaw(CircleCheck), unviewed: 0, pending: 0, bgColor: '#f6ffed', iconColor: '#52c41a' },
  { key: 'station', label: '旁站记录', desc: '关键工序旁站', icon: markRaw(Monitor), unviewed: 0, pending: 0, bgColor: '#fff7e6', iconColor: '#fa8c16' },
  { key: 'witness', label: '见证记录', desc: '见证取样送检', icon: markRaw(Camera), unviewed: 0, pending: 0, bgColor: '#f9f0ff', iconColor: '#722ed1' },
  { key: 'other', label: '其他工作', desc: '其他工作事项', icon: markRaw(Document), unviewed: 0, pending: 0, bgColor: '#e6fffb', iconColor: '#13c2c2' },
])
const hazardPending = ref(0)

async function loadWorkStats() {
  try {
    const stats: any = await workRecordApi.stats() as any
    const wr = stats?.work_records || {}
    workStats.value[0].unviewed = wr['巡视']?.unviewed || 0
    workStats.value[0].pending = wr['巡视']?.pending || 0
    workStats.value[1].unviewed = wr['验收']?.unviewed || 0
    workStats.value[1].pending = wr['验收']?.pending || 0
    workStats.value[2].unviewed = wr['旁站']?.unviewed || 0
    workStats.value[2].pending = wr['旁站']?.pending || 0
    workStats.value[3].unviewed = wr['见证']?.unviewed || 0
    workStats.value[3].pending = wr['见证']?.pending || 0
    workStats.value[4].unviewed = wr['其他']?.unviewed || 0
    workStats.value[4].pending = wr['其他']?.pending || 0
    hazardPending.value = stats?.hazards?.pending || 0
  } catch {
    // 静默失败，统计显示 0
  }
}

function goToRecordList(key: string) {
  const routeMap: Record<string, string> = {
    inspection: '/work-record/inspection',
    acceptance: '/work-record/acceptance',
    station: '/work-record/station',
    witness: '/work-record/witness',
    all: '/work-record/inspection',
  }
  const path = routeMap[key]
  if (path) router.push(path)
}

function goToHazardList() {
  router.push('/inspection-dashboard')
}

onMounted(() => {
  void loadWorkStats()
})
</script>

<style scoped>
.dashboard-page {
  max-width: 1400px;
  margin: 0 auto;
}

.stats-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
}
.title-text {
  font-size: 16px;
  font-weight: 600;
  color: #333;
}
.view-all {
  font-size: 13px;
  color: var(--el-color-primary);
  cursor: pointer;
}
.view-all:hover {
  text-decoration: underline;
}

.stats-cards {
  display: flex;
  gap: 16px;
}
.stat-card-item {
  flex: 1;
  background: #fff;
  border-radius: 8px;
  padding: 16px;
  display: flex;
  gap: 12px;
  cursor: pointer;
  border: 1px solid #e8e8e8;
  transition: all 0.2s;
}
.stat-card-item:hover {
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.1);
  transform: translateY(-2px);
}
.card-icon {
  width: 48px;
  height: 48px;
  border-radius: 8px;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}
.card-content {
  flex: 1;
  display: flex;
  flex-direction: column;
  justify-content: space-between;
}
.card-title {
  font-size: 15px;
  font-weight: 600;
  color: #333;
}
.card-desc {
  font-size: 12px;
  color: #999;
  margin-top: 2px;
}
.card-bottom {
  display: flex;
  align-items: baseline;
  gap: 4px;
  margin-top: 8px;
}
.card-count {
  font-size: 24px;
  font-weight: 700;
  color: #fa541c;
}
.card-count.pending {
  color: #fa8c16;
}
.card-count.count-zero {
  color: #999;
}
.card-unit {
  font-size: 12px;
  color: #999;
}
.card-separator {
  font-size: 14px;
  color: #ccc;
  margin: 0 4px;
}

.hazard-pending-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 16px;
  padding: 12px 16px;
  background: #fff7e6;
  border: 1px solid #ffd591;
  border-radius: 8px;
  cursor: pointer;
  transition: all 0.2s;
}
.hazard-pending-bar:hover {
  background: #fff1cc;
}
.hazard-pending-text {
  flex: 1;
  font-size: 14px;
  color: #333;
}
.hazard-pending-text strong {
  color: #fa541c;
  font-size: 18px;
}

.assistant-hint {
  margin: 18px 0 0;
  font-size: 11.5px;
  color: #b3b8bf;
}
</style>
