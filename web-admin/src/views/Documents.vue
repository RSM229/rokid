<template>
  <div class="documents-page">
    <el-card>
      <template #header>
        <div class="card-header">
          <span>文书审批</span>
          <el-button type="primary" @click="createDocument">新建文书</el-button>
        </div>
      </template>

      <!-- 文书列表 -->
      <el-table :data="documents" style="width: 100%">
        <el-table-column
          v-if="auth.allProjects"
          prop="project_name"
          label="所属项目"
          min-width="160"
          show-overflow-tooltip
        >
          <template #default="{ row }">
            {{ row.project_name || `项目 #${row.project_id}` }}
          </template>
        </el-table-column>
        <el-table-column prop="title" label="标题" />
        <el-table-column prop="doc_type" label="类型" />
        <el-table-column prop="status" label="状态">
          <template #default="{ row }">
            <el-tag :type="getStatusType(row.status)">
              {{ row.status }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="created_at" label="创建时间" />
        <el-table-column label="操作" width="300">
          <template #default="{ row }">
            <el-button size="small" @click="viewDocument(row)">查看</el-button>
            <el-button size="small" @click="openSign(row)">签认</el-button>
            <el-button size="small" type="primary" @click="editDocument(row)">编辑</el-button>
            <el-button size="small" type="danger" @click="deleteDocument(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- 编辑对话框 -->
    <el-dialog v-model="dialogVisible" :title="dialogTitle" width="600px">
      <el-form :model="form" label-position="top">
        <el-form-item label="标题">
          <el-input v-model="form.title" placeholder="请输入标题" />
        </el-form-item>

        <el-form-item label="类型">
          <el-select v-model="form.type" placeholder="请选择类型" style="width: 100%">
            <el-option label="监理通知单" value="监理工程师通知单（质量类）" />
            <el-option label="监理日志" value="监理日志" />
            <el-option label="监理周报" value="监理周报" />
            <el-option label="监理月报" value="监理月报" />
          </el-select>
        </el-form-item>

        <!-- 语音输入开关 -->
        <el-form-item label="语音输入">
          <el-switch v-model="speechEnabled" active-text="开启麦克风" inactive-text="关闭" />
          <span v-if="speechEnabled" class="speech-tip">
            （开启后，每个正文文本框旁将显示麦克风按钮，点击开始/停止语音识别）
          </span>
        </el-form-item>

        <!-- 正文内容（支持语音输入） -->
        <el-form-item
          v-for="f in speechFields"
          :key="f.key"
          :label="f.label"
        >
          <div class="speech-field-row">
            <el-input
              v-model="form[f.key]"
              type="textarea"
              :rows="f.rows || 4"
              :placeholder="f.placeholder || '请输入内容'"
              style="flex: 1"
            />
            <el-button
              v-if="speechEnabled"
              :type="fieldSpeechStates[f.key]?.listening ? 'danger' : 'primary'"
              :icon="fieldSpeechStates[f.key]?.listening ? 'Stop' : 'Microphone'"
              @click="toggleSpeech(f.key)"
              style="margin-left: 8px"
            >
              {{ fieldSpeechStates[f.key]?.listening ? '停止' : '语音' }}
            </el-button>
          </div>
          <div v-if="speechEnabled && !fieldSpeechStates[f.key]?.supported" class="speech-not-supported">
            当前浏览器不支持语音识别，请使用 Chrome/Edge
          </div>
          <div v-if="fieldSpeechStates[f.key]?.loading" class="speech-loading">
            正在识别中...
          </div>
        </el-form-item>
        <el-form-item v-for="field in structuredFields" :key="field.key" :label="field.label"><el-input v-model="structuredContent[field.key]" type="textarea" :rows="3" /></el-form-item>
      </el-form>

      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" @click="saveDocument" :loading="saving">保存</el-button>
      </template>
    </el-dialog>
    <el-dialog v-model="signVisible" title="本人签认 / 项目用章" width="600px" @closed="signForm.password = ''">
      <el-form label-width="100px">
        <el-form-item label="签认位置"><el-select v-model="signForm.slot"><el-option v-for="slot in signSlots" :key="slot.key" :label="slot.label" :value="slot.key" /></el-select></el-form-item>
        <el-form-item label="签章"><el-select v-model="signForm.signature_id"><el-option v-for="sig in signatures" :key="sig.id" :label="sig.name" :value="sig.id" /></el-select><span>个人签名可在电子签章管理中上传</span></el-form-item>
        <el-form-item label="保护密码"><el-input v-model="signForm.password" type="password" /></el-form-item>
      </el-form>
      <p v-for="(item, i) in signHistory" :key="i">{{ item.label }}：{{ item.signer }} · {{ item.signed_at }} · {{ item.current ? '当前版本' : '历史版本' }}</p>
      <template #footer><el-button @click="signVisible = false">取消</el-button><el-button type="primary" @click="confirmSign">本人授权签认</el-button></template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, onMounted, computed, nextTick } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { documentApi, signatureApi } from '@/api'
import { useAuthStore } from '@/stores/auth'
import { ensureWritableProject } from '@/composables/useAllProjectsGuard'

const auth = useAuthStore()
const documents = ref<any[]>([])
const dialogVisible = ref(false)
const dialogTitle = ref('新建文书')
const saving = ref(false)
const editingId = ref<number | null>(null)

const structuredFields = ref<{ key: string; label: string }[]>([])
const structuredContent = ref<Record<string, string>>({})
let originalContent: Record<string, any> = {}
const FIELD_LABELS: Record<string, string> = { temperature_high: '最高气温', temperature_low: '最低气温', weather_morning: '上午天气', weather_afternoon: '下午天气', weather_night: '晚上天气', construction_progress: '施工进展', material_section: '材料及见证', supervision_section: '旁站巡视', parallel_section: '平行检查', acceptance_section: '验收情况', problems_section: '问题及处理', meeting_section: '会议', document_section: '重要发文', closure_section: '闭合记录', report_section: '资料上报', misc_section: '其他事项', acceptance_participants: '验收单位及人员', construction_unit: '施工单位', supervision_org: '项目监理机构' }
function setStructuredContent(row: any) {
  originalContent = { ...(row.content || {}) }
  structuredFields.value = Object.keys(originalContent).filter(key => FIELD_LABELS[key]).map(key => ({ key, label: FIELD_LABELS[key] || key }))
  structuredContent.value = Object.fromEntries(structuredFields.value.map(field => [field.key, String(originalContent[field.key] ?? '')]))
}
const signVisible = ref(false)
const signDocumentId = ref(0)
const signSlots = ref<any[]>([])
const signHistory = ref<any[]>([])
const signatures = ref<any[]>([])
const signForm = reactive({ slot: '', signature_id: null as number | null, password: '' })
async function openSign(row: any) {
  if (!(await ensureWritableProject('签认文书'))) return
  try {
    const data: any = await signatureApi.signoffs(row.id)
    signDocumentId.value = row.id
    signSlots.value = data.slots
    signHistory.value = data.history
    signatures.value = await signatureApi.list() as any
    if (data.project_seal_signature_id && !signatures.value.some(s => s.id === data.project_seal_signature_id)) signatures.value.push({ id: data.project_seal_signature_id, name: '本项目机构章（需授权密码）' })
    signForm.slot = data.slots[0]?.key || ''
    signForm.signature_id = null
    signForm.password = ''
    signVisible.value = true
  } catch (e: any) { ElMessage.error(e?.response?.data?.detail || '签认信息加载失败') }
}
async function confirmSign() {
  if (!signForm.slot || !signForm.signature_id || !signForm.password) { ElMessage.warning('请选择签认位置、签章并输入密码'); return }
  try {
    await signatureApi.signDocument(signDocumentId.value, signForm.signature_id, signForm.password, signForm.slot)
    ElMessage.success('签认成功')
    signVisible.value = false
    await loadDocuments()
  } catch (e: any) { ElMessage.error(e?.response?.data?.detail || '签认失败') }
  finally { signForm.password = '' }
}

// 语音输入总开关
const speechEnabled = ref(false)

const form = reactive<Record<string, string>>({
  title: '',
  type: '',
  contentText: '',
  contentDetail: '',
  contentSummary: '',
})

// 文书正文文本字段配置
const speechFields = [
  { key: 'contentText', label: '内容补充', placeholder: '语音输入或手动填写', rows: 4 },
  { key: 'contentDetail', label: '详细内容', placeholder: '语音输入或手动填写', rows: 6 },
  { key: 'contentSummary', label: '内容摘要', placeholder: '语音输入或手动填写', rows: 3 },
]

// 每个字段的语音识别状态
const fieldSpeechStates = reactive<Record<string, {
  listening: boolean
  loading: boolean
  supported: boolean
  mediaRecorder?: MediaRecorder
}>>({})

// 初始化状态
for (const f of speechFields) {
  fieldSpeechStates[f.key] = {
    listening: false,
    loading: false,
    supported: typeof navigator !== 'undefined' && 'mediaDevices' in navigator && 'getUserMedia' in navigator.mediaDevices,
  }
}

// 加载文书列表
const loadDocuments = async () => {
  try {
    const res = await documentApi.list()
    documents.value = (res as any)?.items || res || []
  } catch (e) {
    ElMessage.error('加载文书列表失败')
  }
}

// 创建文书
const createDocument = async () => {
  if (!(await ensureWritableProject('新建文书'))) return
  dialogTitle.value = '新建文书'
  editingId.value = null
  structuredFields.value = []
  structuredContent.value = {}
  originalContent = {}
  form.title = ''
  form.type = ''
  form.contentText = ''
  form.contentDetail = ''
  form.contentSummary = ''
  dialogVisible.value = true
}

// 查看文书
const viewDocument = async (row: any) => {
  row = await documentApi.detail(row.id)
  setStructuredContent(row)
  dialogTitle.value = '查看文书'
  editingId.value = row.id
  form.title = row.title
  form.type = row.doc_type
  form.contentText = row.content?.content || row.content?.body || ''
  dialogVisible.value = true
}

// 编辑文书
const editDocument = async (row: any) => {
  if (!(await ensureWritableProject('编辑文书'))) return
  row = await documentApi.detail(row.id)
  setStructuredContent(row)
  dialogTitle.value = '编辑文书'
  editingId.value = row.id
  form.title = row.title
  form.type = row.doc_type
  form.contentText = row.content?.content || row.content?.body || ''
  dialogVisible.value = true
}

// 删除文书
const deleteDocument = async (row: any) => {
  if (!(await ensureWritableProject('删除文书'))) return
  try {
    await ElMessageBox.confirm('确定要删除该文书吗？', '提示', { type: 'warning' })
    await documentApi.delete(row.id)
    ElMessage.success('删除成功')
    await loadDocuments()
  } catch (e) {
    if (e !== 'cancel') {
      ElMessage.error('删除失败')
    }
  }
}

// 保存文书
const saveDocument = async () => {
  if (!form.title) {
    ElMessage.warning('请输入标题')
    return
  }
  saving.value = true
  try {
    if (editingId.value) {
      await documentApi.update(editingId.value, { title: form.title, content: { ...originalContent, ...structuredContent.value, content: form.contentText } })
      ElMessage.success('更新成功')
    } else {
      await documentApi.create({ title: form.title, doc_type: form.type, content: { content: form.contentText, details: form.contentDetail, summary: form.contentSummary } })
      ElMessage.success('创建成功')
    }
    dialogVisible.value = false
    await loadDocuments()
  } catch (e) {
    ElMessage.error('保存失败')
  } finally {
    saving.value = false
  }
}

// 获取状态标签颜色
const getStatusType = (status: string) => {
  const map: Record<string, 'info' | 'warning' | 'success' | 'danger'> = {
    '草稿': 'info',
    '待审批': 'warning',
    '已通过': 'success',
    '已驳回': 'danger',
  }
  return map[status] || 'info'
}

// ============ 后端语音识别 ============
const toggleSpeech = async (fieldKey: string) => {
  const state = fieldSpeechStates[fieldKey]
  
  if (state.listening) {
    // 停止录音
    if (state.mediaRecorder && state.mediaRecorder.state === 'recording') {
      state.mediaRecorder.stop()
    }
    state.listening = false
    return
  }

  // 检查浏览器是否支持录音
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    ElMessage.warning('当前浏览器不支持录音，请使用现代浏览器（Chrome/Edge/Firefox）')
    return
  }

  try {
    // 获取麦克风权限
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
    state.mediaRecorder = new MediaRecorder(stream)
    const chunks: Blob[] = []

    state.mediaRecorder.ondataavailable = (e: BlobEvent) => {
      if (e.data.size > 0) chunks.push(e.data)
    }

    state.mediaRecorder.onstop = async () => {
      state.loading = true
      try {
        const audioBlob = new Blob(chunks, { type: 'audio/webm' })
        const formData = new FormData()
        formData.append('file', audioBlob, 'speech.webm')
        
        // 调用后端语音识别接口
        const response = await fetch('/api/omni/transcribe', {
          method: 'POST',
          headers: {
            'Authorization': `Bearer ${auth.token}`,
          },
          body: formData,
        })
        
        if (!response.ok) {
          const errorData = await response.json()
          throw new Error(errorData.detail || '语音识别失败')
        }
        
        const result = await response.json()
        const text = result.text || ''
        
        if (text) {
          // 将识别结果追加到对应字段
          if (form[fieldKey]) {
            form[fieldKey] += (form[fieldKey] ? ' ' : '') + text
          } else {
            form[fieldKey] = text
          }
          ElMessage.success('语音识别完成')
        } else {
          ElMessage.warning('未识别到语音内容')
        }
      } catch (err: any) {
        ElMessage.error('语音识别失败: ' + (err.message || '未知错误'))
        console.error('STT Error:', err)
      } finally {
        state.loading = false
        // 释放资源
        if (stream) {
          stream.getTracks().forEach(track => track.stop())
        }
      }
    }

    // 开始录音
    state.mediaRecorder.start()
    state.listening = true
    ElMessage.success('正在录音，请说话...')

  } catch (err: any) {
    if (err.name === 'NotAllowedError' || err.name === 'PermissionDeniedError') {
      ElMessage.error('麦克风权限被拒绝，请在浏览器设置中允许麦克风访问')
    } else {
      ElMessage.error('录音启动失败: ' + (err.message || '未知错误'))
    }
    console.error('Microphone Error:', err)
  }
}

// 页面加载时获取数据
onMounted(async () => {
  await loadDocuments()
})
</script>

<style scoped>
.documents-page {
  padding: 20px;
}
.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.speech-tip {
  font-size: 12px;
  color: #909399;
  margin-left: 12px;
}
.speech-field-row {
  display: flex;
  align-items: flex-start;
  gap: 8px;
}
.speech-field-row .el-input {
  flex: 1;
}
.speech-not-supported {
  font-size: 12px;
  color: #e6a23c;
  margin-top: 4px;
}
.speech-loading {
  font-size: 12px;
  color: var(--el-color-primary);
  margin-top: 4px;
}
</style>
