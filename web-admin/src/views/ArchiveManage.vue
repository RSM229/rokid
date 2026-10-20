<template>
  <div class="archive-page">
    <div class="page-header">
      <el-breadcrumb separator="/">
        <el-breadcrumb-item>监理工作台</el-breadcrumb-item>
        <el-breadcrumb-item>档案管理</el-breadcrumb-item>
      </el-breadcrumb>
    </div>

    <p class="page-desc">工程档案分类归档、检索与下载。</p>

    <el-card shadow="never" style="margin-bottom: 16px;">
      <el-row :gutter="12" align="middle">
        <el-col :span="6">
          <el-input v-model="searchKey" placeholder="搜索档案名称..." clearable @keyup.enter="loadList">
            <template #prefix><el-icon><Search /></el-icon></template>
          </el-input>
        </el-col>
        <el-col :span="4">
          <el-select v-model="filterType" placeholder="档案类型" clearable style="width:100%">
            <el-option label="全部" value="" />
            <el-option label="监理文书" value="监理文书" />
            <el-option label="验收资料" value="验收资料" />
            <el-option label="巡视记录" value="巡视记录" />
            <el-option label="会议纪要" value="会议纪要" />
            <el-option label="往来函件" value="往来函件" />
            <el-option label="其他" value="其他" />
          </el-select>
        </el-col>
        <el-col :span="4"><el-button type="primary" @click="loadList">搜索</el-button></el-col>
        <el-col :span="10" style="text-align:right">
          <el-button type="success" @click="uploadDialogVisible = true">归档文件</el-button>
        </el-col>
      </el-row>
    </el-card>

    <el-card shadow="never">
      <template #header><span class="section-title">档案列表</span></template>
      <el-table :data="archives" stripe v-loading="loading" empty-text="暂无档案，请归档文件" size="small">
        <el-table-column prop="name" label="档案名称" min-width="200" show-overflow-tooltip />
        <el-table-column prop="archive_type" label="类型" width="100" />
        <el-table-column prop="archive_no" label="档号" width="140" />
        <el-table-column prop="file_size" label="大小" width="100">
          <template #default="{ row }">{{ formatSize(row.file_size) }}</template>
        </el-table-column>
        <el-table-column prop="archived_at" label="归档时间" width="170" />
        <el-table-column label="操作" width="180">
          <template #default="{ row }">
            <el-button size="small" link type="primary" @click="previewArchive(row)">预览</el-button>
            <el-button size="small" link @click="downloadArchive(row)">下载</el-button>
            <el-button size="small" link type="danger" @click="deleteArchive(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- 归档对话框 -->
    <el-dialog v-model="uploadDialogVisible" title="归档文件" width="560px">
      <el-form :model="uploadForm" label-width="100px">
        <el-form-item label="档案名称">
          <el-input v-model="uploadForm.name" placeholder="留空则使用文件名" />
        </el-form-item>
        <el-form-item label="档案类型">
          <el-select v-model="uploadForm.archive_type" placeholder="选择类型" style="width: 100%">
            <el-option label="监理文书" value="监理文书" />
            <el-option label="验收资料" value="验收资料" />
            <el-option label="巡视记录" value="巡视记录" />
            <el-option label="会议纪要" value="会议纪要" />
            <el-option label="往来函件" value="往来函件" />
            <el-option label="其他" value="其他" />
          </el-select>
        </el-form-item>
        <el-form-item label="档号">
          <el-input v-model="uploadForm.archive_no" placeholder="可选" />
        </el-form-item>
        <el-form-item label="文件">
          <el-upload action="#" :auto-upload="false" :on-change="handleFileChange" :limit="1" accept=".pdf,.docx,.doc,.xlsx,.xls,.jpg,.png">
            <el-button type="primary">选择文件</el-button>
            <template #tip>
              <div class="el-upload__tip">支持 pdf / docx / xlsx / jpg / png 等格式</div>
            </template>
          </el-upload>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="uploadDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="uploading" @click="confirmUpload">归档</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, onMounted } from 'vue'
import { ensureWritableProject } from '@/composables/useAllProjectsGuard'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Search } from '@element-plus/icons-vue'
import http from '@/api'

const searchKey = ref('')
const filterType = ref('')
const archives = ref<any[]>([])
const loading = ref(false)
const uploadDialogVisible = ref(false)
const uploading = ref(false)
const uploadForm = reactive({ name: '', archive_type: '', archive_no: '', file: null as File | null })

async function loadList() {
  loading.value = true
  try {
    const res: any = await http.get('/project/archives/list', {
      params: { keyword: searchKey.value, archive_type: filterType.value },
    })
    archives.value = res?.items || []
  } catch {
    archives.value = []
  } finally {
    loading.value = false
  }
}

function formatSize(bytes?: number) {
  if (!bytes) return '-'
  if (bytes < 1024) return bytes + 'B'
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + 'KB'
  return (bytes / 1024 / 1024).toFixed(1) + 'MB'
}

function handleFileChange(file: any) {
  uploadForm.file = file.raw || null
}

async function confirmUpload() {
  if (!(await ensureWritableProject('归档文件'))) return
  if (!uploadForm.file) return ElMessage.warning('请选择文件')
  uploading.value = true
  const form = new FormData()
  form.append('file', uploadForm.file)
  form.append('archive_type', uploadForm.archive_type || '其他')
  if (uploadForm.name) form.append('name', uploadForm.name)
  if (uploadForm.archive_no) form.append('archive_no', uploadForm.archive_no)
  try {
    await http.post('/project/archives/upload', form)
    ElMessage.success('归档成功')
    uploadDialogVisible.value = false
    uploadForm.name = ''
    uploadForm.archive_type = ''
    uploadForm.archive_no = ''
    uploadForm.file = null
    loadList()
  } catch (e: any) {
    ElMessage.error(e.response?.data?.detail || '归档失败')
  } finally {
    uploading.value = false
  }
}

function previewArchive(row: any) {
  if (row.file_url) window.open(row.file_url, '_blank')
  else ElMessage.info('暂无预览文件')
}

function downloadArchive(row: any) {
  if (row.file_url) window.open(row.file_url, '_blank')
  else ElMessage.info('暂无下载文件')
}

async function deleteArchive(row: any) {
  if (!(await ensureWritableProject('删除归档'))) return
  try {
    await ElMessageBox.confirm(`确定删除档案"${row.name}"吗？`, '提示', { type: 'warning' })
    await http.delete(`/project/archives/${row.id}`)
    ElMessage.success('已删除')
    loadList()
  } catch {}
}

onMounted(() => loadList())
</script>

<style scoped>
.page-header { margin-bottom: 16px; }
.page-desc { color: #999; font-size: 13px; margin-bottom: 16px; }
.section-title { font-weight: 600; font-size: 15px; }
</style>
