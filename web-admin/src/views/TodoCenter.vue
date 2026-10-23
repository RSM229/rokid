<template>
  <div class="todo-page">
    <div class="page-header">
      <el-breadcrumb separator="/">
        <el-breadcrumb-item>首页</el-breadcrumb-item>
        <el-breadcrumb-item>待办中心</el-breadcrumb-item>
      </el-breadcrumb>
      <div class="header-actions">
        <el-input v-model="searchText" placeholder="搜索任务" prefix-icon="Search" clearable size="small" style="width: 200px" />
        <el-button size="small" :icon="Refresh" @click="loadData">刷新</el-button>
      </div>
    </div>

    <!-- 统计卡片 -->
    <el-card shadow="never" style="margin-bottom: 16px;">
      <p class="section-title">待办中心 — 监理端接收的所有待办与所有任务</p>
      <el-row :gutter="16" style="margin-top: 16px;">
        <el-col :span="6" v-for="(s, i) in statsCards" :key="i">
          <div class="stat-box-mini">
            <div class="stat-icon-wrap" :style="{ background: s.bg }">
              <el-icon :size="22" color="#fff"><component :is="s.icon" /></el-icon>
            </div>
            <div class="stat-info">
              <span class="stat-num">{{ s.value }}</span>
              <span class="stat-label">{{ s.label }}</span>
            </div>
          </div>
        </el-col>
      </el-row>
    </el-card>

    <!-- 待办列表 -->
    <el-card shadow="never">
      <template #header>
        <div style="display: flex; justify-content: space-between; align-items: center;">
          <span class="section-title">待编辑信息</span>
          <div style="display: flex; gap: 8px; align-items: center;">
            <el-select v-model="filterStatus" placeholder="待办类型" clearable size="small" style="width: 120px">
              <el-option label="全部" value="" />
              <el-option label="待接单" value="待接单" />
              <el-option label="进行中" value="进行中" />
              <el-option label="已完成" value="已完成" />
              <el-option label="已拒单" value="已拒单" />
            </el-select>
            <el-select v-model="filterPriority" placeholder="优先级" clearable size="small" style="width: 100px">
              <el-option label="全部" value="" />
              <el-option label="紧急" value="紧急" />
              <el-option label="高" value="高" />
              <el-option label="中" value="中" />
              <el-option label="低" value="低" />
            </el-select>
            <el-button type="primary" size="small" :icon="Search" @click="loadData">查询</el-button>
            <el-button size="small" :icon="RefreshRight" @click="resetFilters">重置</el-button>
          </div>
        </div>
      </template>

      <el-table :data="filteredTasks" stripe v-loading="loading" empty-text="暂无待办">
        <el-table-column prop="id" label="#" width="50" />
        <el-table-column prop="title" label="任务名称" min-width="180" show-overflow-tooltip />
        <el-table-column prop="category" label="类别" width="110">
          <template #default="{ row }"><el-tag size="small" type="info">{{ row.category || '其他' }}</el-tag></template>
        </el-table-column>
        <el-table-column prop="assigner_name" label="派单人" width="90" />
        <el-table-column prop="status" label="状态" width="90">
          <template #default="{ row }"><el-tag :type="statusType(row.status)" size="small">{{ row.status }}</el-tag></template>
        </el-table-column>
        <el-table-column prop="priority" label="优先级" width="70">
          <template #default="{ row }"><el-tag :type="priorityType(row.priority)" size="small">{{ row.priority }}</el-tag></template>
        </el-table-column>
        <el-table-column prop="deadline" label="截止日期" width="170">
          <template #default="{ row }">
            <span :class="{ overdue: isOverdue(row) }">{{ row.deadline || '-' }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="created_at" label="创建时间" width="170" />
        <el-table-column label="操作" width="220" fixed="right">
          <template #default="{ row }">
            <el-button size="small" link type="primary" @click="viewTask(row)">详情</el-button>
            <el-button
              v-if="row.status === '待接单'" size="small" link type="success" @click="acceptTask(row)"
            >接单</el-button>
            <el-button
              v-if="row.status === '待接单'" size="small" link type="danger" @click="rejectTask(row)"
            >拒单</el-button>
            <el-button
              v-if="row.status === '已接单'" size="small" link type="primary" @click="startTask(row)"
            >开始</el-button>
            <el-button
              v-if="row.status === '进行中'" size="small" link type="success" @click="completeTask(row)"
            >完成</el-button>
          </template>
        </el-table-column>
      </el-table>

      <div style="margin-top: 16px; display: flex; justify-content: space-between; align-items: center;">
        <span style="color: #999; font-size: 13px;">共 {{ filteredTasks.length }} 条</span>
      </div>
    </el-card>

    <!-- 详情弹窗 -->
    <el-dialog v-model="detailVisible" title="任务详情" width="600px">
      <el-descriptions :column="2" border v-if="currentTask">
        <el-descriptions-item label="任务ID">{{ currentTask.id }}</el-descriptions-item>
        <el-descriptions-item label="状态"><el-tag>{{ currentTask.status }}</el-tag></el-descriptions-item>
        <el-descriptions-item label="标题" :span="2">{{ currentTask.title }}</el-descriptions-item>
        <el-descriptions-item label="描述" :span="2">{{ currentTask.description || '-' }}</el-descriptions-item>
        <el-descriptions-item label="优先级">{{ currentTask.priority }}</el-descriptions-item>
        <el-descriptions-item label="截止时间">{{ currentTask.deadline || '-' }}</el-descriptions-item>
        <el-descriptions-item label="位置">{{ currentTask.location || '-' }}</el-descriptions-item>
        <el-descriptions-item label="施工区域">{{ currentTask.section || '-' }}</el-descriptions-item>
        <el-descriptions-item label="创建时间">{{ currentTask.created_at }}</el-descriptions-item>
        <el-descriptions-item label="拒单原因">{{ currentTask.reject_reason || '-' }}</el-descriptions-item>
      </el-descriptions>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { taskApi } from '@/api'
import { Refresh, Search, RefreshRight } from '@element-plus/icons-vue'

const loading = ref(false)
const tasks = ref<any[]>([])
const filterStatus = ref('')
const filterPriority = ref('')
const searchText = ref('')
const detailVisible = ref(false)
const currentTask = ref<any>(null)

const statsCards = ref([
  { label: '全部待办', value: 0, icon: 'Document', bg: '#2563EB' },
  { label: '紧急待办', value: 0, icon: 'WarningFilled', bg: '#F56C6C' },
  { label: '今日提醒', value: 0, icon: 'Timer', bg: '#E6A23C' },
  { label: '已逾期', value: 0, icon: 'CloseBold', bg: '#909399' },
])

const filteredTasks = computed(() => tasks.value.filter((task) => {
  if (filterPriority.value && task.priority !== filterPriority.value) return false
  const keyword = searchText.value.trim().toLowerCase()
  if (keyword && !`${task.title || ''} ${task.description || ''}`.toLowerCase().includes(keyword)) return false
  return true
}))

function statusType(s: string) {
  const m: Record<string, string> = {
    '待接单': 'warning', '已接单': '', '进行中': '',
    '已完成': 'success', '已拒单': 'danger', '已取消': 'info',
  }
  return m[s] || 'info'
}
function priorityType(p: string) {
  const m: Record<string, string> = { '紧急': 'danger', '高': 'warning', '中': '', '低': 'info' }
  return m[p] || ''
}
function isOverdue(row: any) {
  if (!row.deadline || ['已完成', '已取消'].includes(row.status)) return false
  return new Date(row.deadline).getTime() < Date.now()
}

async function loadData() {
  loading.value = true
  try {
    const res = await taskApi.myTasks({ status: filterStatus.value || undefined }) as any
    tasks.value = res.tasks || []
    const now = new Date()
    const today = now.toISOString().slice(0, 10)
    statsCards.value[0].value = tasks.value.filter(t => !['已完成', '已取消', '已拒单'].includes(t.status)).length
    statsCards.value[1].value = tasks.value.filter(t => t.priority === '紧急' && !['已完成', '已取消'].includes(t.status)).length
    statsCards.value[2].value = tasks.value.filter(t => String(t.deadline || '').startsWith(today)).length
    statsCards.value[3].value = tasks.value.filter(isOverdue).length
    // 更新统计
    try {
      await taskApi.todoStats()
    } catch {}
  } catch (e: any) {
    ElMessage.error('加载失败')
  } finally { loading.value = false }
}

function resetFilters() {
  filterStatus.value = ''
  filterPriority.value = ''
  searchText.value = ''
  loadData()
}

function viewTask(row: any) { currentTask.value = row; detailVisible.value = true }

async function acceptTask(row: any) {
  try { await taskApi.respond(row.id, 'accept'); ElMessage.success('已接单'); loadData() }
  catch (e: any) { ElMessage.error(e.response?.data?.detail || '操作失败') }
}

async function rejectTask(row: any) {
  try {
    const reason = await ElMessageBox.prompt('请输入拒单原因', '拒单', { inputType: 'textarea' })
    await taskApi.respond(row.id, 'reject', reason.value)
    ElMessage.success('已拒单'); loadData()
  } catch (e: any) { if (e !== 'cancel') ElMessage.error(e.response?.data?.detail || '操作失败') }
}

async function startTask(row: any) {
  try { await taskApi.start(row.id); ElMessage.success('任务已开始'); loadData() }
  catch (e: any) { ElMessage.error('操作失败') }
}

async function completeTask(row: any) {
  try {
    const remark = await ElMessageBox.prompt('请填写完成回执/说明', '完成任务', {
      inputType: 'textarea',
      inputPlaceholder: '例：巡视完成，发现 2 处隐患已上报...',
      confirmButtonText: '提交',
      cancelButtonText: '取消',
    })
    await taskApi.complete(row.id, { completion_remark: remark.value || '' })
    ElMessage.success('任务已完成')
    loadData()
  } catch (e: any) {
    if (e !== 'cancel' && e !== 'close') ElMessage.error(e.response?.data?.detail || '操作失败')
  }
}

onMounted(() => loadData())
</script>

<style scoped>
.page-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; }
.header-actions { display: flex; gap: 8px; align-items: center; }
.section-title { font-weight: 600; font-size: 15px; }
.stat-box-mini {
  display: flex; align-items: center; gap: 12px;
  padding: 12px 16px; background: #fafafa; border-radius: 8px;
}
.stat-icon-wrap { width: 44px; height: 44px; border-radius: 10px; display: flex; align-items: center; justify-content: center; }
.stat-info { display: flex; flex-direction: column; }
.stat-num { font-size: 24px; font-weight: 700; color: #333; }
.stat-label { font-size: 12px; color: #999; margin-top: 2px; }
.overdue { color: #f56c6c; font-weight: 600; }
</style>
