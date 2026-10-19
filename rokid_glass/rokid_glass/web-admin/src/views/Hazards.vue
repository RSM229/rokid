<template>
  <div>
    <el-card>
      <template #header>
        <div class="card-header">
          <span>AI安全巡检数据</span>
          <div>
            <el-select v-model="statusFilter" placeholder="状态筛选" clearable style="width: 140px; margin-right: 8px">
              <el-option label="已提报" value="已提报" />
              <el-option label="已通知整改" value="已通知整改" />
              <el-option label="整改中" value="整改中" />
              <el-option label="待复查" value="待复查" />
              <el-option label="已闭环" value="已闭环" />
              <el-option label="驳回整改" value="驳回整改" />
            </el-select>
            <el-button type="primary" @click="loadData">刷新</el-button>
          </div>
        </div>
      </template>

      <el-table :data="hazards" stripe v-loading="loading">
        <el-table-column prop="id" label="ID" width="60" />
        <el-table-column prop="top1_risk" label="主要风险" min-width="150" />
        <el-table-column prop="risk_category" label="类别" width="100" />
        <el-table-column prop="risk_status" label="风险状态" width="100">
          <template #default="{ row }">
            <el-tag :type="row.risk_status === '存在隐患' ? 'danger' : 'success'">
              {{ row.risk_status || '-' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="section" label="部位" width="120" />
        <el-table-column prop="status" label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="getStatusType(row.status)">{{ row.status }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="created_at" label="时间" width="180" />
        <el-table-column label="操作" width="280" fixed="right">
          <template #default="{ row }">
            <el-button size="small" @click="viewDetail(row)">详情</el-button>
            <el-button
              v-if="row.status === '已提报'"
              size="small"
              type="warning"
              @click="createRectDoc(row)"
            >生成整改单</el-button>
            <el-button
              v-if="row.status === '待复查'"
              size="small"
              type="success"
              @click="recheck(row, true)"
            >复查合格</el-button>
            <el-button
              v-if="row.status === '待复查'"
              size="small"
              type="danger"
              @click="recheck(row, false)"
            >驳回</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- 详情对话框 -->
    <el-dialog v-model="detailVisible" title="隐患详情" width="700px">
      <el-descriptions :column="2" border v-if="currentHazard">
        <el-descriptions-item label="ID">{{ currentHazard.id }}</el-descriptions-item>
        <el-descriptions-item label="状态">{{ currentHazard.status }}</el-descriptions-item>
        <el-descriptions-item label="主要风险">{{ currentHazard.top1_risk }}</el-descriptions-item>
        <el-descriptions-item label="风险类别">{{ currentHazard.risk_category }}</el-descriptions-item>
        <el-descriptions-item label="风险状态">{{ currentHazard.risk_status }}</el-descriptions-item>
        <el-descriptions-item label="场景">{{ currentHazard.scene_related }}</el-descriptions-item>
        <el-descriptions-item label="部位">{{ currentHazard.section }}</el-descriptions-item>
        <el-descriptions-item label="位置">{{ currentHazard.location }}</el-descriptions-item>
        <el-descriptions-item label="创建时间">{{ currentHazard.created_at }}</el-descriptions-item>
        <el-descriptions-item label="不确定性">{{ currentHazard.uncertainty }}</el-descriptions-item>
      </el-descriptions>
      <div v-if="currentHazard?.evidence?.length" style="margin-top: 16px">
        <h4>证据</h4>
        <ul>
          <li v-for="(e, i) in currentHazard.evidence" :key="i">{{ e }}</li>
        </ul>
      </div>
      <div v-if="currentHazard?.recheck_suggestion?.length" style="margin-top: 16px">
        <h4>复查建议</h4>
        <ul>
          <li v-for="(s, i) in currentHazard.recheck_suggestion" :key="i">{{ s }}</li>
        </ul>
      </div>
      <div v-if="currentHazard?.image_path" style="margin-top: 16px">
        <h4>取证影像</h4>
        <el-image :src="currentHazard.image_path" style="max-width: 400px" fit="contain" />
      </div>
      <div v-if="currentHazard?.rectification_image_path" style="margin-top: 16px">
        <h4>整改影像</h4>
        <el-image :src="currentHazard.rectification_image_path" style="max-width: 400px" fit="contain" />
      </div>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { hazardApi } from '@/api'

const hazards = ref<any[]>([])
const loading = ref(false)
const statusFilter = ref('')
const detailVisible = ref(false)
const currentHazard = ref<any>(null)

function getStatusType(status: string) {
  const map: Record<string, string> = {
    '已提报': 'danger',
    '已通知整改': 'warning',
    '整改中': 'warning',
    '待复查': 'info',
    '已闭环': 'success',
    '驳回整改': 'danger',
  }
  return map[status] || 'info'
}

async function loadData() {
  loading.value = true
  try {
    const params: any = {}
    if (statusFilter.value) params.status_filter = statusFilter.value
    hazards.value = await hazardApi.list(params) as any[]
  } catch (e: any) {
    ElMessage.error('加载失败: ' + (e.message || ''))
  } finally {
    loading.value = false
  }
}

async function viewDetail(row: any) {
  try {
    currentHazard.value = await hazardApi.detail(row.id)
    detailVisible.value = true
  } catch (e: any) {
    ElMessage.error('加载详情失败')
  }
}

async function createRectDoc(row: any) {
  try {
    await ElMessageBox.confirm('确认为该隐患生成整改通知单？', '提示', { type: 'warning' })
    const res = await hazardApi.createRectificationDoc(row.id)
    ElMessage.success(`整改通知单已生成，文书ID: ${res.doc_id}`)
    loadData()
  } catch (e: any) {
    if (e !== 'cancel') ElMessage.error('生成失败: ' + (e.message || ''))
  }
}

async function recheck(row: any, passed: boolean) {
  try {
    const remark = await ElMessageBox.prompt(
      passed ? '请输入复查备注（可选）' : '请输入驳回原因',
      passed ? '复查合格' : '驳回整改',
      { inputType: 'textarea' }
    )
    await hazardApi.recheck(row.id, passed, remark.value)
    ElMessage.success(passed ? '已确认合格，隐患已闭环' : '已驳回整改')
    loadData()
  } catch (e: any) {
    if (e !== 'cancel') ElMessage.error('操作失败: ' + (e.message || ''))
  }
}

onMounted(() => {
  loadData()
})
</script>

<style scoped>
.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
</style>
