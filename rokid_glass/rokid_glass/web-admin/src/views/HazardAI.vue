<template>
  <div class="hazard-page">
    <div class="page-header">
      <el-breadcrumb separator="/">
        <el-breadcrumb-item>监理工作台</el-breadcrumb-item>
        <el-breadcrumb-item>AI 隐患识别</el-breadcrumb-item>
      </el-breadcrumb>
    </div>

    <p class="page-desc">调用华工大模型进行 AI 安全隐患识别。</p>

    <el-row :gutter="20">
      <!-- 左侧：上传检测 -->
      <el-col :span="14">
        <el-card shadow="never" style="margin-bottom: 16px;">
          <template #header><span class="section-title">上传图片识别</span></template>
          <el-upload
            action="#"
            :auto-upload="false"
            :on-change="handleFileChange"
            :file-list="uploadFileList"
            accept=".jpg,.jpeg,.png"
            list-type="picture-card"
            :limit="1"
          >
            <el-icon><Plus /></el-icon>
          </el-upload>
          <div style="margin-top: 12px;">
            <el-button type="primary" @click="handleDetect" :loading="detecting" :disabled="!selectedFile">
              开始 AI 识别
            </el-button>
            <el-checkbox v-model="autoSave" style="margin-left: 16px;">自动保存到隐患库</el-checkbox>
          </div>
        </el-card>

        <!-- 检测结果 -->
        <el-card shadow="never" v-if="detectResult">
          <template #header><span class="section-title">检测结果</span></template>
          <el-descriptions :column="2" border size="small">
            <el-descriptions-item label="是否发现隐患">
              <el-tag :type="detectResult.has_hazard ? 'danger' : 'success'" size="small">
                {{ detectResult.has_hazard ? '是 — 发现隐患' : '否 — 未发现明显隐患' }}
              </el-tag>
            </el-descriptions-item>
            <el-descriptions-item label="置信度">{{ (detectResult.confidence * 100).toFixed(1) }}%</el-descriptions-item>
            <el-descriptions-item label="隐患类型" :span="2">{{ detectResult.hazard_type || '-' }}</el-descriptions-item>
            <el-descriptions-item label="隐患描述" :span="2">{{ detectResult.description || '-' }}</el-descriptions-item>
            <el-descriptions-item label="整改建议" :span="2">{{ detectResult.suggestion || '-' }}</el-descriptions-item>
          </el-descriptions>
          <div style="margin-top: 12px; display: flex; gap: 8px;">
            <el-button
              v-if="detectResult.has_hazard && !detectResult.id"
              size="small"
              type="primary"
              @click="saveHazard"
            >保存记录</el-button>
            <el-button v-if="detectResult.id" size="small" @click="createRectifyDoc">
              生成整改通知单
            </el-button>
          </div>
        </el-card>
      </el-col>

      <!-- 右侧：历史记录 -->
      <el-col :span="10">
        <el-card shadow="never">
          <template #header>
            <div style="display:flex; justify-content:space-between; align-items:center;">
              <span class="section-title">历史隐患记录</span>
              <el-select v-model="filterStatus" clearable size="small" placeholder="状态" style="width:100px">
                <el-option label="全部" value="" />
                <el-option label="已提报" value="已提报" />
                <el-option label="已通知整改" value="已通知整改" />
                <el-option label="整改中" value="整改中" />
                <el-option label="待复查" value="待复查" />
                <el-option label="已闭环" value="已闭环" />
              </el-select>
            </div>
          </template>
          <el-table
            :data="historyRecords"
            stripe
            empty-text="暂无记录"
            size="small"
            max-height="500"
            @row-click="(row: any) => viewRecord(row)"
            style="cursor: pointer;"
          >
            <el-table-column prop="id" label="#" width="45" />
            <el-table-column prop="hazard_type" label="类型" width="90" show-overflow-tooltip />
            <el-table-column prop="status" label="状态" width="80">
              <template #default="{ row }"><el-tag size="small">{{ row.status }}</el-tag></template>
            </el-table-column>
            <el-table-column prop="created_at" label="时间" width="150" />
          </el-table>
        </el-card>
      </el-col>
    </el-row>

    <!-- 详情弹窗 -->
    <el-dialog v-model="detailVisible" title="隐患详情" width="650px">
      <el-row :gutter="20" v-if="currentRecord">
        <el-col :span="10">
          <img :src="currentRecord.image_path" style="width:100%; border-radius:8px;" alt="" />
        </el-col>
        <el-col :span="14">
          <el-descriptions :column="1" border size="small">
            <el-descriptions-item label="ID">{{ currentRecord.id }}</el-descriptions-item>
            <el-descriptions-item label="隐患类型">{{ currentRecord.hazard_type }}</el-descriptions-item>
            <el-descriptions-item label="状态"><el-tag>{{ currentRecord.status }}</el-tag></el-descriptions-item>
            <el-descriptions-item label="描述">{{ currentRecord.description }}</el-descriptions-item>
            <el-descriptions-item label="建议">{{ currentRecord.suggestion }}</el-descriptions-item>
          </el-descriptions>
          <div style="margin-top: 12px;" v-if="currentRecord.status === '待复查'">
            <el-input placeholder="复查备注..." size="small" v-model="recheckRemark" />
            <div style="margin-top:8px; display:flex; gap:8px;">
              <el-button size="small" type="success" @click="doRecheck(true)">通过</el-button>
              <el-button size="small" type="danger" @click="doRecheck(false)">不通过</el-button>
            </div>
          </div>
        </el-col>
      </el-row>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { Plus } from '@element-plus/icons-vue'
