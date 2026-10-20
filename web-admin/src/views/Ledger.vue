<template>
  <div>
    <el-tabs v-model="activeTab" @tab-change="onTabChange">
      <!-- Tab 1: 台账查询（原功能 + 批量操作） -->
      <el-tab-pane label="台账查询" name="query">
        <el-card>
          <template #header>
            <div class="card-header">
              <span>统一台账管理</span>
              <div>
                <el-select v-model="ledgerType" placeholder="台账类型" style="width: 180px; margin-right: 8px" @change="loadData">
                  <el-option v-for="(name, key) in ledgerTypes" :key="key" :label="name" :value="key" />
                </el-select>
                <el-date-picker v-model="dateRange" type="daterange" range-separator="至" start-placeholder="开始" end-placeholder="结束" style="margin-right: 8px" @change="loadData" />
                <el-button type="success" @click="exportLedger('excel')">导出Excel</el-button>
                <el-button type="warning" @click="exportLedger('pdf')">导出PDF</el-button>
                <el-button type="primary" @click="batchPanelVisible = !batchPanelVisible">
                  {{ batchPanelVisible ? '收起批量操作' : '批量操作' }}
                </el-button>
              </div>
            </div>
          </template>

          <!-- 批量操作面板 -->
          <el-collapse-transition>
            <div v-show="batchPanelVisible" class="batch-panel">
              <div class="batch-panel-title">批量操作（多类型台账导出 / 归档）</div>
              <el-form label-width="100px" class="batch-form">
                <el-form-item label="台账类型">
                  <el-checkbox-group v-model="batchSelectedTypes">
                    <el-checkbox
                      v-for="t in batchLedgerTypes"
                      :key="t.value"
                      :label="t.label"
                      :value="t.value"
                    />
                  </el-checkbox-group>
                  <div class="batch-check-actions">
                    <el-button link type="primary" size="small" @click="selectAllBatchTypes">全选</el-button>
                    <el-button link size="small" @click="batchSelectedTypes = []">清空</el-button>
                  </div>
                </el-form-item>
                <el-form-item label="时间范围">
                  <el-date-picker
                    v-model="batchDateRange"
                    type="daterange"
                    range-separator="至"
                    start-placeholder="开始日期"
                    end-placeholder="结束日期"
                    value-format="YYYY-MM-DD"
                    style="width: 360px"
                  />
                </el-form-item>
                <el-form-item label="操作">
                  <el-button type="success" :loading="batchExporting === 'excel'" @click="batchExport('excel')">批量导出 Excel</el-button>
                  <el-button type="warning" :loading="batchExporting === 'pdf'" @click="batchExport('pdf')">批量导出 PDF</el-button>
                  <el-button type="primary" :loading="batchArchiving" @click="openBatchArchiveDialog">批量归档</el-button>
                </el-form-item>
              </el-form>
            </div>
          </el-collapse-transition>

          <el-table :data="records" stripe v-loading="loading">
            <el-table-column prop="id" label="ID" width="60" />
            <template v-if="ledgerType === 'image'">
              <el-table-column prop="source" label="来源" width="80" />
              <el-table-column prop="taken_at" label="拍摄时间" width="180" />
              <el-table-column prop="section" label="部位" width="120" />
              <el-table-column prop="location" label="位置" />
              <el-table-column prop="weather" label="天气" width="100" />
              <el-table-column prop="remark" label="备注" />
            </template>
            <template v-else-if="ledgerType === 'hazard'">
              <el-table-column prop="top1_risk" label="主要风险" min-width="150" />
              <el-table-column prop="risk_category" label="类别" width="100" />
              <el-table-column prop="section" label="部位" width="120" />
              <el-table-column prop="status" label="状态" width="100">
                <template #default="{ row }">
                  <el-tag :type="row.status === '已闭环' ? 'success' : 'warning'">{{ row.status }}</el-tag>
                </template>
              </el-table-column>
              <el-table-column prop="created_at" label="时间" width="180" />
            </template>
            <template v-else>
              <el-table-column prop="doc_number" label="编号" width="180" />
              <el-table-column prop="title" label="标题" min-width="200" />
              <el-table-column prop="section" label="部位" width="120" />
              <el-table-column prop="status" label="状态" width="100" />
              <el-table-column prop="created_at" label="创建时间" width="180" />
            </template>
          </el-table>
        </el-card>
      </el-tab-pane>

      <!-- Tab 2: 归档管理 -->
      <el-tab-pane label="归档管理" name="archives">
        <el-card>
          <template #header>
            <div class="card-header">
              <span>台账归档管理</span>
              <el-button :icon="Refresh" @click="loadArchives">刷新</el-button>
            </div>
          </template>
          <el-table :data="archives" stripe v-loading="archivesLoading" empty-text="暂无归档">
            <el-table-column prop="id" label="ID" width="60" />
            <el-table-column prop="archive_name" label="归档名称" min-width="180" show-overflow-tooltip />
            <el-table-column label="台账类型" min-width="200">
              <template #default="{ row }">
                <el-tag
                  v-for="t in (row.ledger_types || [])"
                  :key="t"
                  size="small"
                  style="margin-right: 4px; margin-bottom: 2px"
                >{{ batchLedgerTypeLabel(t) }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="时间范围" width="220">
              <template #default="{ row }">
                <span v-if="row.date_from || row.date_to">
                  {{ row.date_from || '?' }} ~ {{ row.date_to || '?' }}
                </span>
                <span v-else style="color: #999">全部</span>
              </template>
            </el-table-column>
            <el-table-column prop="created_at" label="创建时间" width="180" />
            <el-table-column label="操作" width="160" fixed="right">
              <template #default="{ row }">
                <el-button size="small" link type="primary" @click="downloadArchive(row)">下载</el-button>
                <el-button
                  v-if="canDeleteArchive"
                  size="small"
                  link
                  type="danger"
                  @click="confirmDeleteArchive(row)"
                >删除</el-button>
              </template>
            </el-table-column>
          </el-table>
        </el-card>
      </el-tab-pane>
    </el-tabs>

    <!-- 批量归档对话框 -->
    <el-dialog v-model="batchArchiveDialogVisible" title="批量归档" width="480px">
      <el-form label-width="100px">
        <el-form-item label="已选类型">
          <div class="archive-selected-types">
            <el-tag
              v-for="t in batchSelectedTypes"
              :key="t"
              size="small"
              style="margin-right: 4px; margin-bottom: 2px"
            >{{ batchLedgerTypeLabel(t) }}</el-tag>
            <span v-if="batchSelectedTypes.length === 0" style="color: #999">未选择</span>
          </div>
        </el-form-item>
        <el-form-item label="时间范围">
          <span v-if="batchDateRange && batchDateRange.length === 2">
            {{ batchDateRange[0] }} ~ {{ batchDateRange[1] }}
          </span>
          <span v-else style="color: #999">未限制</span>
        </el-form-item>
        <el-form-item label="归档名称" required>
          <el-input v-model="batchArchiveName" placeholder="请输入归档名称，如：2026年5月监理台账归档" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="batchArchiveDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="batchArchiving" @click="confirmBatchArchive">确认归档</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { ensureWritableProject } from '@/composables/useAllProjectsGuard'
import { ElMessage, ElMessageBox, ElNotification } from 'element-plus'
import { Refresh } from '@element-plus/icons-vue'
import { ledgerApi } from '@/api'
import { useAuthStore } from '@/stores/auth'
import { isChiefRole } from '@/constants/role'

const auth = useAuthStore()
const activeTab = ref('query')

// 8 类台账（用于批量操作）
const batchLedgerTypes = [
  { label: '影像台账', value: 'image' },
  { label: '旁站记录', value: 'station' },
  { label: '监理日志', value: 'daily_log' },
  { label: '整改通知单', value: 'rectification' },
  { label: 'AI安全巡检', value: 'ai_inspection' },
  { label: '周报', value: 'weekly_report' },
  { label: '月报', value: 'monthly_report' },
  { label: '见证取样', value: 'witness' },
]

function batchLedgerTypeLabel(value: string): string {
  return batchLedgerTypes.find(t => t.value === value)?.label || value
}

// 仅总监/总代可删除归档（S2：后端 role 为英文 key）
// 公司级账号（org_role 非空）视为放行 —— token.role 会是 super_admin/admin，
// 若只比对项目岗位 key 会把最高权限的账号挡在门外。
const canDeleteArchive = computed(() => {
  const info = auth.userInfo
  if (info?.org_role) return true
  return isChiefRole(info?.role || '')
})

// ===== 台账查询（原功能） =====
const ledgerTypes = ref<Record<string, string>>({})
const ledgerType = ref('hazard')
const records = ref<any[]>([])
const loading = ref(false)
const dateRange = ref<[Date, Date] | null>(null)

async function loadTypes() {
  try {
    const res = await ledgerApi.types()
    ledgerTypes.value = (res as any).types
  } catch {}
}

async function loadData() {
  loading.value = true
  try {
    const params: any = { ledger_type: ledgerType.value }
    if (dateRange.value) {
      params.start_date = dateRange.value[0].toISOString().split('T')[0]
      params.end_date = dateRange.value[1].toISOString().split('T')[0]
    }
    const res = await ledgerApi.list(params)
    records.value = (res as any).records || []
  } catch (e: any) {
    ElMessage.error('加载失败')
  } finally {
    loading.value = false
  }
}

async function exportLedger(format: string) {
  try {
    const params: any = {}
    if (dateRange.value) {
      params.start_date = dateRange.value[0].toISOString().split('T')[0]
      params.end_date = dateRange.value[1].toISOString().split('T')[0]
    }
    const res = await ledgerApi.export(ledgerType.value, format, params)
    ElMessage.success(`导出成功，共${(res as any).total}条`)
    // 下载
    const blob = await ledgerApi.download((res as any).filename)
    const url = window.URL.createObjectURL(blob as any)
    const a = document.createElement('a')
    a.href = url
    a.download = (res as any).filename
    a.click()
    window.URL.revokeObjectURL(url)
  } catch (e: any) {
    ElMessage.error('导出失败: ' + (e.message || ''))
  }
}

// ===== 批量操作 =====
const batchPanelVisible = ref(false)
const batchSelectedTypes = ref<string[]>([])
const batchDateRange = ref<[string, string] | null>(null)
const batchExporting = ref<'' | 'excel' | 'pdf'>('')
const batchArchiving = ref(false)
const batchArchiveDialogVisible = ref(false)
const batchArchiveName = ref('')

function selectAllBatchTypes() {
  batchSelectedTypes.value = batchLedgerTypes.map(t => t.value)
}

async function batchExport(format: 'excel' | 'pdf') {
  if (batchSelectedTypes.value.length === 0) {
    ElMessage.warning('请至少选择一种台账类型')
    return
  }
  try {
    await ElMessageBox.confirm(
      `确认批量导出 ${batchSelectedTypes.value.length} 类台账（${format.toUpperCase()}）？导出文件将以 zip 压缩包形式下载。`,
      '批量导出确认',
      { type: 'info' },
    )
  } catch {
    return
  }
  batchExporting.value = format
  const notif = ElNotification({
    title: '批量导出',
    message: `正在生成 ${format.toUpperCase()} 压缩包，请稍候...`,
    type: 'info',
    duration: 0,
  })
  try {
    const params: any = {
      ledger_types: batchSelectedTypes.value,
      format,
    }
    if (batchDateRange.value && batchDateRange.value.length === 2) {
      params.date_from = batchDateRange.value[0]
      params.date_to = batchDateRange.value[1]
    }
    const blob = await ledgerApi.batchExport(params)
    const url = window.URL.createObjectURL(blob as any)
    const a = document.createElement('a')
    a.href = url
    const today = new Date().toISOString().split('T')[0]
    a.download = `台账批量导出_${today}.zip`
    a.click()
    window.URL.revokeObjectURL(url)
    notif.close()
    ElNotification({
      title: '批量导出',
      message: '导出成功，已开始下载',
      type: 'success',
    })
  } catch (e: any) {
    notif.close()
    ElNotification({
      title: '批量导出',
      message: '导出失败: ' + (e.response?.data?.detail || e.message || ''),
      type: 'error',
    })
  } finally {
    batchExporting.value = ''
  }
}

async function openBatchArchiveDialog() {
  if (!(await ensureWritableProject('批量归档'))) return
  if (batchSelectedTypes.value.length === 0) {
    ElMessage.warning('请至少选择一种台账类型')
    return
  }
  const today = new Date().toISOString().split('T')[0]
  batchArchiveName.value = `台账归档_${today}`
  batchArchiveDialogVisible.value = true
}

async function confirmBatchArchive() {
  if (!batchArchiveName.value.trim()) {
    ElMessage.warning('请输入归档名称')
    return
  }
  try {
    await ElMessageBox.confirm(
      `确认将 ${batchSelectedTypes.value.length} 类台账归档为 "${batchArchiveName.value}"？`,
      '批量归档确认',
      { type: 'info' },
    )
  } catch {
    return
  }
  batchArchiving.value = true
  const notif = ElNotification({
    title: '批量归档',
    message: '正在归档，请稍候...',
    type: 'info',
    duration: 0,
  })
  try {
    const params: any = {
      ledger_types: batchSelectedTypes.value,
      archive_name: batchArchiveName.value.trim(),
    }
    if (batchDateRange.value && batchDateRange.value.length === 2) {
      params.date_from = batchDateRange.value[0]
      params.date_to = batchDateRange.value[1]
    }
    await ledgerApi.batchArchive(params)
    notif.close()
    ElNotification({
      title: '批量归档',
      message: '归档成功',
      type: 'success',
    })
    batchArchiveDialogVisible.value = false
    // 归档成功后刷新归档列表（若已加载过）
    if (archivesLoaded.value) loadArchives()
  } catch (e: any) {
    notif.close()
    ElNotification({
      title: '批量归档',
      message: '归档失败: ' + (e.response?.data?.detail || e.message || ''),
      type: 'error',
    })
  } finally {
    batchArchiving.value = false
  }
}

// ===== 归档管理 =====
const archives = ref<any[]>([])
const archivesLoading = ref(false)
const archivesLoaded = ref(false)

async function loadArchives() {
  archivesLoading.value = true
  try {
    const res = await ledgerApi.listArchives()
    const data: any = res
    archives.value = Array.isArray(data) ? data : (data?.items || data?.archives || [])
    archivesLoaded.value = true
  } catch (e: any) {
    ElMessage.error('归档列表加载失败: ' + (e.response?.data?.detail || e.message || ''))
    archives.value = []
  } finally {
    archivesLoading.value = false
  }
}

async function downloadArchive(row: any) {
  try {
    const blob = await ledgerApi.downloadArchive(row.id)
    const url = window.URL.createObjectURL(blob as any)
    const a = document.createElement('a')
    a.href = url
    a.download = `${row.archive_name || ('archive_' + row.id)}.zip`
    a.click()
    window.URL.revokeObjectURL(url)
    ElMessage.success('归档文件下载已开始')
  } catch (e: any) {
    ElMessage.error('下载失败: ' + (e.response?.data?.detail || e.message || ''))
  }
}

async function confirmDeleteArchive(row: any) {
  try {
    await ElMessageBox.confirm(
      `确认删除归档 "${row.archive_name}"？该操作不可恢复。`,
      '删除确认',
      { type: 'warning' },
    )
    await ledgerApi.deleteArchive(row.id)
    ElMessage.success('归档已删除')
    loadArchives()
  } catch (e: any) {
    if (e !== 'cancel') ElMessage.error('删除失败: ' + (e.response?.data?.detail || e.message || ''))
  }
}

function onTabChange(tab: string) {
  if (tab === 'archives' && !archivesLoaded.value) loadArchives()
}

onMounted(async () => {
  await loadTypes()
  loadData()
})
</script>

<style scoped>
.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.batch-panel {
  background: #f5f7fa;
  border: 1px solid #e4e7ed;
  border-radius: 4px;
  padding: 16px;
  margin-bottom: 16px;
}
.batch-panel-title {
  font-weight: 600;
  font-size: 14px;
  margin-bottom: 12px;
  color: #303133;
}
.batch-form {
  margin: 0;
}
.batch-check-actions {
  margin-top: 4px;
}
.archive-selected-types {
  line-height: 1.6;
}
</style>
