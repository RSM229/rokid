<template>
  <div>
    <div class="page-header">
      <div>
        <h2>验收流程模板配置</h2>
        <p>维护各类工程验收的标准步骤、必查要点、常见违规问题及规范依据，供移动端验收调用。</p>
      </div>
      <el-button type="primary" @click="openCreate">新增模板</el-button>
    </div>

    <el-card shadow="never">
      <el-table :data="templates" v-loading="loading" stripe>
        <el-table-column prop="id" label="ID" width="60" />
        <el-table-column prop="acceptance_type" label="验收类型" width="140">
          <template #default="{ row }">
            <el-tag size="small">{{ row.acceptance_type }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="title" label="标题" min-width="220" />
        <el-table-column label="步骤数" width="100">
          <template #default="{ row }">{{ row.standard_steps?.length || 0 }}</template>
        </el-table-column>
        <el-table-column label="管控要点" width="100">
          <template #default="{ row }">{{ row.control_points?.length || 0 }}</template>
        </el-table-column>
        <el-table-column prop="updated_at" label="更新时间" width="180" />
        <el-table-column label="操作" width="240" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" @click="openPreview(row)">预览</el-button>
            <el-button link type="primary" @click="openEdit(row)">编辑</el-button>
            <el-button link type="danger" @click="confirmDelete(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- 新增/编辑模板对话框 -->
    <el-dialog
      v-model="formVisible"
      :title="editingId ? '编辑验收模板' : '新增验收模板'"
      width="820px"
      :close-on-click-modal="false"
    >
      <el-form ref="formRef" :model="form" :rules="formRules" label-width="110px">
        <el-form-item label="验收类型" prop="acceptance_type">
          <el-select v-model="form.acceptance_type" placeholder="请选择验收类型" style="width: 100%;">
            <el-option v-for="t in acceptanceTypes" :key="t" :label="t" :value="t" />
          </el-select>
        </el-form-item>
        <el-form-item label="标题" prop="title">
          <el-input v-model="form.title" placeholder="如：钢筋工程验收模板" />
        </el-form-item>

        <!-- 标准步骤 -->
        <el-divider content-position="left">标准步骤</el-divider>
        <div v-for="(step, idx) in form.standard_steps" :key="'s' + idx" class="dynamic-row">
          <el-input
            v-model="step.step_description"
            placeholder="请输入步骤说明，如：检查钢筋规格、数量、间距"
            style="flex: 1;"
          />
          <el-button type="danger" link @click="removeStep(idx)">
            <el-icon><Delete /></el-icon>
          </el-button>
        </div>
        <el-button type="primary" link @click="addStep">
          <el-icon><Plus /></el-icon>添加步骤
        </el-button>

        <!-- 必查管控要点 -->
        <el-divider content-position="left">必查管控要点</el-divider>
        <div v-for="(point, idx) in form.control_points" :key="'c' + idx" class="dynamic-row">
          <el-input
            v-model="form.control_points[idx]"
            placeholder="请输入管控要点"
            style="flex: 1;"
          />
          <el-button type="danger" link @click="removePoint(idx)">
            <el-icon><Delete /></el-icon>
          </el-button>
        </div>
        <el-button type="primary" link @click="addPoint">
          <el-icon><Plus /></el-icon>添加管控要点
        </el-button>

        <!-- 常见违规问题 -->
        <el-divider content-position="left">常见违规问题</el-divider>
        <div v-for="(issue, idx) in form.common_violations" :key="'v' + idx" class="dynamic-row">
          <el-input
            v-model="form.common_violations[idx]"
            placeholder="请输入常见违规问题"
            style="flex: 1;"
          />
          <el-button type="danger" link @click="removeViolation(idx)">
            <el-icon><Delete /></el-icon>
          </el-button>
        </div>
        <el-button type="primary" link @click="addViolation">
          <el-icon><Plus /></el-icon>添加违规问题
        </el-button>

        <!-- 规范依据 -->
        <el-divider content-position="left">规范依据</el-divider>
        <div
          v-for="(reg, idx) in form.regulations"
          :key="'r' + idx"
          class="dynamic-row regulation-row"
        >
          <el-input
            v-model="reg.regulation_title"
            placeholder="规范名称"
            style="width: 45%;"
          />
          <el-input
            v-model="reg.clause"
            placeholder="条款号，如 5.3.2"
            style="width: 45%;"
          />
          <el-button type="danger" link @click="removeRegulation(idx)">
            <el-icon><Delete /></el-icon>
          </el-button>
        </div>
        <el-button type="primary" link @click="addRegulation">
          <el-icon><Plus /></el-icon>添加规范依据
        </el-button>
      </el-form>
      <template #footer>
        <el-button @click="formVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="submitForm">保存</el-button>
      </template>
    </el-dialog>

    <!-- 模板预览对话框 -->
    <el-dialog v-model="previewVisible" title="模板预览" width="780px">
      <div v-if="previewItem" class="preview-content">
        <el-descriptions :column="2" border>
          <el-descriptions-item label="验收类型">{{ previewItem.acceptance_type }}</el-descriptions-item>
          <el-descriptions-item label="标题">{{ previewItem.title }}</el-descriptions-item>
          <el-descriptions-item label="更新时间">{{ previewItem.updated_at }}</el-descriptions-item>
          <el-descriptions-item label="创建时间">{{ previewItem.created_at }}</el-descriptions-item>
        </el-descriptions>

        <h4 class="preview-section-title">标准步骤</h4>
        <ol v-if="previewItem.standard_steps?.length" class="preview-list">
          <li v-for="(s, i) in previewItem.standard_steps" :key="i">{{ s.step_description }}</li>
        </ol>
        <el-empty v-else description="无" :image-size="60" />

        <h4 class="preview-section-title">必查管控要点</h4>
        <ul v-if="previewItem.control_points?.length" class="preview-list">
          <li v-for="(p, i) in previewItem.control_points" :key="i">{{ p }}</li>
        </ul>
        <el-empty v-else description="无" :image-size="60" />

        <h4 class="preview-section-title">常见违规问题</h4>
        <ul v-if="previewItem.common_violations?.length" class="preview-list">
          <li v-for="(v, i) in previewItem.common_violations" :key="i">{{ v }}</li>
        </ul>
        <el-empty v-else description="无" :image-size="60" />

        <h4 class="preview-section-title">规范依据</h4>
        <ul v-if="previewItem.regulations?.length" class="preview-list">
          <li v-for="(r, i) in previewItem.regulations" :key="i">
            《{{ r.regulation_title }}》 条款 {{ r.clause }}
          </li>
        </ul>
        <el-empty v-else description="无" :image-size="60" />
      </div>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox, type FormInstance, type FormRules } from 'element-plus'
