<template>
  <div class="witness-page">
    <div class="page-header">
      <el-breadcrumb separator="/">
        <el-breadcrumb-item>监理工作台</el-breadcrumb-item>
        <el-breadcrumb-item>见证取样</el-breadcrumb-item>
      </el-breadcrumb>
    </div>

    <!-- 记录列表 -->
    <el-card shadow="never">
      <template #header>
        <div class="card-header">
          <span class="section-title">见证记录</span>
          <el-button type="primary" size="small" @click="openCreateDialog">
            <el-icon><Plus /></el-icon>
            <span>新建记录</span>
          </el-button>
        </div>
      </template>
      <el-table :data="records" stripe empty-text="暂无记录" size="small">
        <el-table-column prop="id" label="#" width="50" />
        <el-table-column label="材料名称" width="140">
          <template #default="{ row }">{{ row.witness_material_name || '-' }}</template>
        </el-table-column>
        <el-table-column label="规格型号" width="120">
          <template #default="{ row }">{{ row.witness_material_spec || '-' }}</template>
        </el-table-column>
        <el-table-column prop="status" label="状态" width="90">
          <template #default="{ row }"><el-tag size="small">{{ row.status }}</el-tag></template>
        </el-table-column>
        <el-table-column prop="created_at" label="创建时间" width="170" />
        <el-table-column label="操作" width="180">
          <template #default="{ row }">
            <el-button size="small" link @click="viewDetail(row)">查看</el-button>
            <el-button size="small" link type="primary" @click="openEdit(row)">编辑</el-button>
            <el-button size="small" link type="primary" @click="generateReport(row)">生成报告</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- 新建弹窗 -->
    <el-dialog v-model="createDialogVisible" title="新建见证记录" width="600px">
      <el-form :model="form" label-width="100px">
        <el-row :gutter="16">
          <el-col :span="12"><el-form-item label="材料名称"><el-input v-model="form.material_name" /></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="规格型号"><el-input v-model="form.specification" /></el-form-item></el-col>
        </el-row>
        <el-row :gutter="16">
          <el-col :span="12"><el-form-item label="生产厂家"><el-input v-model="form.manufacturer" /></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="批次号"><el-input v-model="form.batch_no" /></el-form-item></el-col>
        </el-row>
        <el-form-item label="施工区域"><el-input v-model="form.section" /></el-form-item>
        <el-form-item label="材料照片">
          <el-upload action="#" :auto-upload="false" multiple accept=".jpg,.jpeg,.png"
            list-type="picture-card" :file-list="fileList"
            :on-change="handleFileChange" :on-remove="handleFileChange">
            <el-icon><Plus /></el-icon>
          </el-upload>
        </el-form-item>
        <el-form-item label="备注"><el-input v-model="form.remark" type="textarea" :rows="2" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="handleSave">保存</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="detailDialogVisible" title="见证记录详情" width="700px">
      <el-descriptions v-if="currentDetail" :column="2" border>
        <el-descriptions-item label="记录编号">#{{ currentDetail.id }}</el-descriptions-item>
        <el-descriptions-item label="状态">{{ currentDetail.status }}</el-descriptions-item>
        <el-descriptions-item label="材料名称">{{ currentDetail.witness_material_name || '-' }}</el-descriptions-item>
        <el-descriptions-item label="规格型号">{{ currentDetail.witness_material_spec || '-' }}</el-descriptions-item>
        <el-descriptions-item label="施工区域">{{ currentDetail.section || '-' }}</el-descriptions-item>
        <el-descriptions-item label="具体位置">{{ currentDetail.location || '-' }}</el-descriptions-item>
        <el-descriptions-item label="备注" :span="2">{{ currentDetail.remark || '-' }}</el-descriptions-item>
      </el-descriptions>
      <div v-if="currentDetail?.witness_photos?.length" class="detail-photos">
        <el-image v-for="url in currentDetail.witness_photos" :key="url" :src="url"
          :preview-src-list="currentDetail.witness_photos" fit="cover" />
      </div>
      <template #footer>
        <el-button v-if="currentDetail?.witness_report_path" type="primary" @click="openReport(currentDetail.witness_report_path)">查看报告</el-button>
        <el-button @click="detailDialogVisible = false">关闭</el-button>
      </template>
    </el-dialog>

    <!-- 编辑弹窗 -->
    <el-dialog v-model="editDialogVisible" title="编辑见证记录" width="600px">
      <el-form :model="editForm" label-width="100px">
        <el-row :gutter="16">
          <el-col :span="12"><el-form-item label="材料名称"><el-input v-model="editForm.witness_material_name" /></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="规格型号"><el-input v-model="editForm.witness_material_spec" /></el-form-item></el-col>
        </el-row>
        <el-form-item label="施工区域"><el-input v-model="editForm.section" /></el-form-item>
        <el-form-item label="状态">
          <el-select v-model="editForm.status" style="width: 100%;">
            <el-option label="草稿" value="草稿" />
            <el-option label="已提交" value="已提交" />
          </el-select>
        </el-form-item>
        <el-form-item label="备注"><el-input v-model="editForm.remark" type="textarea" :rows="2" /></el-form-item>
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
const fileList = ref<any[]>([])
const saving = ref(false)
const detailDialogVisible = ref(false)
const currentDetail = ref<any>(null)

