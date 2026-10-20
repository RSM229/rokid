<template>
  <div class="dashboard-page">
    <!-- 当前层级指示器 -->
    <div class="level-indicator">
      <el-breadcrumb separator="/">
        <el-breadcrumb-item>监理工作台</el-breadcrumb-item>
        <el-breadcrumb-item v-if="currentLevel1">{{ currentLevel1 }}</el-breadcrumb-item>
        <el-breadcrumb-item v-if="currentLevel2">{{ currentLevel2 }}</el-breadcrumb-item>
        <el-breadcrumb-item v-if="currentLevel3">{{ currentLevel3 }}</el-breadcrumb-item>
      </el-breadcrumb>
    </div>

    <!-- 中部：智能体对话框（占据主要空间） -->
    <el-card shadow="never" class="chat-section">
      <div class="chat-header">
        <el-icon size="28"><ChatDotRound /></el-icon>
        <img src="/baize-logo.png" alt="" class="chat-agent-logo" />
        <span class="chat-title">白泽</span>
        <el-tag size="small" type="info" effect="plain" style="margin-left: 8px;">
          {{ currentLevel1 || '主页' }}{{ currentLevel2 ? ' / ' + currentLevel2 : '' }}{{ currentLevel3 ? ' / ' + currentLevel3 : '' }}
        </el-tag>
      </div>

      <!-- 聊天消息列表 -->
      <div class="chat-messages" ref="messagesRef">
        <div v-for="(msg, i) in chatMessages" :key="i" class="message-item" :class="msg.role">
          <div class="avatar">
            <el-icon v-if="msg.role === 'user'" size="32"><User /></el-icon>
            <el-icon v-else size="32"><ChatDotRound /></el-icon>
          </div>
          <div class="bubble">
            <img v-if="msg.image" :src="msg.image" class="chat-image" @click="previewImage(msg.image)" />
            <p v-for="(line, j) in msg.content.split('\n')" :key="j">{{ line }}</p>
          </div>
        </div>
      </div>

      <!-- 输入区域 -->
      <div class="chat-input-area">
        <!-- 待发送图片预览 -->
        <div v-if="pendingImage" class="pending-image-row">
          <div class="pending-image-wrap">
            <img :src="pendingImage" class="pending-image" />
            <el-icon class="pending-image-close" @click="clearPendingImage"><Close /></el-icon>
          </div>
        </div>

        <el-input
          v-model="chatInput"
          type="textarea"
          :rows="4"
          placeholder="请输入指令，如&quot;新建巡视记录&quot;、&quot;启动隐患识别&quot;、&quot;生成监理日志&quot;..."
          @keyup.enter.prevent="sendMessage"
        >
          <template #append>
            <el-button @click="sendMessage">发送</el-button>
          </template>
        </el-input>

        <!-- 底部工具栏 -->
        <div class="toolbar-row">
          <div class="select-group">
            <span class="select-label">模型</span>
            <el-tag size="small" effect="plain">{{ selectedModel }}</el-tag>
          </div>

          <div class="select-group">
            <span class="select-label">知识库</span>
            <el-select v-model="selectedKB" placeholder="选择知识库" size="small" style="width: 180px;">
              <el-option label="全部" value="all" />
              <el-option label="省统表" value="省统表" />
              <el-option label="安全监理用表" value="安全监理用表" />
              <el-option label="知识图谱" value="knowledge-graph" />
            </el-select>
          </div>

          <el-upload
            action="#"
            :auto-upload="false"
            :show-file-list="false"
            :on-change="handleInsertImage"
            accept="image/jpeg,image/png,image/webp"
          >
            <el-button size="small" type="primary" plain>
              <el-icon style="margin-right: 4px;"><Picture /></el-icon>导入现场图片
            </el-button>
          </el-upload>

          <el-tag v-if="hazardMode" size="small" type="warning" effect="plain">
            隐患识别模式
          </el-tag>
          <span class="toolbar-hint">输入“启动隐患识别”后导入现场图片，识别结果会直接显示在对话中</span>
        </div>
      </div>
    </el-card>

    <!-- 底部：工作记录统计卡片（仅显示未查看/未处理） -->
    <div class="stats-footer">
      <div class="stats-title">
        <span class="title-text">工作记录（未查看 / 未处理）</span>
        <span class="view-all" @click="goToRecordList('all')">查看全部 ›</span>
      </div>
      <div class="stats-cards">
        <div class="stat-card-item" v-for="(stat, i) in workStats" :key="i" @click="goToRecordList(stat.key)">
          <div class="card-icon" :style="{ background: stat.bgColor }">
            <el-icon :size="24" :color="stat.iconColor"><component :is="stat.icon" /></el-icon>
          </div>
          <div class="card-content">
            <div class="card-title">{{ stat.label }}</div>
            <div class="card-desc">{{ stat.desc }}</div>
            <div class="card-bottom">
              <span class="card-count" :class="{ 'count-zero': stat.unviewed === 0 }">{{ stat.unviewed }}</span>
              <span class="card-unit">未查看</span>
              <span class="card-separator">/</span>
              <span class="card-count pending" :class="{ 'count-zero': stat.pending === 0 }">{{ stat.pending }}</span>
              <span class="card-unit">未处理</span>
            </div>
          </div>
        </div>
      </div>

      <!-- 隐患待处理 -->
      <div class="hazard-pending-bar" @click="goToHazardList">
        <el-icon size="20" color="#fa8c16"><Warning /></el-icon>
        <span class="hazard-pending-text">
          安全隐患待处理：<strong>{{ hazardPending }}</strong> 条
        </span>
        <el-icon size="14"><ArrowRight /></el-icon>
      </div>
    </div>

    <!-- 监理日志生成弹窗 -->
    <el-dialog v-model="logDialogVisible" title="生成监理日志" width="500px">
      <el-form label-width="100px">
        <el-form-item label="日期">
          <el-date-picker
            v-model="logDate"
            type="date"
            value-format="YYYY-MM-DD"
            placeholder="选择日期"
            style="width: 100%;"
          />
        </el-form-item>
      </el-form>
      <div v-if="logResult" class="log-result">
        <el-alert :title="logResult.message" :type="logResult.success ? 'success' : 'error'" :closable="false" />
        <div v-if="logResult.doc_id" class="log-actions">
          <el-button size="small" type="primary" @click="viewLogPdf(logResult.doc_id)">查看PDF</el-button>
          <el-button size="small" @click="goToDocumentList">前往文书列表</el-button>
        </div>
      </div>
      <template #footer>
        <el-button @click="logDialogVisible = false">关闭</el-button>
        <el-button type="primary" :loading="logGenerating" @click="generateDailyLog">生成</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, nextTick, markRaw } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { workRecordApi, documentApi } from '@/api'
