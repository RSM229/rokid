<template>
  <div class="company-dashboard">
    <div class="page-header">
      <div>
        <h2>公司看板</h2>
        <p>公司级多项目横向对比：在建数 / 隐患数 / 整改率排名。</p>
      </div>
      <el-button :icon="Refresh" @click="loadAll">刷新</el-button>
    </div>

    <!-- 顶部 KPI 卡片 -->
    <div class="stat-cards" v-loading="loading">
      <div class="stat-card" v-for="(card, i) in statCards" :key="i">
        <div class="card-icon" :style="{ background: card.bg, color: card.color }">
          <el-icon :size="26"><component :is="card.icon" /></el-icon>
        </div>
        <div class="card-body">
          <div class="card-title">{{ card.title }}</div>
          <div class="card-value" :style="{ color: card.color }">{{ card.value }}</div>
        </div>
      </div>
    </div>

    <!-- 项目对比表 -->
    <el-card shadow="never" class="compare-card">
      <template #header>
        <span class="card-header-title">项目对比（按整改率降序）</span>
      </template>
      <el-table
        :data="projects"
        v-loading="loading"
        stripe
        :default-sort="{ prop: 'rectify_rate', order: 'descending' }"
      >
        <el-table-column prop="name" label="项目名称" min-width="200" show-overflow-tooltip />
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="statusType(row.status)" size="small">{{ row.status }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="hazard_total" label="隐患总数" width="110" sortable />
        <el-table-column prop="hazard_pending" label="待处理隐患" width="120" sortable>
          <template #default="{ row }">
            <span :class="{ 'pending-hot': row.hazard_pending > 0 }">{{ row.hazard_pending }}</span>
          </template>
        </el-table-column>
        <el-table-column label="整改率" min-width="180" sortable prop="rectify_rate" :sort-method="sortByRectifyRate">
          <template #default="{ row }">
            <div v-if="row.rectify_rate !== null && row.rectify_rate !== undefined" class="rate-cell">
              <el-progress
                :percentage="row.rectify_rate"
                :stroke-width="10"
                :color="rateColor(row.rectify_rate)"
                class="rate-bar"
              />
              <span class="rate-text">{{ row.rectify_rate }}%</span>
            </div>
            <span v-else class="no-data">—</span>
          </template>
        </el-table-column>
        <el-table-column label="最新形象进度" min-width="180" sortable prop="latest_progress" :sort-method="sortByProgress">
          <template #default="{ row }">
            <div v-if="row.latest_progress !== null && row.latest_progress !== undefined" class="rate-cell">
              <el-progress
                :percentage="row.latest_progress"
                :stroke-width="10"
                :color="progressColor(row.latest_progress)"
                class="rate-bar"
              />
              <span class="rate-text">{{ row.latest_progress }}%</span>
            </div>
            <span v-else class="no-data">—</span>
          </template>
        </el-table-column>
      </el-table>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { Refresh, Folder, Aim, Warning, TrendCharts } from '@element-plus/icons-vue'
import { statsApi } from '@/api'

const stats = ref<any>({
  total_projects: 0,
  active_projects: 0,
  total_pending_hazards: 0,
  avg_rectify_rate: null,
})
const projects = ref<any[]>([])
const loading = ref(false)

const statCards = computed(() => [
  {
    title: '总项目数',
    value: stats.value.total_projects ?? 0,
    icon: Folder,
    color: '#1890ff',
    bg: '#e6f4ff',
  },
  {
    title: '在建项目',
    value: stats.value.active_projects ?? 0,
    icon: Aim,
    color: '#52c41a',
    bg: '#f6ffed',
  },
  {
    title: '未闭合隐患总数',
    value: stats.value.total_pending_hazards ?? 0,
    icon: Warning,
    color: '#fa541c',
    bg: '#fff2e8',
  },
  {
    title: '平均整改率',
    value:
      stats.value.avg_rectify_rate !== null && stats.value.avg_rectify_rate !== undefined
        ? `${stats.value.avg_rectify_rate}%`
        : '—',
    icon: TrendCharts,
    color: '#722ed1',
    bg: '#f9f0ff',
  },
])

function statusType(status?: string) {
  const map: Record<string, string> = {
    '在建': 'success',
    '竣工': 'info',
    '未开工': 'warning',
  }
  return map[status || ''] || 'info'
}

function rateColor(rate: number) {
  if (rate >= 90) return '#52c41a'
  if (rate >= 60) return '#faad14'
  return '#fa541c'
}

function progressColor(value: number) {
  if (value >= 100) return '#52c41a'
  if (value >= 50) return '#1890ff'
  return '#faad14'
}

// 空值（无隐患/无进度记录）排序时按 -1 处理，避免 null 排序异常
function sortByRectifyRate(a: any, b: any) {
  return (a.rectify_rate ?? -1) - (b.rectify_rate ?? -1)
}

function sortByProgress(a: any, b: any) {
  return (a.latest_progress ?? -1) - (b.latest_progress ?? -1)
}

async function loadAll() {
  loading.value = true
  try {
    const res: any = await statsApi.company()
    stats.value = {
      total_projects: res?.total_projects ?? 0,
      active_projects: res?.active_projects ?? 0,
      total_pending_hazards: res?.total_pending_hazards ?? 0,
      avg_rectify_rate: res?.avg_rectify_rate ?? null,
    }
    projects.value = Array.isArray(res?.projects) ? res.projects : []
  } catch (e: any) {
    ElMessage.error('公司看板加载失败: ' + (e.response?.data?.detail || e.message || ''))
  } finally {
    loading.value = false
  }
}

onMounted(loadAll)
</script>

<style scoped>
.company-dashboard {
  max-width: 1400px;
  margin: 0 auto;
}
.page-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 16px;
}
.page-header h2 { margin: 0 0 6px; }
.page-header p { margin: 0; color: #7a8580; }

.stat-cards {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 16px;
  margin-bottom: 16px;
}
.stat-card {
  background: #fff;
  border-radius: 8px;
  padding: 18px;
  display: flex;
  align-items: center;
  gap: 14px;
  box-shadow: 0 1px 2px rgba(0, 21, 42, 0.06);
  border: 1px solid #f0f0f0;
}
.card-icon {
  width: 56px;
  height: 56px;
  border-radius: 8px;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}
.card-body { flex: 1; min-width: 0; }
.card-title {
  font-size: 13px;
  color: #7a8580;
  margin-bottom: 6px;
}
.card-value {
  font-size: 26px;
  font-weight: 700;
  line-height: 1;
}

.card-header-title {
  font-size: 15px;
  font-weight: 600;
  color: #303133;
}

.rate-cell {
  display: flex;
  align-items: center;
  gap: 8px;
}
.rate-bar { flex: 1; min-width: 80px; }
.rate-bar :deep(.el-progress__text) { display: none; }
.rate-text {
  font-size: 13px;
  font-weight: 600;
  white-space: nowrap;
}
.no-data { color: #bfbfbf; }
.pending-hot { color: #fa541c; font-weight: 600; }

@media (max-width: 992px) {
  .stat-cards { grid-template-columns: repeat(2, 1fr); }
}
</style>