const createDialogVisible = ref(false)
const form = reactive({
  material_name: '', specification: '', manufacturer: '',
  batch_no: '', section: '', remark: '',
})

const editDialogVisible = ref(false)
const editSaving = ref(false)
const editingId = ref<number | null>(null)
const editForm = reactive({
  witness_material_name: '', witness_material_spec: '',
  section: '', status: '', remark: '',
})

async function openCreateDialog() {
  if (!(await ensureWritableProject('新建见证取样'))) return
  resetForm()
  createDialogVisible.value = true
}

function resetForm() {
  Object.assign(form, {
    material_name: '', specification: '', manufacturer: '',
    batch_no: '', section: '', remark: '',
  })
  fileList.value = []
}

function handleFileChange(_file: any, files: any[]) {
  fileList.value = files
}

async function handleSave() {
  if (!form.material_name) return ElMessage.warning('请填写材料名称')
  saving.value = true
  try {
    const res: any = await workRecordApi.createWitness({
      witness_material_name: form.material_name,
      witness_material_spec: form.specification,
      manufacturer: form.manufacturer,
      batch_no: form.batch_no,
      section: form.section,
      remark: form.remark,
    })
    const files = fileList.value.map(file => file.raw).filter(Boolean)
    if (res.record_id && files.length) {
      await workRecordApi.uploadWitnessPhotos(res.record_id, files)
    }
    ElMessage.success('见证记录已保存')
    createDialogVisible.value = false
    resetForm()
    loadRecords()
  } catch (e: any) { ElMessage.error(e.response?.data?.detail || '失败') }
  finally { saving.value = false }
}

async function viewDetail(row: any) {
  try {
    currentDetail.value = await workRecordApi.detail(row.id)
    detailDialogVisible.value = true
  } catch (e: any) { ElMessage.error(e.response?.data?.detail || '获取详情失败') }
}

async function generateReport(row: any) {
  try {
    const res: any = await workRecordApi.generateWitnessReport(row.id)
    ElMessage.success(res.detail || '见证报告已生成')
    loadRecords()
    if (res.pdf_url) openReport(res.pdf_url)
  } catch (e: any) { ElMessage.error(e.response?.data?.detail || '报告生成失败') }
}

function openReport(url: string) {
  window.open(url, '_blank', 'noopener,noreferrer')
}

async function loadRecords() {
  try { records.value = (await workRecordApi.list({ record_type: '见证' }) as any).records || [] }
  catch {}
}

async function openEdit(row: any) {
  try {
    const detail: any = await workRecordApi.detail(row.id)
    editingId.value = row.id
    Object.assign(editForm, {
      witness_material_name: detail.witness_material_name || '',
      witness_material_spec: detail.witness_material_spec || '',
      section: detail.section || '',
      status: detail.status || '',
      remark: detail.remark || '',
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
.detail-photos { margin-top: 20px; }
.detail-photos .el-image { width: 140px; height: 100px; margin: 12px 12px 0 0; border-radius: 6px; }
</style>