import {
  ChatDotRound, User, View, CircleCheck, Monitor, Camera, Document,
  Warning, ArrowRight, Plus, EditPen, Reading, Document as DocIcon, Notebook,
  Picture, Close,
} from '@element-plus/icons-vue'

const router = useRouter()

// ===== 聊天相关 =====
const chatMessages = ref<any[]>([
  { role: 'assistant', content: '您好！我是白泽，您的 AI 工程监理智能体。\n您可以通过语音或文字指令进行操作。\n\n可用指令：\n• "新建巡视记录" - 进入巡视记录创建\n• "启动隐患识别" - 在对话中导入现场图片识别\n• "生成监理日志" - 自动生成当日监理日志\n• "知识库" - 前往信息库\n• "返回" - 返回上一级' },
])
const chatInput = ref('')
const messagesRef = ref<HTMLElement | null>(null)
const pendingImage = ref<string | null>(null)
const pendingImageFile = ref<File | null>(null)
const hazardMode = ref(false)

// ===== 层级导航 =====
const currentLevel1 = ref('')
const currentLevel2 = ref('')
const currentLevel3 = ref('')

// ===== 工具栏状态 =====
const selectedModel = ref('加载中...')
const selectedKB = ref('all')

// ===== 工作统计加载（未查看/未处理） =====
const workStats = ref([
  { key: 'inspection', label: '巡视记录', desc: '现场巡视检查', icon: markRaw(View), unviewed: 0, pending: 0, bgColor: '#e6f4ff', iconColor: '#1890ff' },
  { key: 'acceptance', label: '验收记录', desc: '工程质量验收', icon: markRaw(CircleCheck), unviewed: 0, pending: 0, bgColor: '#f6ffed', iconColor: '#52c41a' },
  { key: 'station', label: '旁站记录', desc: '关键工序旁站', icon: markRaw(Monitor), unviewed: 0, pending: 0, bgColor: '#fff7e6', iconColor: '#fa8c16' },
  { key: 'witness', label: '见证记录', desc: '见证取样送检', icon: markRaw(Camera), unviewed: 0, pending: 0, bgColor: '#f9f0ff', iconColor: '#722ed1' },
  { key: 'other', label: '其他工作', desc: '其他工作事项', icon: markRaw(DocIcon), unviewed: 0, pending: 0, bgColor: '#e6fffb', iconColor: '#13c2c2' },
])
const hazardPending = ref(0)

