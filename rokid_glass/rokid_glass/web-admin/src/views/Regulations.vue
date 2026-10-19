<template>
  <div>
    <el-tabs v-model="activeTab" @tab-change="onTabChange">
      <!-- 规范列表 Tab -->
      <el-tab-pane label="规范库" name="list">
        <el-card>
          <template #header>
            <div class="card-header">
              <span>建筑工程规范库</span>
              <div>
                <el-input v-model="keyword" placeholder="输入关键词搜索规范" style="width: 300px; margin-right: 8px" @keyup.enter="search" />
                <el-select v-model="category" placeholder="分类" clearable style="width: 160px; margin-right: 8px">
                  <el-option v-for="c in categories" :key="c" :label="c" :value="c" />
                </el-select>
                <el-button type="primary" @click="search">搜索</el-button>
                <el-button @click="loadList">全部</el-button>
                <el-button type="success" @click="importDialogVisible = true">导入规范</el-button>
              </div>
            </div>
          </template>

          <el-table :data="regulations" stripe v-loading="loading">
            <el-table-column prop="name" label="文档名称" min-width="200" />
            <el-table-column prop="category" label="分类" width="120" />
            <el-table-column prop="filename" label="文件名" width="250" />
            <el-table-column prop="exists" label="状态" width="80">
              <template #default="{ row }">
                <el-tag :type="row.exists ? 'success' : 'info'">{{ row.exists ? '可用' : '缺失' }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="160">
              <template #default="{ row }">
                <el-button size="small" :disabled="!row.exists" @click="download(row)">下载</el-button>
                <el-button size="small" type="warning" @click="openVersionDialog(row)">版本</el-button>
              </template>
            </el-table-column>
          </el-table>
        </el-card>

        <!-- 搜索结果片段 -->
        <el-card v-if="snippets.length" style="margin-top: 16px" header="内容匹配">
          <div v-for="(s, i) in snippets" :key="i" class="snippet-item">
            <strong>{{ s.name }}</strong>
            <p class="snippet-text">{{ s.snippet }}</p>
          </div>
        </el-card>

        <!-- 导入规范对话框 -->
        <el-dialog v-model="importDialogVisible" title="导入规范文档" width="500px">
          <el-form label-width="80px">
            <el-form-item label="文档名称">
              <el-input v-model="importName" placeholder="留空则使用文件名" />
            </el-form-item>
            <el-form-item label="分类">
              <el-select v-model="importCategory" placeholder="选择分类" style="width: 100%">
                <el-option label="省统表" value="省统表" />
                <el-option label="安全监理用表" value="安全监理用表" />
              </el-select>
            </el-form-item>
            <el-form-item label="文件">
              <el-upload
                ref="uploadRef"
                action="#"
                :auto-upload="false"
                :on-change="handleImportFileChange"
                :limit="1"
                accept=".docx,.xlsx,.pdf,.doc,.xls"
              >
                <el-button type="primary">选择文件</el-button>
                <template #tip>
                  <div class="el-upload__tip">支持 docx/xlsx/pdf/doc/xls 格式</div>
                </template>
              </el-upload>
            </el-form-item>
          </el-form>
          <template #footer>
            <el-button @click="importDialogVisible = false">取消</el-button>
            <el-button type="primary" :loading="importLoading" @click="confirmImport">导入</el-button>
          </template>
        </el-dialog>
      </el-tab-pane>

      <!-- 版本管理 Tab -->
      <el-tab-pane label="版本管理" name="versions">
        <el-card>
          <template #header>
            <div class="card-header">
              <span>规范版本管理</span>
              <div>
                <el-select v-model="versionFilterCategory" placeholder="分类筛选" clearable style="width: 160px; margin-right: 8px">
                  <el-option v-for="c in categories" :key="c" :label="c" :value="c" />
                </el-select>
                <el-checkbox v-model="versionActiveOnly" style="margin-right: 8px" @change="loadVersions">仅活跃版本</el-checkbox>
                <el-button type="primary" @click="loadVersions">刷新</el-button>
                <el-button type="success" @click="syncVersions" :loading="syncLoading">同步版本</el-button>
              </div>
            </div>
          </template>

          <el-table :data="versions" stripe v-loading="versionLoading">
            <el-table-column prop="doc_name" label="文档名称" min-width="180" />
            <el-table-column prop="filename" label="文件名" width="200" />
            <el-table-column prop="category" label="分类" width="120" />
            <el-table-column prop="version" label="版本号" width="80" />
            <el-table-column prop="effective_date" label="生效日期" width="120" />
            <el-table-column prop="expire_date" label="失效日期" width="120" />
            <el-table-column label="状态" width="80">
              <template #default="{ row }">
                <el-tag :type="row.is_active ? 'success' : 'info'">{{ row.is_active ? '活跃' : '失效' }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="remark" label="备注" min-width="120" />
            <el-table-column label="操作" width="100">
              <template #default="{ row }">
                <el-button
                  v-if="!row.is_active"
                  size="small"
                  type="warning"
                  @click="doRollback(row)"
                >回退</el-button>
              </template>
            </el-table-column>
          </el-table>
        </el-card>
      </el-tab-pane>
    </el-tabs>

    <!-- 版本注册对话框 -->
    <el-dialog v-model="versionDialogVisible" title="注册规范版本" width="500px">
      <el-form label-width="80px">
        <el-form-item label="文档">
          <el-input :model-value="versionForm.doc_name" disabled />
        </el-form-item>
        <el-form-item label="版本号">
          <el-input v-model="versionForm.version" placeholder="如 1.0、2.0" />
        </el-form-item>
        <el-form-item label="生效日期">
          <el-date-picker v-model="versionForm.effective_date" type="date" value-format="YYYY-MM-DD" placeholder="选择生效日期" style="width: 100%" />
        </el-form-item>
        <el-form-item label="失效日期">
          <el-date-picker v-model="versionForm.expire_date" type="date" value-format="YYYY-MM-DD" placeholder="选择失效日期" style="width: 100%" />
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="versionForm.remark" type="textarea" placeholder="版本变更说明" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="versionDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="versionSaving" @click="saveVersion">注册版本</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { knowledgeApi } from '@/api'

const activeTab = ref('list')

// 规范列表
const keyword = ref('')
const category = ref('')
const categories = ref<string[]>([])
const regulations = ref<any[]>([])
const snippets = ref<any[]>([])
const loading = ref(false)

const importDialogVisible = ref(false)
const importName = ref('')
const importCategory = ref('省统表')
const importFile = ref<File | null>(null)
const importLoading = ref(false)

// 版本管理
const versions = ref<any[]>([])
const versionLoading = ref(false)
const versionFilterCategory = ref('')
const versionActiveOnly = ref(false)
const syncLoading = ref(false)
const versionDialogVisible = ref(false)
const versionSaving = ref(false)
const versionForm = ref({
  filename: '',
  doc_name: '',
  category: '',
  version: '1.0',
  effective_date: '',
  expire_date: '',
  remark: '',
})

async function loadCategories() {
  try {
    const res: any = await knowledgeApi.categories()
    categories.value = res.categories || []
  } catch {}
}

async function loadList() {
  loading.value = true
  snippets.value = []
  try {
    const res: any = await knowledgeApi.listRegulations(category.value || undefined)
    regulations.value = res.results || []
  } catch {
    ElMessage.error('加载失败')
  } finally {
    loading.value = false
  }
}

async function search() {
  if (!keyword.value) {
    loadList()
    return
  }
  loading.value = true
  try {
    const res: any = await knowledgeApi.searchRegulations(keyword.value, category.value || undefined)
    regulations.value = res.results || []
    snippets.value = regulations.value.filter((r: any) => r.snippet)
  } catch {
    ElMessage.error('搜索失败')
  } finally {
    loading.value = false
  }
}

async function download(row: any) {
  try {
    const blob = await knowledgeApi.downloadRegulation(row.filename, row.category)
    const url = window.URL.createObjectURL(blob as any)
    const a = document.createElement('a')
    a.href = url
    a.download = row.filename
    a.click()
    window.URL.revokeObjectURL(url)
  } catch {
    ElMessage.error('下载失败')
  }
}

function handleImportFileChange(file: any) {
  importFile.value = file.raw || null
}

async function confirmImport() {
  if (!importFile.value) {
    ElMessage.warning('请选择要导入的文件')
    return
  }
  importLoading.value = true
  try {
    const res: any = await knowledgeApi.importRegulation(
      importFile.value,
      importCategory.value,
      importName.value || undefined,
    )
    ElMessage.success(res.message || '导入成功')
    importDialogVisible.value = false
    importName.value = ''
    importFile.value = null
    loadList()
  } catch (e: any) {
    ElMessage.error(e.response?.data?.detail || '导入失败')
  } finally {
    importLoading.value = false
  }
}

// 版本管理方法
async function loadVersions() {
  versionLoading.value = true
  try {
    const res: any = await knowledgeApi.listVersions({
      category: versionFilterCategory.value || undefined,
      active_only: versionActiveOnly.value,
    })
    versions.value = res.versions || []
  } catch {
    ElMessage.error('加载版本列表失败')
  } finally {
    versionLoading.value = false
  }
}

async function syncVersions() {
  syncLoading.value = true
  try {
    const res: any = await knowledgeApi.syncVersions()
    ElMessage.success(res.message || '同步完成')
    loadVersions()
  } catch {
    ElMessage.error('同步失败')
  } finally {
    syncLoading.value = false
  }
}

function openVersionDialog(row: any) {
  versionForm.value = {
    filename: row.filename,
    doc_name: row.name,
    category: row.category,
    version: '1.0',
    effective_date: '',
    expire_date: '',
    remark: '',
  }
  versionDialogVisible.value = true
}

async function saveVersion() {
  versionSaving.value = true
  try {
    const res: any = await knowledgeApi.createVersion({
      filename: versionForm.value.filename,
      doc_name: versionForm.value.doc_name,
      category: versionForm.value.category,
      version: versionForm.value.version,
      effective_date: versionForm.value.effective_date || null,
      expire_date: versionForm.value.expire_date || null,
      remark: versionForm.value.remark || null,
    })
    ElMessage.success(res.message || '版本注册成功')
    versionDialogVisible.value = false
    loadVersions()
  } catch (e: any) {
    ElMessage.error(e.response?.data?.detail || '注册失败')
  } finally {
    versionSaving.value = false
  }
}

async function doRollback(row: any) {
  try {
    await ElMessageBox.confirm(
      `确认回退到版本 ${row.version}？`,
      '版本回退',
      { type: 'warning' },
    )
    const res: any = await knowledgeApi.rollbackVersion(row.id)
    ElMessage.success(res.message || '回退成功')
    loadVersions()
  } catch (e: any) {
    if (e !== 'cancel') {
      ElMessage.error(e.response?.data?.detail || '回退失败')
    }
  }
}

function onTabChange(tab: string) {
  if (tab === 'versions' && versions.value.length === 0) {
    loadVersions()
  }
}

onMounted(async () => {
  await loadCategories()
  loadList()
})
</script>

<style scoped>
.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.snippet-item {
  margin-bottom: 12px;
  padding: 8px;
  background: #f5f7fa;
  border-radius: 4px;
}
.snippet-text {
  color: #606266;
  font-size: 13px;
  margin: 4px 0 0 0;
}
</style>