import { hazardApi } from '@/api'

const detecting = ref(false)
const autoSave = ref(true)
const selectedFile = ref<File | null>(null)
const uploadFileList = ref<any[]>([])
const detectResult = ref<any>(null)
const historyRecords = ref<any[]>([])
const filterStatus = ref('')
const detailVisible = ref(false)
const currentRecord = ref<any>(null)
const recheckRemark = ref('')

function handleFileChange(file: any) {
  if (file.raw) {
    selectedFile.value = file.raw
    uploadFileList.value = [file]
    detectResult.value = null
  }
}

async function handleDetect() {
  if (!selectedFile.value) return ElMessage.warning('请先选择图片')
  detecting.value = true
  try {
    const res: any = autoSave.value
      ? await hazardApi.detectAndSave(selectedFile.value!)
      : await hazardApi.detect(selectedFile.value!)
    detectResult.value = res
    loadHistory()
  } catch (e: any) {
    ElMessage.error(e.response?.data?.detail || 'AI 识别失败，请检查模型服务')
  } finally { detecting.value = false }
}

async function saveHazard() {
  if (!selectedFile.value || !detectResult.value?.has_hazard) return
  try {
    detectResult.value = await hazardApi.detectAndSave(selectedFile.value!)
    ElMessage.success('已保存'); loadHistory()
  } catch (e: any) { ElMessage.error('保存失败') }
}

async function createRectifyDoc() {
  if (!detectResult.value?.id) return ElMessage.warning('请先保存记录')
  try {
    await hazardApi.createRectificationDoc(detectResult.value.id)
    ElMessage.success('整改通知单已生成')
  } catch (e: any) { ElMessage.error('生成失败') }
}

function viewRecord(row: any) {
  currentRecord.value = row; detailVisible.value = true
}

async function doRecheck(passed: boolean) {
  if (!currentRecord.value) return
  try {
    await hazardApi.recheck(currentRecord.value.id, passed, recheckRemark.value || undefined)
    ElMessage.success(passed ? '复查通过' : '复查不通过，继续整改')
    detailVisible.value = false; loadHistory()
  } catch (e: any) { ElMessage.error('操作失败') }
}

async function loadHistory() {
  try {
    historyRecords.value = await hazardApi.list({
      status_filter: filterStatus.value || undefined,
    }) as any[]
  } catch {}
}

onMounted(() => loadHistory())
</script>

<style scoped>
.page-header { margin-bottom: 16px; }
.page-desc { color: #999; font-size: 13px; margin-bottom: 16px; }
.section-title { font-weight: 600; font-size: 15px; }
</style>
