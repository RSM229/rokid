<template>
  <div class="station-page">
    <div class="page-header">
      <el-breadcrumb separator="/">
        <el-breadcrumb-item>监理工作台</el-breadcrumb-item>
        <el-breadcrumb-item>旁站记录</el-breadcrumb-item>
      </el-breadcrumb>
    </div>

    <!-- 记录列表 -->
    <el-card shadow="never">
      <template #header>
        <div class="card-header">
          <span class="section-title">旁站记录</span>
          <el-button type="primary" size="small" @click="openCreateDialog">
            <el-icon><Plus /></el-icon>
            <span>新建记录</span>
          </el-button>
        </div>
      </template>
      <el-table :data="records" stripe empty-text="暂无记录" size="small">
        <el-table-column prop="id" label="#" width="50" />
        <el-table-column label="工程名称" min-width="140" show-overflow-tooltip>
          <template #default="{ row }">{{ row.station_project_name || '-' }}</template>
        </el-table-column>
        <el-table-column prop="section" label="施工区域" width="110" />
        <el-table-column label="旁站部位" min-width="130" show-overflow-tooltip>
          <template #default="{ row }">{{ row.station_location || '-' }}</template>
        </el-table-column>
        <el-table-column prop="status" label="状态" width="90">
          <template #default="{ row }"><el-tag size="small">{{ row.status }}</el-tag></template>
        </el-table-column>
        <el-table-column prop="created_at" label="创建时间" width="170" />
        <el-table-column label="操作" width="180">
          <template #default="{ row }">
            <el-button size="small" link @click="viewDetail(row)">查看</el-button>
            <el-button size="small" link type="primary" @click="openEdit(row)">编辑</el-button>
            <el-button v-if="row.status === '进行中'" size="small" link type="warning"
              @click="endStation(row)">结束</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- 新建弹窗 -->
    <el-dialog v-model="createDialogVisible" title="新建旁站记录" width="700px">
      <el-form :model="form" label-width="120px">
        <el-row :gutter="16">
          <el-col :span="12">
            <el-form-item label="工程名称"><el-input v-model="form.project_name" /></el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="施工区域"><el-input v-model="form.section" /></el-form-item>
          </el-col>
        </el-row>
        <el-row :gutter="16">
          <el-col :span="12">
            <el-form-item label="旁站部位/工序"><el-input v-model="form.station_location" /></el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="开始时间">
              <el-date-picker v-model="form.start_time" type="datetime" placeholder="选择时间" style="width:100%" />
            </el-form-item>
          </el-col>
        </el-row>
        <el-form-item label="结束时间">
          <el-date-picker v-model="form.end_time" type="datetime" placeholder="选择时间" style="width:100%" />
        </el-form-item>
        <el-form-item label="旁站内容描述"><el-input v-model="form.content" type="textarea" :rows="3" /></el-form-item>
        <el-form-item label="发现问题及处理"><el-input v-model="form.issues" type="textarea" :rows="2" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="handleSave">保存</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="detailDialogVisible" title="旁站记录详情" width="700px">
      <el-descriptions v-if="currentDetail" :column="2" border>
        <el-descriptions-item label="记录编号">#{{ currentDetail.id }}</el-descriptions-item>
        <el-descriptions-item label="状态">{{ currentDetail.status }}</el-descriptions-item>
        <el-descriptions-item label="工程名称">{{ currentDetail.station_project_name || '-' }}</el-descriptions-item>
        <el-descriptions-item label="施工区域">{{ currentDetail.section || '-' }}</el-descriptions-item>
        <el-descriptions-item label="旁站部位">{{ currentDetail.station_location || '-' }}</el-descriptions-item>
        <el-descriptions-item label="开始时间">{{ currentDetail.station_start_time || '-' }}</el-descriptions-item>
        <el-descriptions-item label="结束时间" :span="2">{{ currentDetail.station_end_time || '-' }}</el-descriptions-item>
        <el-descriptions-item label="旁站内容" :span="2">{{ currentDetail.station_content || '-' }}</el-descriptions-item>
        <el-descriptions-item label="旁站结论" :span="2">{{ currentDetail.station_conclusion || '-' }}</el-descriptions-item>
      </el-descriptions>
    </el-dialog>

    <!-- 编辑弹窗 -->
    <el-dialog v-model="editDialogVisible" title="编辑旁站记录" width="600px">
      <el-form :model="editForm" label-width="120px">
        <el-row :gutter="16">
          <el-col :span="12">
            <el-form-item label="工程名称"><el-input v-model="editForm.station_project_name" /></el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="施工区域"><el-input v-model="editForm.section" /></el-form-item>
          </el-col>
        </el-row>
        <el-row :gutter="16">
          <el-col :span="12">
            <el-form-item label="旁站部位/工序"><el-input v-model="editForm.station_location" /></el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="状态">
              <el-select v-model="editForm.status" style="width: 100%;">
                <el-option label="进行中" value="进行中" />
                <el-option label="已提交" value="已提交" />
                <el-option label="草稿" value="草稿" />
              </el-select>
            </el-form-item>
          </el-col>
        </el-row>
        <el-form-item label="旁站内容描述"><el-input v-model="editForm.station_content" type="textarea" :rows="3" /></el-form-item>
        <el-form-item label="旁站结论"><el-input v-model="editForm.station_conclusion" type="textarea" :rows="2" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="editDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="editSaving" @click="handleEditSave">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { Plus } from '@element-plus/icons-vue'
