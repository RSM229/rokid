<template>
  <div class="acceptance-page">
    <div class="page-header">
      <el-breadcrumb separator="/">
        <el-breadcrumb-item>监理工作台</el-breadcrumb-item>
        <el-breadcrumb-item>验收管理</el-breadcrumb-item>
      </el-breadcrumb>
    </div>

    <!-- 记录列表 -->
    <el-card shadow="never">
      <template #header>
        <div class="card-header">
          <span class="section-title">验收记录</span>
          <el-button type="primary" size="small" @click="openCreateDialog">
            <el-icon><Plus /></el-icon>
            <span>新建记录</span>
          </el-button>
        </div>
      </template>
      <el-table :data="records" stripe empty-text="暂无记录" size="small">
        <el-table-column prop="id" label="#" width="50" />
        <el-table-column label="验收类型" width="140">
          <template #default="{ row }">{{ row.content_json?.acceptance_type || '-' }}</template>
        </el-table-column>
        <el-table-column prop="section" label="施工区域" width="120" />
        <el-table-column prop="status" label="状态" width="90">
          <template #default="{ row }"><el-tag size="small">{{ row.status }}</el-tag></template>
        </el-table-column>
        <el-table-column prop="created_at" label="创建时间" width="170" />
        <el-table-column label="操作" width="200">
          <template #default="{ row }">
            <el-button size="small" link @click="viewDetail(row)">查看</el-button>
            <el-button size="small" link type="primary" @click="openEdit(row)">编辑</el-button>
            <el-button size="small" link type="primary" @click="downloadForm(row)">下载统表</el-button>
            <el-button v-if="row.status === '草稿'" size="small" link type="success" @click="submitRecord(row)">提交</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- 新建弹窗 -->
    <el-dialog v-model="createDialogVisible" title="新建验收记录" width="600px">
      <el-form :model="form" label-width="100px">
        <el-form-item label="验收类型">
          <el-select v-model="form.acceptance_type" placeholder="选择验收类型" style="width: 100%;">
            <el-option value="隐蔽工程验收" label="隐蔽工程验收" />
            <el-option value="检验批验收" label="检验批验收" />
            <el-option value="分项工程验收" label="分项工程验收" />
            <el-option value="分部工程验收" label="分部工程验收" />
            <el-option value="单位工程验收" label="单位工程验收" />
          </el-select>
        </el-form-item>
        <el-row :gutter="16">
          <el-col :span="12"><el-form-item label="施工区域"><el-input v-model="form.section" /></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="具体位置"><el-input v-model="form.location" /></el-form-item></el-col>
        </el-row>
        <el-form-item label="验收内容"><el-input v-model="form.acceptance_content" type="textarea" :rows="3" /></el-form-item>
        <el-form-item label="参考流程">
          <el-select v-model="form.guide_ref" placeholder="从知识图谱选择验收流程（可选）" filterable clearable style="width: 100%;" :loading="guidesLoading">
            <el-option v-for="(g, i) in guides" :key="i" :label="g.title || g.name" :value="g.id" />
          </el-select>
          <div v-if="!guidesLoading && guides.length === 0" class="no-data-tip">
            知识图谱暂无验收流程数据，可直接填写验收内容创建记录
          </div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="handleSave">保存并生成统表</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="detailDialogVisible" title="验收记录详情" width="700px">
      <el-descriptions v-if="currentDetail" :column="2" border>
        <el-descriptions-item label="记录编号">#{{ currentDetail.id }}</el-descriptions-item>
        <el-descriptions-item label="状态">{{ currentDetail.status }}</el-descriptions-item>
        <el-descriptions-item label="验收类型">{{ currentDetail.content_json?.acceptance_type || '-' }}</el-descriptions-item>
        <el-descriptions-item label="验收结果">{{ currentDetail.acceptance_result || '-' }}</el-descriptions-item>
        <el-descriptions-item label="施工区域">{{ currentDetail.section || '-' }}</el-descriptions-item>
        <el-descriptions-item label="具体位置">{{ currentDetail.location || '-' }}</el-descriptions-item>
        <el-descriptions-item label="验收内容" :span="2">{{ currentDetail.content_json?.acceptance_content || '-' }}</el-descriptions-item>
        <el-descriptions-item label="引用流程" :span="2">{{ currentDetail.acceptance_guide_ref || '-' }}</el-descriptions-item>
      </el-descriptions>
    </el-dialog>

    <!-- 编辑弹窗 -->
    <el-dialog v-model="editDialogVisible" title="编辑验收记录" width="600px">
      <el-form :model="editForm" label-width="100px">
        <el-form-item label="验收类型">
          <el-select v-model="editForm.acceptance_type" style="width: 100%;">
            <el-option value="隐蔽工程验收" label="隐蔽工程验收" />
            <el-option value="检验批验收" label="检验批验收" />
            <el-option value="分项工程验收" label="分项工程验收" />
            <el-option value="分部工程验收" label="分部工程验收" />
            <el-option value="单位工程验收" label="单位工程验收" />
          </el-select>
        </el-form-item>
        <el-row :gutter="16">
          <el-col :span="12"><el-form-item label="施工区域"><el-input v-model="editForm.section" /></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="具体位置"><el-input v-model="editForm.location" /></el-form-item></el-col>
        </el-row>
        <el-form-item label="验收内容"><el-input v-model="editForm.acceptance_content" type="textarea" :rows="3" /></el-form-item>
        <el-form-item label="验收结果"><el-input v-model="editForm.acceptance_result" type="textarea" :rows="2" /></el-form-item>
        <el-form-item label="状态">
          <el-select v-model="editForm.status" style="width: 100%;">
            <el-option label="草稿" value="草稿" />
            <el-option label="已提交" value="已提交" />
          </el-select>
        </el-form-item>
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
import { documentApi, workRecordApi, downloadFile } from '@/api'
import { ensureWritableProject } from '@/composables/useAllProjectsGuard'

