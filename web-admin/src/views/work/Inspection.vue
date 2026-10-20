<template>
  <div class="inspection-page">
    <div class="page-header">
      <el-breadcrumb separator="/">
        <el-breadcrumb-item>监理工作台</el-breadcrumb-item>
        <el-breadcrumb-item>巡视记录</el-breadcrumb-item>
      </el-breadcrumb>
    </div>

    <!-- 记录列表 -->
    <el-card shadow="never">
      <template #header>
        <div class="card-header">
          <span class="section-title">巡视记录</span>
          <el-button type="primary" size="small" @click="openCreateDialog">
            <el-icon><Plus /></el-icon>
            <span>新建记录</span>
          </el-button>
        </div>
      </template>
      <el-table :data="records" stripe empty-text="暂无记录" size="small">
        <el-table-column prop="id" label="#" width="50" />
        <el-table-column prop="section" label="施工区域" width="120" />
        <el-table-column prop="location" label="位置" min-width="120" />
        <el-table-column prop="status" label="状态" width="90">
          <template #default="{ row }"><el-tag size="small">{{ row.status }}</el-tag></template>
        </el-table-column>
        <el-table-column prop="created_at" label="创建时间" width="170" />
        <el-table-column label="操作" width="180">
          <template #default="{ row }">
            <el-button size="small" link @click="viewDetail(row)">查看</el-button>
            <el-button size="small" link type="primary" @click="openEdit(row)">编辑</el-button>
            <el-button v-if="row.status === '草稿'" size="small" link type="success" @click="submitRecord(row)">提交</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- 新建弹窗 -->
    <el-dialog v-model="createDialogVisible" title="新建巡视记录" width="700px">
      <el-form :model="form" label-width="100px">
        <el-row :gutter="16">
          <el-col :span="12">
            <el-form-item label="施工区域"><el-input v-model="form.section" placeholder="如：A区3层" /></el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="具体位置"><el-input v-model="form.location" placeholder="详细位置描述" /></el-form-item>
          </el-col>
        </el-row>
        <el-row :gutter="16">
          <el-col :span="12">
            <el-form-item label="施工内容"><el-input v-model="form.work_content" /></el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="天气"><el-input v-model="form.weather" placeholder="自动获取或手动填写" /></el-form-item>
          </el-col>
        </el-row>
        <el-form-item label="巡视备注"><el-input v-model="form.inspection_remark" type="textarea" :rows="2" /></el-form-item>

        <!-- 水印参数 -->
        <el-divider content-position="left">水印信息（照片左下角显示）</el-divider>
        <el-row :gutter="16">
          <el-col :span="8"><el-form-item label="工程名称"><el-input v-model="wmForm.project_name" /></el-form-item></el-col>
          <el-col :span="8"><el-form-item label="单位工程"><el-input v-model="wmForm.unit_project" placeholder="工程简称" /></el-form-item></el-col>
          <el-col :span="8"><el-form-item label="施工区域"><el-input v-model="wmForm.wm_section" /></el-form-item></el-col>
        </el-row>

        <el-form-item label="巡检拍照">
          <el-upload
            action="#"
            :auto-upload="false"
            :on-change="handleFileChange"
            :on-remove="handleFileChange"
            :file-list="fileList"
            multiple
            accept=".jpg,.jpeg,.png"
            list-type="picture-card"
          >
            <el-icon><Plus /></el-icon>
          </el-upload>
          <div class="upload-tip">每个环节拍摄的照片将自动添加水印（含工程名称、单位工程、施工区域、施工内容、时间、天气）</div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="handleSave">保存</el-button>
        <el-button type="success" :loading="saving" @click="handleSaveAndSubmit">保存并提交</el-button>
      </template>
    </el-dialog>

    <!-- 详情弹窗 -->
    <el-dialog v-model="detailDialogVisible" title="巡视记录详情" width="720px">
      <el-descriptions v-if="currentDetail" :column="2" border>
        <el-descriptions-item label="记录编号">#{{ currentDetail.id }}</el-descriptions-item>
        <el-descriptions-item label="状态">{{ currentDetail.status }}</el-descriptions-item>
        <el-descriptions-item label="施工区域">{{ currentDetail.section || '-' }}</el-descriptions-item>
        <el-descriptions-item label="具体位置">{{ currentDetail.location || '-' }}</el-descriptions-item>
        <el-descriptions-item label="天气">{{ currentDetail.weather || '-' }}</el-descriptions-item>
        <el-descriptions-item label="创建时间">{{ currentDetail.created_at || '-' }}</el-descriptions-item>
        <el-descriptions-item label="施工内容" :span="2">{{ currentDetail.work_content || '-' }}</el-descriptions-item>
        <el-descriptions-item label="巡视备注" :span="2">{{ currentDetail.inspection_remark || '-' }}</el-descriptions-item>
      </el-descriptions>
      <div v-if="currentDetail?.inspection_photos?.length" class="detail-photos">
        <div class="section-title">巡视影像</div>
        <el-image
          v-for="url in currentDetail.inspection_photos"
          :key="url"
          :src="url"
          :preview-src-list="currentDetail.inspection_photos"
          fit="cover"
        />
      </div>
      <template #footer>
        <el-button @click="detailDialogVisible = false">关闭</el-button>
      </template>
    </el-dialog>

    <!-- 编辑弹窗 -->
    <el-dialog v-model="editDialogVisible" title="编辑巡视记录" width="600px">
      <el-form :model="editForm" label-width="100px">
        <el-row :gutter="16">
          <el-col :span="12">
            <el-form-item label="施工区域"><el-input v-model="editForm.section" /></el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="具体位置"><el-input v-model="editForm.location" /></el-form-item>
          </el-col>
        </el-row>
        <el-row :gutter="16">
          <el-col :span="12">
            <el-form-item label="施工内容"><el-input v-model="editForm.work_content" /></el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="天气"><el-input v-model="editForm.weather" /></el-form-item>
          </el-col>
        </el-row>
        <el-form-item label="巡视备注"><el-input v-model="editForm.inspection_remark" type="textarea" :rows="3" /></el-form-item>
        <el-form-item label="状态">
          <el-select v-model="editForm.status">
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
import { workRecordApi } from '@/api'
import { ensureWritableProject } from '@/composables/useAllProjectsGuard'

