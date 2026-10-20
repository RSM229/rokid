<template>
  <div class="task-manage-page">
    <div class="page-header">
      <el-breadcrumb separator="/">
        <el-breadcrumb-item>系统设置</el-breadcrumb-item>
        <el-breadcrumb-item>派单管理</el-breadcrumb-item>
      </el-breadcrumb>
    </div>

    <p class="page-desc">总监/总代派单给工程师/监理员，被指派人可选择接单或拒单；支持转派、催办、眼镜端实时通知。</p>

    <!-- 通知铃铛 -->
    <div class="notify-bar">
      <el-badge :value="unreadCount" :hidden="unreadCount === 0" :max="99">
        <el-button :type="unreadCount > 0 ? 'warning' : 'default'" @click="showNotifDrawer = true">
          <el-icon><Bell /></el-icon> 通知
        </el-button>
      </el-badge>
      <span style="color:#999; font-size:12px; margin-left:12px;">
        每 15 秒自动拉取眼镜端通知
      </span>
    </div>

    <!-- 统计卡片 -->
    <el-row :gutter="12" style="margin-bottom:16px;">
      <el-col :span="4" v-for="s in statsList" :key="s.key">
        <el-card shadow="never" class="stat-mini">
          <div class="stat-mini-num">{{ s.value }}</div>
          <div class="stat-mini-label">{{ s.label }}</div>
        </el-card>
      </el-col>
    </el-row>

    <!-- 新建派单 -->
    <el-card shadow="never" style="margin-bottom: 16px;">
      <template #header>
        <span class="section-title">{{ editingTask ? '编辑任务' : '新建派单' }}</span>
      </template>
      <el-form :model="form" label-width="100px" style="max-width: 760px;">
        <el-form-item label="任务标题" required><el-input v-model="form.title" placeholder="请输入任务标题" /></el-form-item>
        <el-form-item label="任务描述"><el-input v-model="form.description" type="textarea" :rows="3" /></el-form-item>
        <el-row :gutter="16">
          <el-col :span="12">
            <el-form-item label="指派给" required>
              <el-select v-model="form.assignee_id" filterable placeholder="选择接收人" style="width:100%">
                <el-option
                  v-for="u in assignableUsers"
                  :key="u.id"
                  :label="`${u.real_name}（${u.role}）`"
                  :value="u.id"
                />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :span="6">
            <el-form-item label="类别">
              <el-select v-model="form.category" style="width:100%">
                <el-option v-for="c in CATEGORIES" :key="c" :label="c" :value="c" />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :span="6">
            <el-form-item label="优先级">
              <el-select v-model="form.priority" style="width:100%">
                <el-option value="低" label="低" />
                <el-option value="中" label="中" />
                <el-option value="高" label="高" />
                <el-option value="紧急" label="紧急" />
              </el-select>
            </el-form-item>
          </el-col>
        </el-row>
        <el-row :gutter="16">
          <el-col :span="12">
            <el-form-item label="截止时间">
              <el-date-picker v-model="form.deadline" type="datetime" placeholder="选择截止时间" style="width:100%" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="施工区域"><el-input v-model="form.section" /></el-form-item>
          </el-col>
        </el-row>
        <el-form-item label="具体位置"><el-input v-model="form.location" /></el-form-item>
        <el-form-item>
          <el-checkbox v-model="form.notify_glasses">同步推送到眼镜端</el-checkbox>
        </el-form-item>
        <el-form-item>
          <el-button type="primary" @click="handleCreate" :loading="saving">{{ editingTask ? '更新' : '创建派单' }}</el-button>
          <el-button v-if="editingTask" @click="resetForm">取消编辑</el-button>
        </el-form-item>
      </el-form>
    </el-card>

    <!-- 任务列表 -->
    <el-card shadow="never">
      <template #header>
        <div style="display:flex; justify-content:space-between; align-items:center;">
          <span class="section-title">我派出的任务（{{ tasks.length }}）</span>
          <div style="display:flex; gap:8px; align-items:center;">
            <el-select v-model="filterStatus" clearable size="small" placeholder="状态筛选" style="width:120px">
              <el-option v-for="s in STATUSES" :key="s" :label="s" :value="s" />
            </el-select>
            <el-select v-model="filterCategory" clearable size="small" placeholder="类别筛选" style="width:130px">
              <el-option v-for="c in CATEGORIES" :key="c" :label="c" :value="c" />
            </el-select>
            <el-button size="small" @click="loadTasks">刷新</el-button>
          </div>
        </div>
      </template>

      <el-table :data="filteredTasks" stripe v-loading="loading" empty-text="暂无任务" size="small">
        <el-table-column prop="id" label="#" width="50" />
        <el-table-column prop="title" label="任务标题" min-width="180" show-overflow-tooltip />
        <el-table-column prop="category" label="类别" width="120">
          <template #default="{ row }"><el-tag size="small" type="info">{{ row.category || '其他' }}</el-tag></template>
        </el-table-column>
        <el-table-column prop="assignee_name" label="接收人" width="100" />
        <el-table-column prop="status" label="状态" width="90">
          <template #default="{ row }"><el-tag :type="statusType(row.status)" size="small">{{ row.status }}</el-tag></template>
        </el-table-column>
        <el-table-column prop="priority" label="优先级" width="80">
          <template #default="{ row }"><el-tag :type="priorityType(row.priority)" size="small">{{ row.priority }}</el-tag></template>
        </el-table-column>
        <el-table-column prop="deadline" label="截止日期" width="170">
          <template #default="{ row }">
            <span :class="{ overdue: isOverdue(row) }">{{ row.deadline || '-' }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="created_at" label="创建时间" width="170" />
        <el-table-column label="操作" width="280" fixed="right">
          <template #default="{ row }">
            <el-button size="small" link type="primary" @click="viewDetail(row)">详情</el-button>
            <el-button
              v-if="['待接单','已接单','进行中'].includes(row.status)"
              size="small" link type="warning"
              @click="urgeTask(row)"
            >催办</el-button>
            <el-button
              v-if="['待接单','已拒单'].includes(row.status)"
              size="small" link
              @click="openReassignDialog(row)"
            >转派</el-button>
            <el-button
              v-if="row.status === '已拒单' && row.assignee_id"
              size="small" link type="primary"
              @click="resendTask(row)"
            >重派</el-button>
            <el-button
              v-if="!['已完成','已取消'].includes(row.status)"
              size="small" link type="danger"
              @click="cancelTask(row)"
            >取消</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- 详情弹窗 -->
    <el-dialog v-model="detailVisible" title="任务详情" width="650px">
      <el-descriptions :column="2" border v-if="currentTask" size="small">
        <el-descriptions-item label="ID">{{ currentTask.id }}</el-descriptions-item>
        <el-descriptions-item label="状态"><el-tag :type="statusType(currentTask.status)">{{ currentTask.status }}</el-tag></el-descriptions-item>
        <el-descriptions-item label="类别">{{ currentTask.category }}</el-descriptions-item>
        <el-descriptions-item label="优先级">{{ currentTask.priority }}</el-descriptions-item>
        <el-descriptions-item label="标题" :span="2">{{ currentTask.title }}</el-descriptions-item>
        <el-descriptions-item label="描述" :span="2">{{ currentTask.description || '-' }}</el-descriptions-item>
        <el-descriptions-item label="接收人">{{ currentTask.assignee_name || '-' }}</el-descriptions-item>
        <el-descriptions-item label="派单人">#{{ currentTask.assigner_id }}</el-descriptions-item>
        <el-descriptions-item label="截止时间">{{ currentTask.deadline || '-' }}</el-descriptions-item>
        <el-descriptions-item label="接单时间">{{ currentTask.accepted_at || '-' }}</el-descriptions-item>
        <el-descriptions-item label="开始时间">{{ currentTask.started_at || '-' }}</el-descriptions-item>
        <el-descriptions-item label="完成时间">{{ currentTask.completed_at || '-' }}</el-descriptions-item>
        <el-descriptions-item label="施工区域">{{ currentTask.section || '-' }}</el-descriptions-item>
        <el-descriptions-item label="具体位置">{{ currentTask.location || '-' }}</el-descriptions-item>
        <el-descriptions-item label="拒单原因" :span="2">{{ currentTask.reject_reason || '-' }}</el-descriptions-item>
        <el-descriptions-item label="完成回执" :span="2">{{ currentTask.completion_remark || '-' }}</el-descriptions-item>
        <el-descriptions-item label="附件" :span="2">
          <div v-if="currentTask.attachments && currentTask.attachments.length">
            <a v-for="(a, i) in currentTask.attachments" :key="i" :href="a" target="_blank" style="margin-right:8px;">
              📎 附件{{ Number(i) + 1 }}
            </a>
          </div>
          <span v-else>-</span>
        </el-descriptions-item>
        <el-descriptions-item label="创建时间" :span="2">{{ currentTask.created_at }}</el-descriptions-item>
      </el-descriptions>
    </el-dialog>

    <!-- 转派弹窗 -->
    <el-dialog v-model="reassignVisible" title="转派任务" width="450px">
      <el-form label-width="100px">
        <el-form-item label="原接收人">
          <el-input v-model="reassignForm.old_assignee_name" disabled />
        </el-form-item>
        <el-form-item label="新接收人" required>
          <el-select v-model="reassignForm.new_assignee_id" filterable placeholder="选择新接收人" style="width:100%">
            <el-option
              v-for="u in assignableUsers"
              :key="u.id"
              :label="`${u.real_name}（${u.role}）`"
              :value="u.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="转派原因"><el-input v-model="reassignForm.reason" type="textarea" :rows="2" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="reassignVisible = false">取消</el-button>
        <el-button type="primary" @click="confirmReassign">确认转派</el-button>
      </template>
    </el-dialog>

    <!-- 通知抽屉 -->
    <el-drawer v-model="showNotifDrawer" title="实时通知" size="380px" direction="rtl">
      <div v-if="notifications.length === 0" class="empty-state">
        <el-icon :size="48" color="#ddd"><BellFilled /></el-icon>
        <p>暂无新通知</p>
      </div>
      <el-timeline v-else>
        <el-timeline-item
          v-for="(n, i) in notifications" :key="i"
          :timestamp="formatTime(n.ts)" placement="top"
          :type="n.payload?.type?.includes('urge') ? 'warning' : 'primary'"
        >
          <div class="notif-title">{{ n.title }}</div>
          <div class="notif-body">{{ n.body }}</div>
        </el-timeline-item>
      </el-timeline>
      <div style="margin-top: 16px; text-align: center;">
        <el-button size="small" @click="clearNotifications">清空通知</el-button>
      </div>
    </el-drawer>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed, onMounted, onUnmounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { taskApi } from '@/api'
import { ensureWritableProject } from '@/composables/useAllProjectsGuard'
import { Bell, BellFilled } from '@element-plus/icons-vue'

const CATEGORIES = ['巡视检查', '验收管理', '见证取样', '旁站监督', '隐患处理', '文书处理', '整改跟踪', '其他事务']
const STATUSES = ['待接单', '已接单', '进行中', '已完成', '已拒单', '已取消']

const loading = ref(false)
const saving = ref(false)
const tasks = ref<any[]>([])
const assignableUsers = ref<any[]>([])
const filterStatus = ref('')
const filterCategory = ref('')
const detailVisible = ref(false)
const currentTask = ref<any>(null)
const editingTask = ref<any>(null)

const reassignVisible = ref(false)
const reassignForm = reactive({ task_id: 0, old_assignee_name: '', new_assignee_id: null as number | null, reason: '' })

const showNotifDrawer = ref(false)
const notifications = ref<any[]>([])
let pollTimer: any = null

const form = reactive({
  title: '', description: '', assignee_id: null as number | null,
  priority: '中', category: '其他事务', deadline: null as Date | string | null,
  section: '', location: '', notify_glasses: true,
})

const statsList = ref([
  { key: 'pending', label: '待接单', value: 0 },
  { key: 'accepted', label: '已接单', value: 0 },
  { key: 'progress', label: '进行中', value: 0 },
  { key: 'completed', label: '已完成', value: 0 },
  { key: 'rejected', label: '已拒单', value: 0 },
  { key: 'cancelled', label: '已取消', value: 0 },
])

const unreadCount = computed(() => notifications.value.length)

const filteredTasks = computed(() => {
  return tasks.value.filter(t => {
    if (filterStatus.value && t.status !== filterStatus.value) return false
    if (filterCategory.value && t.category !== filterCategory.value) return false
    return true
  })
})

function statusType(s: string) {
  const m: Record<string, string> = {
    '待接单': 'warning', '已接单': '', '进行中': 'primary',
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
function formatTime(ts: number) {
  return new Date(ts * 1000).toLocaleString('zh-CN', { hour12: false })
}

async function loadAssignableUsers() {
  try { assignableUsers.value = (await taskApi.assignableUsers() as any) || [] } catch {}
}

async function loadStats() {
  try {
    const s: any = await taskApi.todoStats()
    statsList.value[0].value = s.pending_receive || 0
    statsList.value[1].value = 0
    statsList.value[2].value = s.in_progress || 0
    statsList.value[3].value = s.completed || 0
    statsList.value[4].value = s.rejected || 0
    statsList.value[5].value = 0
  } catch {}
}

async function handleCreate() {
  if (!(await ensureWritableProject('派单'))) return
  if (!form.title) return ElMessage.warning('请填写任务标题')
  if (!form.assignee_id) return ElMessage.warning('请选择接收人')
  saving.value = true
  try {
    const payload: any = {
      ...form,
      deadline: form.deadline instanceof Date ? form.deadline.toISOString() : form.deadline,
    }
    await taskApi.create(payload)
    ElMessage.success('派单成功')
    resetForm()
    loadTasks(); loadStats()
  } catch (e: any) { ElMessage.error(e.response?.data?.detail || '操作失败') }
  finally { saving.value = false }
}

function editTask(row: any) {
  editingTask.value = row
  form.title = row.title
  form.description = row.description || ''
  form.assignee_id = row.assignee_id
  form.priority = row.priority
  form.category = row.category || '其他事务'
  form.deadline = row.deadline || ''
  form.section = row.section || ''
  form.location = row.location || ''
  form.notify_glasses = row.notify_glasses ?? true
}

function resetForm() {
  editingTask.value = null
  Object.assign(form, { title: '', description: '', assignee_id: null,
    priority: '中', category: '其他事务', deadline: '',
    section: '', location: '', notify_glasses: true })
}

function viewDetail(row: any) {
  taskApi.detail(row.id).then((d: any) => {
    currentTask.value = d
    detailVisible.value = true
  }).catch(() => { currentTask.value = row; detailVisible.value = true })
}

async function cancelTask(row: any) {
  try {
    await ElMessageBox.confirm(`确定取消任务「${row.title}」？`, '确认', { type: 'warning' })
    await taskApi.cancel(row.id)
    ElMessage.success('已取消')
    loadTasks()
  } catch (e: any) { if (e !== 'cancel') ElMessage.error('操作失败') }
}

async function urgeTask(row: any) {
  try {
    await taskApi.urge(row.id)
    ElMessage.success('催办通知已发送')
  } catch (e: any) { ElMessage.error('催办失败') }
}

function openReassignDialog(row: any) {
  reassignForm.task_id = row.id
  reassignForm.old_assignee_name = row.assignee_name || ''
  reassignForm.new_assignee_id = null
  reassignForm.reason = ''
  reassignVisible.value = true
}

async function confirmReassign() {
  if (!reassignForm.new_assignee_id) return ElMessage.warning('请选择新接收人')
  try {
    await taskApi.reassign(reassignForm.task_id, {
      new_assignee_id: reassignForm.new_assignee_id,
      reason: reassignForm.reason,
    })
    ElMessage.success('转派成功')
    reassignVisible.value = false
    loadTasks()
  } catch (e: any) { ElMessage.error('转派失败') }
}

async function resendTask(row: any) {
  try {
    await ElMessageBox.confirm(`重新将任务「${row.title}」派给 ${row.assignee_name}？`, '确认', { type: 'info' })
    await taskApi.reassign(row.id, { new_assignee_id: row.assignee_id, reason: '拒单后重派' })
    ElMessage.success('已重新派发')
    loadTasks()
  } catch (e: any) { if (e !== 'cancel') ElMessage.error('重派失败') }
}

async function loadTasks() {
  loading.value = true
  try {
    tasks.value = (await taskApi.assignedByMe({ status: filterStatus.value || undefined }) as any).tasks || []
  } catch {} finally { loading.value = false }
}

async function loadNotifications() {
  try {
    const res: any = await taskApi.notifications()
    notifications.value = res.notifications || []
  } catch {}
}

async function clearNotifications() {
  try {
    await taskApi.clearNotifications()
    notifications.value = []
    ElMessage.success('已清空')
  } catch {}
}

onMounted(() => {
  loadTasks(); loadAssignableUsers(); loadStats(); loadNotifications()
  pollTimer = setInterval(loadNotifications, 15000)
})
onUnmounted(() => { if (pollTimer) clearInterval(pollTimer) })
</script>

<style scoped>
.page-header { margin-bottom: 8px; }
.page-desc { color: #999; font-size: 13px; margin-bottom: 12px; }
.section-title { font-weight: 600; font-size: 15px; }
.notify-bar { margin-bottom: 12px; display: flex; align-items: center; }
.stat-mini { height: 80px; display: flex; align-items: center; justify-content: center; flex-direction: column; }
.stat-mini-num { font-size: 22px; font-weight: 700; color: #333; }
.stat-mini-label { font-size: 12px; color: #999; margin-top: 2px; }
.empty-state { display: flex; flex-direction: column; align-items: center; padding: 60px 0; color: #999; }
.empty-state p { margin-top: 8px; font-size: 13px; }
.notif-title { font-weight: 600; font-size: 14px; color: #333; margin-bottom: 4px; }
.notif-body { font-size: 13px; color: #666; }
.overdue { color: #f56c6c; font-weight: 600; }
</style>
