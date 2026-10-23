<template>
  <div class="inspection-dashboard">
    <div class="page-header">
      <div>
        <h2>AI 巡检统计看板</h2>
        <p>展示 AI 巡检的总体统计、趋势及隐患类别分布。</p>
      </div>
      <el-button :icon="Refresh" @click="loadAll">刷新</el-button>
    </div>

    <!-- 顶部数字卡片 -->
    <div class="stat-cards" v-loading="statsLoading">
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

    <!-- 中部图表 -->
    <el-row :gutter="16" class="chart-row">
      <el-col :xs="24" :md="14">
        <el-card shadow="never">
          <template #header>
            <span class="card-header-title">最近 7 天巡检趋势</span>
          </template>
          <LineChart
            :categories="trendCategories"
            :series="trendSeries"
            dualAxis
            leftAxisName="巡检次数"
            rightAxisName="隐患数"
            height="340px"
          />
        </el-card>
      </el-col>
      <el-col :xs="24" :md="10">
        <el-card shadow="never">
          <template #header>
            <span class="card-header-title">隐患类别分布</span>
          </template>
          <PieChart :data="categoryData" ring height="340px" />
        </el-card>
      </el-col>
    </el-row>

    <!-- 底部最近巡检记录 -->
    <el-card shadow="never" class="record-card">
      <template #header>
        <span class="card-header-title">最近 20 条巡检记录</span>
      </template>
      <el-table :data="recentRecords" v-loading="recordsLoading" stripe>
        <el-table-column prop="inspected_at" label="时间" width="170" />
        <el-table-column prop="location" label="地点" min-width="160" />
        <el-table-column label="风险状态" width="120">
          <template #default="{ row }">
            <el-tag :type="riskStatusType(row.risk_status)" size="small">
              {{ row.risk_status || '-' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="risk_category" label="风险类别" width="130" />
        <el-table-column label="处理状态" width="120">
          <template #default="{ row }">
            <el-tag :type="handleStatusType(row.handle_status)" size="small" effect="plain">
              {{ row.handle_status || '未处理' }}
            </el-tag>
          </template>
        </el-table-column>
      </el-table>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { Refresh, View, Warning, DataLine, Histogram } from '@element-plus/icons-vue'
import { inspectionApi } from '@/api'
import LineChart from '@/components/charts/LineChart.vue'
import PieChart from '@/components/charts/PieChart.vue'

// 统计卡片
const stats = ref<any>({
  today_inspection_count: 0,
  today_hazard_count: 0,
  total_inspection_count: 0,
  total_hazard_count: 0,
})
const statsLoading = ref(false)

const statCards = computed(() => [
  {
    title: '今日巡检次数',
    value: stats.value.today_inspection_count ?? 0,
    icon: View,
    color: '#2563EB',
    bg: '#e9effd',
  },
  {
    title: '今日发现隐患',
    value: stats.value.today_hazard_count ?? 0,
    icon: Warning,
    color: '#fa541c',
    bg: '#fff2e8',
  },
  {
    title: '累计巡检次数',
    value: stats.value.total_inspection_count ?? 0,
    icon: DataLine,
    color: '#52c41a',
    bg: '#f6ffed',
  },
  {
    title: '累计发现隐患',
    value: stats.value.total_hazard_count ?? 0,
    icon: Histogram,
    color: '#722ed1',
    bg: '#f9f0ff',
  },
])

// 趋势图
const trendData = ref<any[]>([])
const trendCategories = computed(() => trendData.value.map((d) => d.date))
const trendSeries = computed(() => [
  {
    name: '巡检次数',
    data: trendData.value.map((d) => d.inspection_count ?? 0),
    yAxisIndex: 0,
    color: '#2563EB',
  },
  {
    name: '隐患数',
    data: trendData.value.map((d) => d.hazard_count ?? 0),
    yAxisIndex: 1,
    color: '#fa541c',
  },
])

// 类别饼图
const categoryData = ref<{ name: string; value: number }[]>([])

// 最近记录
const recentRecords = ref<any[]>([])
const recordsLoading = ref(false)

function riskStatusType(status?: string) {
  const map: Record<string, string> = {
    '高风险': 'danger',
    '中风险': 'warning',
    '低风险': 'info',
    '安全': 'success',
  }
  return map[status || ''] || 'info'
}

function handleStatusType(status?: string) {
  const map: Record<string, string> = {
    '已处理': 'success',
    '处理中': 'warning',
    '未处理': 'danger',
  }
  return map[status || ''] || 'danger'
}

async function loadStats() {
  statsLoading.value = true
  try {
    const res: any = await inspectionApi.stats()
    stats.value = res || {}
  } catch (e: any) {
    ElMessage.error('统计加载失败: ' + (e.response?.data?.detail || e.message || ''))
  } finally {
    statsLoading.value = false
  }
}

async function loadTrend() {
  try {
    const res: any = await inspectionApi.trend(7)
    trendData.value = Array.isArray(res) ? res : res?.items || []
  } catch (e: any) {
    ElMessage.error('趋势加载失败: ' + (e.response?.data?.detail || e.message || ''))
  }
}

async function loadCategory() {
  try {
    const res: any = await inspectionApi.categoryDistribution()
    const list = Array.isArray(res) ? res : res?.items || []
    categoryData.value = list.map((item: any) => ({
      name: item.name || item.category || '未知',
      value: item.value ?? item.count ?? 0,
    }))
  } catch (e: any) {
    ElMessage.error('类别分布加载失败: ' + (e.response?.data?.detail || e.message || ''))
  }
}

async function loadRecent() {
  recordsLoading.value = true
  try {
    const res: any = await inspectionApi.recentRecords(20)
    recentRecords.value = Array.isArray(res) ? res : res?.items || []
  } catch (e: any) {
    ElMessage.error('最近记录加载失败: ' + (e.response?.data?.detail || e.message || ''))
  } finally {
    recordsLoading.value = false
  }
}

async function loadAll() {
  await Promise.all([loadStats(), loadTrend(), loadCategory(), loadRecent()])
}

onMounted(loadAll)
</script>

<style scoped>
.inspection-dashboard {
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

.chart-row { margin-bottom: 16px; }
.card-header-title {
  font-size: 15px;
  font-weight: 600;
  color: #303133;
}
.record-card { margin-top: 4px; }

@media (max-width: 992px) {
  .stat-cards { grid-template-columns: repeat(2, 1fr); }
}
</style>