const saving = ref(false)
const records = ref<any[]>([])
const fileList = ref<any[]>([])

const createDialogVisible = ref(false)
const editDialogVisible = ref(false)
const editSaving = ref(false)
const editingId = ref<number | null>(null)
const editForm = reactive({ section: '', location: '', work_content: '', weather: '', inspection_remark: '', status: '' })
const detailDialogVisible = ref(false)
const currentDetail = ref<any>(null)

const form = reactive({ section: '', location: '', work_content: '', weather: '', inspection_remark: '' })
const wmForm = reactive({ project_name: '', unit_project: '', wm_section: '' })

function handleFileChange(_file: any, files: any[]) {
  fileList.value = files
}

async function openCreateDialog() {
  if (!(await ensureWritableProject('新建巡视记录'))) return
  resetForm()
  createDialogVisible.value = true
}

async function saveRecord(submit: boolean) {
  if (!form.section) return ElMessage.warning('请填写施工区域')
  saving.value = true
  try {
    const res = await workRecordApi.createInspection(form) as any
    // 上传照片
    if (fileList.value.length && res.record_id) {
      const files = fileList.value.map(f => f.raw).filter(Boolean)
      if (files.length) {
        await workRecordApi.uploadInspectionPhotos(res.record_id, {
          project_name: wmForm.project_name,
          unit_project: wmForm.unit_project,
          section: form.section || wmForm.wm_section,
          work_content: form.work_content,
          weather: form.weather,
        }, files)
      }
    }
    if (submit && res.record_id) {
      await workRecordApi.submitInspection(res.record_id)
    }
    ElMessage.success(submit ? '巡视记录已保存并提交' : '巡视记录已保存')
    createDialogVisible.value = false
    resetForm()
    loadRecords()
  } catch (e: any) { ElMessage.error(e.response?.data?.detail || '保存失败') }
  finally { saving.value = false }
}

async function handleSave() { await saveRecord(false) }

async function handleSaveAndSubmit() { await saveRecord(true) }

async function submitRecord(row: any) {
  try { await workRecordApi.submitInspection(row.id); ElMessage.success('已提交'); loadRecords() }
  catch (e: any) { ElMessage.error('提交失败') }
}

async function viewDetail(row: any) {
  try {
    currentDetail.value = await workRecordApi.detail(row.id)
    detailDialogVisible.value = true
  } catch (e: any) {
    ElMessage.error(e.response?.data?.detail || '获取详情失败')
  }
}

async function openEdit(row: any) {
  try {
    const detail: any = await workRecordApi.detail(row.id)
    editingId.value = row.id
    Object.assign(editForm, {
      section: detail.section || '',
      location: detail.location || '',
      work_content: detail.work_content || '',
      weather: detail.weather || '',
      inspection_remark: detail.inspection_remark || '',
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

function resetForm() {
  Object.assign(form, { section: '', location: '', work_content: '', weather: '', inspection_remark: '' })
  fileList.value = []
}

async function loadRecords() {
  try { records.value = (await workRecordApi.list({ record_type: '巡视' }) as any).records || [] }
  catch {}
}

onMounted(() => loadRecords())
</script>

<style scoped>
.page-header { margin-bottom: 16px; }
.section-title { font-weight: 600; font-size: 15px; }
.upload-tip { color: #999; font-size: 12px; margin-top: 4px; }
.card-header { display: flex; justify-content: space-between; align-items: center; }
.detail-photos { margin-top: 20px; }
.detail-photos .el-image { width: 140px; height: 100px; margin: 12px 12px 0 0; border-radius: 6px; }
</style>