// ===== 监理日志生成 =====
const logDialogVisible = ref(false)
const logDate = ref(new Date().toISOString().split('T')[0])
const logGenerating = ref(false)
const logResult = ref<any>(null)

// ===== 指令路由表 =====
type CommandRoute = {
  level1?: string
  level2?: string
  level3?: string
  navigate?: string
  localAction?: 'hazard-recognition'
  reply: string
}

const commandRoutes: Record<string, CommandRoute> = {
  // 一级：工作记录
  '工作记录': { level1: '工作记录', reply: '已进入工作记录。请选择：巡视记录、验收记录、旁站记录、见证记录、其他工作' },
  '打开工作记录': { level1: '工作记录', reply: '已进入工作记录。请选择：巡视记录、验收记录、旁站记录、见证记录、其他工作' },
  // 二级
  '巡视记录': { level1: '工作记录', level2: '巡视记录', reply: '已进入巡视记录。可执行：新建巡视记录、查看巡视列表', navigate: '/work-record/inspection' },
  '巡视': { level1: '工作记录', level2: '巡视记录', reply: '已进入巡视记录。可执行：新建巡视记录、查看巡视列表', navigate: '/work-record/inspection' },
  '验收记录': { level1: '工作记录', level2: '验收记录', reply: '已进入验收记录。可执行：新建验收记录、查看验收列表', navigate: '/work-record/acceptance' },
  '验收': { level1: '工作记录', level2: '验收记录', reply: '已进入验收记录。可执行：新建验收记录、查看验收列表', navigate: '/work-record/acceptance' },
  '旁站记录': { level1: '工作记录', level2: '旁站记录', reply: '已进入旁站记录。可执行：新建旁站记录、查看旁站列表', navigate: '/work-record/station' },
  '旁站': { level1: '工作记录', level2: '旁站记录', reply: '已进入旁站记录。可执行：新建旁站记录、查看旁站列表', navigate: '/work-record/station' },
  '见证记录': { level1: '工作记录', level2: '见证记录', reply: '已进入见证记录。可执行：新建见证记录、查看见证列表', navigate: '/work-record/witness' },
  '见证': { level1: '工作记录', level2: '见证记录', reply: '已进入见证记录。可执行：新建见证记录、查看见证列表', navigate: '/work-record/witness' },
  // 新建操作
  '新建巡视记录': { level1: '工作记录', level2: '巡视记录', level3: '新建', reply: '正在跳转到巡视记录页面，请填写巡视信息...', navigate: '/work-record/inspection' },
  '新建验收记录': { level1: '工作记录', level2: '验收记录', level3: '新建', reply: '正在跳转到验收记录页面...', navigate: '/work-record/acceptance' },
  '新建旁站记录': { level1: '工作记录', level2: '旁站记录', level3: '新建', reply: '正在跳转到旁站记录页面...', navigate: '/work-record/station' },
  '新建见证记录': { level1: '工作记录', level2: '见证记录', level3: '新建', reply: '正在跳转到见证记录页面...', navigate: '/work-record/witness' },
  // 一级：安全隐患
  '安全隐患': { level1: '安全隐患', reply: '已进入安全隐患。可执行：启动隐患识别、查看隐患列表' },
  '隐患识别': { level1: '安全隐患', level2: 'AI识别', reply: '隐患识别已就绪。请点击“导入现场图片”，可附加检查要求后发送，结果会直接显示在当前对话中。', localAction: 'hazard-recognition' },
  '启动隐患识别': { level1: '安全隐患', level2: 'AI识别', reply: '隐患识别已启动。请点击“导入现场图片”，可附加检查要求后发送。', localAction: 'hazard-recognition' },
  '启动安全ai巡检': { level1: '安全隐患', level2: 'AI巡检', reply: 'AI巡检已启动。请导入现场图片，识别结果会直接显示在当前对话中。', localAction: 'hazard-recognition' },
  '停止安全ai巡检': { level1: '', level2: '', level3: '', reply: '安全AI巡检已停止。' },
  // 现场识别（摄像头）
  '现场识别': { level1: '安全隐患', level2: '现场识别', reply: '正在打开现场识别页面...', navigate: '/camera-simulator' },
  '拍照': { reply: '正在打开现场识别页面，请使用摄像头拍照...', navigate: '/camera-simulator' },
  // 监理日志
  '生成监理日志': { level1: '文书管理', level2: '监理日志', reply: '请选择要生成监理日志的日期。' },
  '监理日志': { level1: '文书管理', level2: '监理日志', reply: '请选择要生成监理日志的日期。' },
  // 台账
  '查看台账': { level1: '台账管理', reply: '正在打开台账管理页面...', navigate: '/ledger' },
  '台账': { level1: '台账管理', reply: '正在打开台账管理页面...', navigate: '/ledger' },
  // 知识库
  '知识库': { level1: '信息库', reply: '正在打开知识库页面...', navigate: '/knowledge-base' },
  '查看规范': { level1: '信息库', reply: '正在打开知识库页面...', navigate: '/knowledge-base' },
  // 文书审批
  '文书审批': { level1: '文书管理', level2: '文书审批', reply: '正在打开文书审批页面...', navigate: '/documents' },
  // 返回
  '返回': { reply: '已返回主页。' },
  '返回主页': { reply: '已返回主页。' },
  '主页': { reply: '已返回主页。' },
}