import { Plus, Delete } from '@element-plus/icons-vue'
import { acceptanceTemplateApi } from '@/api'

interface StepItem { step_description: string }
interface RegItem { regulation_title: string; clause: string }

const acceptanceTypes = [
  '钢筋', '混凝土', '模板', '脚手架', '深基坑',
  '防水', '电气', '安全帽', '临边洞口', '临时用电',
]

const templates = ref<any[]>([])
const loading = ref(false)
const formVisible = ref(false)
const saving = ref(false)
const editingId = ref<number | null>(null)
const formRef = ref<FormInstance | null>(null)

const form = reactive({
  acceptance_type: '',
  title: '',
  standard_steps: [] as StepItem[],
  control_points: [] as string[],
  common_violations: [] as string[],
  regulations: [] as RegItem[],
})

const formRules: FormRules = {
  acceptance_type: [{ required: true, message: '请选择验收类型', trigger: 'change' }],
  title: [{ required: true, message: '请输入标题', trigger: 'blur' }],
}

// 预览
const previewVisible = ref(false)
const previewItem = ref<any>(null)

async function loadData() {
  loading.value = true
  try {
    const res: any = await acceptanceTemplateApi.list()
    templates.value = Array.isArray(res) ? res : res?.items || []
  } catch (e: any) {
    ElMessage.error('模板列表加载失败: ' + (e.response?.data?.detail || e.message || ''))
  } finally {
    loading.value = false
  }
}