import { workRecordApi } from '@/api'
import { ensureWritableProject } from '@/composables/useAllProjectsGuard'

const records = ref<any[]>([])
const saving = ref(false)
const detailDialogVisible = ref(false)
const currentDetail = ref<any>(null)

const createDialogVisible = ref(false)
const form = reactive({
  project_name: '', section: '', station_location: '',
  start_time: null as Date | string | null,
  end_time: null as Date | string | null,
  content: '', issues: '',
})

const editDialogVisible = ref(false)
const editSaving = ref(false)
const editingId = ref<number | null>(null)
const editForm = reactive({
  station_project_name: '', section: '', station_location: '',
  station_content: '', station_conclusion: '', status: '',
})

async function openCreateDialog() {
  if (!(await ensureWritableProject('新建旁站记录'))) return
  resetForm()
  createDialogVisible.value = true
}

function resetForm() {
  Object.assign(form, {
    project_name: '', section: '', station_location: '',
    start_time: null, end_time: null, content: '', issues: '',
  })
}

async function handleSave() {
  if (!form.project_name) return ElMessage.warning('请填写工程名称')
  saving.value = true
  try {
    await workRecordApi.createStation({
      station_project_name: form.project_name,
      section: form.section,
      station_location: form.station_location,
      station_content: form.content,
      station_conclusion: form.issues,
      start_time: form.start_time instanceof Date ? form.start_time.toISOString() : form.start_time || undefined,
      end_time: form.end_time instanceof Date ? form.end_time.toISOString() : form.end_time || undefined,
    })
    ElMessage.success('旁站记录已保存')
    createDialogVisible.value = false
    resetForm()
    loadRecords()
  } catch (e: any) { ElMessage.error(e.response?.data?.detail || '失败') }
  finally { saving.value = false }
}

async function endStation(row: any) {
  try {
    await workRecordApi.endStation(row.id)
    ElMessage.success('旁站已结束')
    loadRecords()
  } catch (e: any) { ElMessage.error('操作失败') }
}

async function loadRecords() {
  try { records.value = (await workRecordApi.list({ record_type: '旁站' }) as any).records || [] }
  catch {}
}

async function viewDetail(row: any) {
  try {
    currentDetail.value = await workRecordApi.detail(row.id)
    detailDialogVisible.value = true
  } catch (e: any) { ElMessage.error(e.response?.data?.detail || '获取详情失败') }
}

async function openEdit(row: any) {
  try {
    const detail: any = await workRecordApi.detail(row.id)
    editingId.value = row.id
    Object.assign(editForm, {
      station_project_name: detail.station_project_name || '',
      section: detail.section || '',
      station_location: detail.station_location || '',
      station_content: detail.station_content || '',
      station_conclusion: detail.station_conclusion || '',
      status: detail.status || '',
    })
    editDialogVisible.value = true
  } catch (e: any) { ElMessage.error('获取详情失败') }
}

async function handleEditSave() {
  if (!editingId.value) return
  editSaving.value = true
  try {
    await workRecordApi.update(editingId.value, editForm)
    ElMessage.success('保存成功')
    editDialogVisible.value = false
    loadRecords()
  } catch (e: any) { ElMessage.error(e.response?.data?.detail || '保存失败') }
  finally { editSaving.value = false }
}

onMounted(() => loadRecords())
</script>

<style scoped>
.page-header { margin-bottom: 16px; }
.page-desc { color: #999; font-size: 13px; margin-bottom: 16px; }
.section-title { font-weight: 600; font-size: 15px; }
.card-header { display: flex; justify-content: space-between; align-items: center; }
</style>