function matchCommand(text: string): (CommandRoute & { isLog?: boolean }) | null {
  const lower = text.toLowerCase().trim()

  // 精确匹配
  if (commandRoutes[lower]) {
    return { ...commandRoutes[lower], isLog: lower.includes('监理日志') }
  }

  // 模糊匹配
  for (const [key, route] of Object.entries(commandRoutes)) {
    if (lower.includes(key)) {
      return { ...route, isLog: key.includes('监理日志') }
    }
  }

  return null
}

async function sendMessage() {
  const userMsg = chatInput.value.trim()
  const hasImage = !!pendingImage.value && !!pendingImageFile.value
  if (!userMsg && !hasImage) return

  // 用户消息加入列表（包含图片）
  chatMessages.value.push({
    role: 'user',
    content: userMsg || '请识别这张图片中的隐患',
    image: pendingImage.value || undefined,
  })
  chatInput.value = ''
  await nextTick()
  scrollToBottom()

  // 如果有图片，优先进行 AI 隐患识别
  if (hasImage) {
    await detectHazardWithImage(userMsg || '请识别图片中的隐患')
    clearPendingImage()
    return
  }

  // 先尝试本地指令路由
  const route = matchCommand(userMsg)

  if (route) {
    // 处理返回
    if (userMsg.includes('返回') || userMsg === '主页') {
      currentLevel1.value = ''
      currentLevel2.value = ''
      currentLevel3.value = ''
      chatMessages.value.push({ role: 'assistant', content: route.reply })
      await nextTick()
      scrollToBottom()
      return
    }

    // 更新层级
    if (route.level1 !== undefined) currentLevel1.value = route.level1
    if (route.level2 !== undefined) currentLevel2.value = route.level2
    if (route.level3 !== undefined) currentLevel3.value = route.level3

    chatMessages.value.push({ role: 'assistant', content: route.reply })

    if (route.localAction === 'hazard-recognition') {
      hazardMode.value = true
    }

    // 监理日志特殊处理
    if (route.isLog) {
      logResult.value = null
      logDialogVisible.value = true
    }

    // 页面跳转
    if (route.navigate) {
      setTimeout(() => {
        router.push(route.navigate!)
      }, 800)
    }

    await nextTick()
    scrollToBottom()
    return
  }

  // 未匹配本地指令，调用后端 LLM
  await callLlmChat(userMsg)
}