function resetForm() {
  form.acceptance_type = ''
  form.title = ''
  form.standard_steps = []
  form.control_points = []
  form.common_violations = []
  form.regulations = []
}

function openCreate() {
  editingId.value = null
  resetForm()
  formVisible.value = true
}

async function openEdit(row: any) {
  editingId.value = row.id
  resetForm()
  try {
    const detail: any = await acceptanceTemplateApi.get(row.id)
    form.acceptance_type = detail.acceptance_type || ''
    form.title = detail.title || ''
    form.standard_steps = (detail.standard_steps || []).map((s: any) => ({
      step_description: typeof s === 'string' ? s : s.step_description,
    }))
    form.control_points = [...(detail.control_points || [])]
    form.common_violations = [...(detail.common_violations || [])]
    form.regulations = (detail.regulations || []).map((r: any) => ({
      regulation_title: r.regulation_title || '',
      clause: r.clause || '',
    }))
    formVisible.value = true
  } catch (e: any) {
    ElMessage.error('模板加载失败: ' + (e.response?.data?.detail || e.message || ''))
  }
}

function addStep() {
  form.standard_steps.push({ step_description: '' })
}
function removeStep(idx: number) {
  form.standard_steps.splice(idx, 1)
}
function addPoint() {
  form.control_points.push('')
}
function removePoint(idx: number) {
  form.control_points.splice(idx, 1)
}
function addViolation() {
  form.common_violations.push('')
}
function removeViolation(idx: number) {
  form.common_violations.splice(idx, 1)
}
function addRegulation() {
  form.regulations.push({ regulation_title: '', clause: '' })
}
function removeRegulation(idx: number) {
  form.regulations.splice(idx, 1)
}

function buildPayload() {
  return {
    acceptance_type: form.acceptance_type,
    title: form.title.trim(),
    standard_steps: form.standard_steps.filter((s) => s.step_description.trim()),
    control_points: form.control_points.filter((p) => p.trim()),
    common_violations: form.common_violations.filter((v) => v.trim()),
    regulations: form.regulations.filter((r) => r.regulation_title.trim() || r.clause.trim()),
  }
}

async function submitForm() {
  if (!formRef.value) return
  await formRef.value.validate(async (valid) => {
    if (!valid) return
    saving.value = true
    try {
      const payload = buildPayload()
      if (editingId.value) {
        await acceptanceTemplateApi.update(editingId.value, payload)
        ElMessage.success('模板已更新')
      } else {
        await acceptanceTemplateApi.create(payload)
        ElMessage.success('模板已创建')
      }
      formVisible.value = false
      loadData()
    } catch (e: any) {
      ElMessage.error('保存失败: ' + (e.response?.data?.detail || e.message || ''))
    } finally {
      saving.value = false
    }
  })
}

async function confirmDelete(row: any) {
  try {
    await ElMessageBox.confirm(
      `确定删除模板「${row.title}」吗？该操作不可恢复。`,
      '删除确认',
      { type: 'warning', confirmButtonText: '确认删除', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  try {
    await acceptanceTemplateApi.delete(row.id)
    ElMessage.success('模板已删除')
    loadData()
  } catch (e: any) {
    ElMessage.error('删除失败: ' + (e.response?.data?.detail || e.message || ''))
  }
}

function openPreview(row: any) {
  previewItem.value = row
  previewVisible.value = true
}

onMounted(loadData)
</script>

<style scoped>
.page-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 16px;
}
.page-header h2 { margin: 0 0 6px; }
.page-header p { margin: 0; color: #7a8580; }

.dynamic-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}
.regulation-row {
  gap: 12px;
}

.preview-content {
  max-height: 60vh;
  overflow-y: auto;
  padding-right: 8px;
}
.preview-section-title {
  margin: 18px 0 8px;
  font-size: 14px;
  color: #303133;
  border-left: 3px solid #409EFF;
  padding-left: 8px;
}
.preview-list {
  margin: 0;
  padding-left: 22px;
  color: #333;
  font-size: 13px;
  line-height: 1.8;
}
</style>