const records = ref<any[]>([])
const guides = ref<any[]>([])
const guidesLoading = ref(false)
const saving = ref(false)
const detailDialogVisible = ref(false)
const currentDetail = ref<any>(null)

const createDialogVisible = ref(false)
const editDialogVisible = ref(false)
const editSaving = ref(false)
const editingId = ref<number | null>(null)
const editForm = reactive({
  acceptance_type: '', section: '', location: '',
  acceptance_content: '', acceptance_result: '', status: '',
})

const form = reactive({
  acceptance_type: '', section: '', location: '',
  acceptance_content: '', guide_ref: '' as string | number,
})

async function openCreateDialog() {
  if (!(await ensureWritableProject('新建验收记录'))) return
  resetForm()
  createDialogVisible.value = true
}

function resetForm() {
  Object.assign(form, {
    acceptance_type: '', section: '', location: '',
    acceptance_content: '', guide_ref: '',
  })
}

async function handleSave() {
  if (!form.acceptance_type) return ElMessage.warning('请选择验收类型')
  saving.value = true
  try {
    const res: any = await workRecordApi.createAcceptance({
      acceptance_type: form.acceptance_type,
      section: form.section,
      location: form.location,
      acceptance_content: form.acceptance_content,
      guide_ref: form.guide_ref || undefined,
    })
    ElMessage.success(res.detail || '验收记录已创建')
    createDialogVisible.value = false
    resetForm()
    loadRecords()
  } catch (e: any) { ElMessage.error(e.response?.data?.detail || '保存失败') }
  finally { saving.value = false }
}

async function loadRecords() {
  try { records.value = (await workRecordApi.list({ record_type: '验收' }) as any).records || [] }
  catch {}
}

async function viewDetail(row: any) {
  try {
    currentDetail.value = await workRecordApi.detail(row.id)
    detailDialogVisible.value = true
  } catch (e: any) { ElMessage.error(e.response?.data?.detail || '获取详情失败') }
}

async function submitRecord(row: any) {
  try {
    await workRecordApi.update(row.id, { status: '已提交' })
    ElMessage.success('验收记录已提交')
    loadRecords()
  } catch (e: any) { ElMessage.error(e.response?.data?.detail || '提交失败') }
}

async function downloadForm(row: any) {
  if (!row.generated_doc_id) {
    ElMessage.warning('该记录尚未生成验收统表')
    return
  }
  try {
    const result: any = await documentApi.exportPdf(row.generated_doc_id)
    if (result.pdf_url) downloadFile(result.pdf_url)
  } catch (e: any) { ElMessage.error(e.response?.data?.detail || '下载失败') }
}

async function openEdit(row: any) {
  try {
    const detail: any = await workRecordApi.detail(row.id)
    editingId.value = row.id
    Object.assign(editForm, {
      acceptance_type: detail.content_json?.acceptance_type || '',
      section: detail.section || '',
      location: detail.location || '',
      acceptance_content: detail.content_json?.acceptance_content || '',
      acceptance_result: detail.acceptance_result || '',
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

onMounted(async () => {
  loadRecords()
  // 加载知识图谱中的验收流程指引
  guidesLoading.value = true
  try { guides.value = (await workRecordApi.listGuides({ category: '验收流程' }) as any).guides || [] }
  catch {}
  finally { guidesLoading.value = false }
})
</script>

<style scoped>
.page-header { margin-bottom: 16px; }
.page-desc { color: #999; font-size: 13px; margin-bottom: 16px; }
.section-title { font-weight: 600; font-size: 15px; }
.no-data-tip { font-size: 12px; color: #909399; margin-top: 4px; }
.card-header { display: flex; justify-content: space-between; align-items: center; }
</style>