async function callLlmChat(userMsg: string) {
  try {
    const resp = await fetch('/api/knowledge/llm-query', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${localStorage.getItem('token') || ''}`,
      },
      body: JSON.stringify({
        question: userMsg,
        use_llm: true,
        knowledge_scope: selectedKB.value,
      }),
    })

    if (resp.ok) {
      const data = await resp.json()
      chatMessages.value.push({
        role: 'assistant',
        content: data.answer || formatKnowledgeFallback(data.context_results) || '知识库中暂未找到相关内容。',
      })
    } else {
      chatMessages.value.push({ role: 'assistant', content: '服务暂时不可用，请稍后再试。\n您也可以使用快捷指令按钮进行操作。' })
    }
  } catch (e: any) {
    chatMessages.value.push({ role: 'assistant', content: '网络错误：' + e.message + '\n您也可以使用快捷指令按钮进行操作。' })
  }

  await nextTick()
  scrollToBottom()
}

async function loadActiveModel() {
  try {
    const response = await fetch('/api/local/llm/status', {
      headers: { Authorization: `Bearer ${localStorage.getItem('token') || ''}` },
    })
    if (!response.ok) throw new Error('status unavailable')
    const data = await response.json()
    selectedModel.value = [data.provider, data.current_model].filter(Boolean).join(' / ') || '未配置'
  } catch {
    selectedModel.value = '未配置'
  }
}



function handleInsertImage(file: any) {
  const raw = file.raw
  if (!raw || !raw.type?.startsWith('image/')) {
    ElMessage.warning('请选择图片文件')
    return
  }
  if (pendingImage.value) URL.revokeObjectURL(pendingImage.value)
  pendingImageFile.value = raw
  pendingImage.value = URL.createObjectURL(raw)
  hazardMode.value = true
  currentLevel1.value = '安全隐患'
  currentLevel2.value = 'AI识别'
  ElMessage.success('现场图片已导入，可输入检查要求后发送')
}

function clearPendingImage() {
  if (pendingImage.value) URL.revokeObjectURL(pendingImage.value)
  pendingImage.value = null
  pendingImageFile.value = null
}

function formatKnowledgeFallback(results: any[] | undefined): string {
  if (!results?.length) return ''
  return [
    '从所选知识范围检索到以下相关内容：',
    ...results.slice(0, 5).map((item) => `• ${item.title || item.name || item.source_file}：${item.snippet || ''}`),
  ].join('\n')
}

function previewImage(src: string) {
  window.open(src, '_blank')
}

async function detectHazardWithImage(userMsg: string) {
  if (!pendingImageFile.value) return

  chatMessages.value.push({ role: 'assistant', content: `正在分析图片：${userMsg}...` })
  await nextTick()
  scrollToBottom()

  try {
    const form = new FormData()
    form.append('image', pendingImageFile.value)
    form.append('prompt', userMsg)

    const resp = await fetch('/api/hazard/detect', {
      method: 'POST',
      headers: { Authorization: `Bearer ${localStorage.getItem('token')}` },
      body: form,
    })

    if (resp.ok) {
      const data = await resp.json()
      const resultText = formatHazardResult(data)
      chatMessages.value.push({ role: 'assistant', content: resultText })
      hazardMode.value = false
    } else {
      const err = await resp.text()
      chatMessages.value.push({ role: 'assistant', content: `隐患识别失败：${err || '请检查图片格式'}` })
    }
  } catch (e: any) {
    chatMessages.value.push({ role: 'assistant', content: '识别错误：' + e.message })
  }

  await nextTick()
  scrollToBottom()
}

function formatHazardResult(data: any): string {
  let text = '隐患识别结果：\n'
  if (data.risk_status) text += `风险状态：${data.risk_status}\n`
  if (data.risk_category) text += `风险类别：${data.risk_category}\n`
  if (data.top1_risk) text += `主要风险：${data.top1_risk}\n`
  if (data.evidence && data.evidence.length) text += `证据：${data.evidence.join('、')}\n`
  if (data.recheck_suggestion && data.recheck_suggestion.length) text += `复查建议：${data.recheck_suggestion.join('、')}\n`
  return text
}

function scrollToBottom() {
  if (messagesRef.value) {
    messagesRef.value.scrollTop = messagesRef.value.scrollHeight
  }
}

// ===== 工作统计加载（未查看/未处理） =====
async function loadWorkStats() {
  try {
    const stats: any = await workRecordApi.stats() as any

    // 映射统计结果
    const wr = stats?.work_records || {}
    workStats.value[0].unviewed = wr['巡视']?.unviewed || 0
    workStats.value[0].pending = wr['巡视']?.pending || 0
    workStats.value[1].unviewed = wr['验收']?.unviewed || 0
    workStats.value[1].pending = wr['验收']?.pending || 0
    workStats.value[2].unviewed = wr['旁站']?.unviewed || 0
    workStats.value[2].pending = wr['旁站']?.pending || 0
    workStats.value[3].unviewed = wr['见证']?.unviewed || 0
    workStats.value[3].pending = wr['见证']?.pending || 0
    workStats.value[4].unviewed = wr['其他']?.unviewed || 0
    workStats.value[4].pending = wr['其他']?.pending || 0

    hazardPending.value = stats?.hazards?.pending || 0
  } catch {
    // 静默失败，统计为0
  }
}

// ===== 导航 =====
function goToRecordList(key: string) {
  const routeMap: Record<string, string> = {
    inspection: '/work-record/inspection',
    acceptance: '/work-record/acceptance',
    station: '/work-record/station',
    witness: '/work-record/witness',
    all: '/work-record/inspection',
  }
  const path = routeMap[key]
  if (path) router.push(path)
}

function goToHazardList() {
  hazardMode.value = true
  currentLevel1.value = '安全隐患'
  currentLevel2.value = 'AI识别'
  chatMessages.value.push({
    role: 'assistant',
    content: `当前有 ${hazardPending.value} 条安全隐患待处理。可点击“导入现场图片”继续识别，结果会直接显示在这里。`,
  })
  window.scrollTo({ top: 0, behavior: 'smooth' })
  void nextTick().then(scrollToBottom)
}

function goToDocumentList() {
  logDialogVisible.value = false
  router.push('/documents')
}

// ===== 监理日志生成 =====
async function generateDailyLog() {
  if (!logDate.value) {
    ElMessage.warning('请选择日期')
    return
  }
  logGenerating.value = true
  logResult.value = null
  try {
    const res: any = await documentApi.generateDailyLog(logDate.value) as any
    logResult.value = {
      success: true,
      message: `监理日志已生成（文书ID: ${res.doc_id}），包含 ${res.summary?.work_records || 0} 条工作记录、${res.summary?.hazards || 0} 条隐患记录。`,
      doc_id: res.doc_id,
    }
    ElMessage.success('监理日志生成成功')
  } catch (e: any) {
    logResult.value = {
      success: false,
      message: e.response?.data?.detail || '生成失败，请检查后端服务是否正常',
    }
    ElMessage.error('监理日志生成失败')
  } finally {
    logGenerating.value = false
  }
}

async function viewLogPdf(docId: number) {
  try {
    const res: any = await documentApi.exportPdf(docId) as any
    if (res.pdf_url) {
      window.open(res.pdf_url, '_blank')
    }
  } catch {
    ElMessage.error('PDF导出失败')
  }
}

onMounted(() => {
  void loadWorkStats()
  void loadActiveModel()
})
</script>

<style scoped>
.dashboard-page { max-width: 1400px; margin: 0 auto; }

/* 层级指示器 */
.level-indicator { margin-bottom: 12px; }

/* 聊天区域 */
.chat-section { display: flex; flex-direction: column; }
.chat-header { display: flex; align-items: center; gap: 8px; padding: 12px 0; }
.chat-title { font-weight: 600; font-size: 15px; }
.chat-agent-logo {
  width: 30px;
  height: 30px;
  border-radius: 8px;
  object-fit: cover;
  object-position: center top;
  border: 1px solid #d9e7f3;
}
.chat-messages { max-height: 400px; overflow-y: auto; padding: 0 12px; border: 1px solid #e8e8e8; border-radius: 4px; background: #fafafa; }
.message-item { display: flex; gap: 10px; margin-bottom: 12px; align-items: flex-start; margin-top: 12px; }
.message-item.user { flex-direction: row-reverse; }
.avatar { width: 32px; height: 32px; border-radius: 50%; background: #f0f0f0; display: flex; align-items: center; justify-content: center; flex-shrink: 0; }
.bubble { max-width: 70%; padding: 6px 10px; border-radius: 6px; background: #fff; line-height: 1.5; font-size: 14px; border: 1px solid #e8e8e8; white-space: pre-wrap; }
.bubble p { margin: 0; }
.message-item.user .bubble { background: #409EFF; color: #fff; border-color: #409EFF; }
.chat-image { max-width: 200px; max-height: 160px; border-radius: 4px; margin-bottom: 6px; cursor: pointer; display: block; }

/* 输入区域 */
.chat-input-area { padding: 12px 0 0; flex-shrink: 0; }
.pending-image-row { margin-bottom: 8px; }
.pending-image-wrap { position: relative; display: inline-block; }
.pending-image { max-width: 120px; max-height: 90px; border-radius: 4px; border: 1px solid #d9d9d9; }
.pending-image-close { position: absolute; top: -8px; right: -8px; width: 18px; height: 18px; background: #ff4d4f; color: #fff; border-radius: 50%; cursor: pointer; font-size: 12px; display: flex; align-items: center; justify-content: center; }
.toolbar-row { display: flex; align-items: center; gap: 12px; margin-top: 8px; flex-wrap: wrap; }
.toolbar-hint { font-size: 12px; color: #909399; margin-left: auto; }
.select-group { display: flex; align-items: center; gap: 8px; }
.select-label { font-size: 13px; color: #606266; white-space: nowrap; }

/* 统计区域 */
.stats-footer {
  margin-top: 20px;
  background: #f5f7fa;
  padding: 16px 24px;
  border-radius: 8px;
  border: 1px solid #e8e8e8;
}
.stats-title {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
}
.title-text { font-size: 16px; font-weight: 600; color: #333; }
.view-all { font-size: 13px; color: #409eff; cursor: pointer; }
.view-all:hover { text-decoration: underline; }
.stats-cards { display: flex; gap: 16px; }
.stat-card-item {
  flex: 1;
  background: #fff;
  border-radius: 8px;
  padding: 16px;
  display: flex;
  gap: 12px;
  cursor: pointer;
  border: 1px solid #e8e8e8;
  transition: all 0.2s;
}
.stat-card-item:hover { box-shadow: 0 4px 12px rgba(0, 0, 0, 0.1); transform: translateY(-2px); }
.card-icon {
  width: 48px; height: 48px; border-radius: 8px;
  display: flex; align-items: center; justify-content: center; flex-shrink: 0;
}
.card-content { flex: 1; display: flex; flex-direction: column; justify-content: space-between; }
.card-title { font-size: 15px; font-weight: 600; color: #333; }
.card-desc { font-size: 12px; color: #999; margin-top: 2px; }
.card-bottom { display: flex; align-items: baseline; gap: 4px; margin-top: 8px; }
.card-count { font-size: 24px; font-weight: 700; color: #fa541c; }
.card-count.pending { color: #fa8c16; }
.card-count.count-zero { color: #999; }
.card-unit { font-size: 12px; color: #999; }
.card-separator { font-size: 14px; color: #ccc; margin: 0 4px; }

/* 隐患待处理栏 */
.hazard-pending-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 16px;
  padding: 12px 16px;
  background: #fff7e6;
  border: 1px solid #ffd591;
  border-radius: 8px;
  cursor: pointer;
  transition: all 0.2s;
}
.hazard-pending-bar:hover { background: #fff1cc; }
.hazard-pending-text { flex: 1; font-size: 14px; color: #333; }
.hazard-pending-text strong { color: #fa541c; font-size: 18px; }

/* 监理日志生成结果 */
.log-result { margin-top: 16px; }
.log-actions { margin-top: 12px; display: flex; gap: 8px; }
</style>
