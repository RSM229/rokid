import { request } from '../../services/request'
import { session } from '../../services/session'
import { needsHazardDetection } from '../../services/attachment-intent'
import { uploadAudio, uploadDocument, uploadImage, friendlyServerError } from '../../services/upload'
import { persistTempFile, uploadQueue } from '../../services/uploadQueue'
import { absoluteMediaUrl, config } from '../../config'
import { getCached, invalidateCache, invalidateByPrefix, CACHE_KEYS } from '../../services/cache'
import { ensurePrivacy } from '../../utils/privacy'
import { themed } from '../../utils/theme'
import { cleanMarkdown } from '../../utils/markdown'
import { createReviewPlan, saveReviewPlan, confirmReviewPlan, executeReviewPlan, editEvent, confirmPlan, reviewIssues, type ReviewPlan } from '../../services/work-review'

/** 整理计划当前可直接执行的记录表式；其余表式属于独立文书/台账流程。 */
const INLINE_REVIEW_FORMS = ['巡视记录', '危大工程巡视记录', '旁站记录', '平行检验记录', '验收记录']

const recorder = wx.getRecorderManager()
const player = wx.createInnerAudioContext()

/** 智能体执行回执卡（工作记录/隐患/文书） */
interface AgentCard {
  kind: 'work_record' | 'hazard' | 'document' | string
  id: number
  title: string
  sub?: string
  status?: string
  time?: string
}

interface Message {
  role: 'user' | 'assistant'
  text: string
  hint?: string
  image?: string
  images?: string[]
  type?: 'text' | 'actions' | 'stats'
  actions?: { label: string; command: string }[]
  /** 工作记录字段候选（点选即填入，也可自己填写） */
  commands?: { field: string; label: string; options: string[] }[]
  /** 智能体回执卡（由 /agent/chat 返回，点击可跳详情） */
  cards?: AgentCard[]
  /** 追问的快速候选值（点击即作为回答发送，如 巡视/旁站/验收/见证） */
  chips?: string[]
  reviewPlan?: ReviewPlan
  reviewEvents?: Array<{
    id: string
    title: string
    date: string
    dateConfirmed: boolean
    dateOptions: string[]
    forms: string[]
    formsText: string
    formOptions: Array<{ label: string; checked: boolean }>
    evidenceCount: number
  }>
  reviewBusy?: boolean
  reviewResult?: string
  reviewIssues?: string[]
}

/** /agent/chat 响应：kind=action 表示服务端已执行动作，chat 表示纯问答 */
interface AgentChatResponse {
  kind: 'action' | 'chat'
  tool?: string
  reply: string
  card?: AgentCard | null
  cards?: AgentCard[]
  actions?: { type: string; label: string; url?: string }[]
  need_more?: string[]
  options?: Record<string, string[]>
  undo?: { entity: string; id: number } | null
  model?: string
  degraded?: boolean
}

interface ChatHistory {
  id: number
  title: string
  modelId: string
  messages: Message[]
  updatedAt: string
}

function conversationIdFor(sessionId: number): string {
  return sessionId ? `wx-${sessionId}` : ''
}

/** 待发送附件：来自相册/相机/文件的本地临时路径 */
interface Attachment {
  id: string
  kind: 'image' | 'file'
  path: string
  name: string
  ext?: string
  size?: number
}

const HISTORY_KEY = `gdjl_${config.storageScope}_chat_history`

interface CommandRoute {
  level1?: string
  level2?: string
  level3?: string
  navigate?: string
  localAction?: string
  reply: string
}

const COMMAND_ROUTES: Record<string, CommandRoute> = {
  // 「工作记录 / 巡视 / 旁站 / 验收 / 见证」不再走指令跳转（2026-10-14 用户拍板删除弹窗界面）：
  // 这些词直接落到 /agent/chat 由智能体建记录；「XX记录」保留为打开对应页面的指令。
  '巡视记录': { level1: '工作记录', level2: '巡视记录', navigate: '/pages/work/index', reply: '正在打开巡视记录...' },
  '验收记录': { level1: '工作记录', level2: '验收记录', navigate: '/pages/work/index', reply: '正在打开验收记录...' },
  '旁站记录': { level1: '工作记录', level2: '旁站记录', navigate: '/pages/work/index', reply: '正在打开旁站记录...' },
  '见证记录': { level1: '工作记录', level2: '见证记录', navigate: '/pages/work/index', reply: '正在打开见证记录...' },
  '安全隐患': { level1: '安全隐患', reply: '可执行操作：启动隐患识别、查看隐患列表' },
  '隐患识别': { level1: '安全隐患', level2: 'AI识别', localAction: 'hazard-recognition', reply: '请点击拍照导入现场图片进行隐患识别' },
  '启动隐患识别': { level1: '安全隐患', level2: 'AI识别', localAction: 'hazard-recognition', reply: '请点击拍照导入现场图片进行隐患识别' },
  '隐患列表': { level1: '安全隐患', level2: '隐患列表', navigate: '/pages/hazards/index', reply: '正在打开隐患列表...' },
  'AI隐患识别': { level1: '安全隐患', level2: 'AI识别', navigate: '/pages/capture/index?mode=hazard', reply: '正在打开AI隐患识别...' },
  '拍照': { level1: '安全隐患', level2: '现场识别', navigate: '/pages/capture/index', reply: '正在打开拍照识别...' },
  '生成监理日志': { level1: '文书管理', level2: '监理日志', localAction: 'daily-log', reply: '请选择日期生成监理日志：' },
  '监理日志': { level1: '文书管理', level2: '监理日志', localAction: 'daily-log', reply: '请选择日期生成监理日志：' },
  '查看台账': { level1: '台账管理', navigate: '/pages/documents/index?tab=ledger', reply: '正在打开台账...' },
  '台账': { level1: '台账管理', navigate: '/pages/documents/index?tab=ledger', reply: '正在打开台账...' },
  '知识库': { level1: '信息库', localAction: 'knowledge-query', reply: '请选择资料范围后输入问题查询：' },
  // 「资料库」＝「知识库」新名称（2026-10-09 改名，两个词都保留为可识别指令）
  '资料库': { level1: '信息库', localAction: 'knowledge-query', reply: '请选择资料范围后输入问题查询：' },
  '我的知识库': { level1: '资料库', navigate: '/pages/kb/index', reply: '正在打开我的资料库...' },
  '我的资料库': { level1: '资料库', navigate: '/pages/kb/index', reply: '正在打开我的资料库...' },
  '知识库管理': { level1: '资料库', navigate: '/pages/kb/index', reply: '正在打开资料库...' },
  '资料库管理': { level1: '资料库', navigate: '/pages/kb/index', reply: '正在打开资料库...' },
  '查看规范': { level1: '信息库', localAction: 'knowledge-query', reply: '好的，请输入要查询的规范或资料问题：' },
  '文书审批': { level1: '文书管理', level2: '文书审批', navigate: '/pages/documents/index', reply: '正在打开文书审批...' },
  '监理文书': { level1: '文书管理', navigate: '/pages/documents/index', reply: '正在打开监理文书...' },
  '待办任务': { level1: '任务管理', navigate: '/pages/todo/index', reply: '正在打开待办任务...' },
  '任务': { level1: '任务管理', navigate: '/pages/todo/index', reply: '正在打开任务列表...' },
  'AI巡检': { level1: '安全隐患', level2: 'AI巡检', navigate: '/pages/inspection/index', reply: '正在打开AI巡检...' },
  '巡检': { level1: '安全隐患', level2: 'AI巡检', navigate: '/pages/inspection/index', reply: '正在打开AI巡检...' },
  '规范查询': { level1: '信息库', navigate: '/pages/knowledge/index', reply: '正在打开规范查询...' },
  '返回': { reply: '已返回主页' },
  '返回主页': { reply: '已返回主页' },
  '主页': { reply: '已返回主页' },
}

/** 工作台「模型」下拉可选的大模型清单（2026-10-09 改版：原为千问/华工两供应商，现扩展为多个模型）。
 * 2026-10-09 14:13 按需求精简为「华工 + 千问 3.7」两项系列（共 3 条）。
 *  - provider='' 走默认文本大模型端点（阿里云 token-plan），用 model 字段覆盖具体模型名；
 *  - provider='huagong' 走华南理工独立供应商（未配置则后端自动回退默认模型）。 */
interface ChatModel {
  id: string
  name: string
  desc: string
  provider: string
  model: string
  temperature: number
}

const CHAT_MODELS: ChatModel[] = [
  { id: 'qwen3.7-max', name: '通义千问 3.7 Max', desc: '最强推理，复杂规范分析', provider: '', model: 'qwen3.7-max', temperature: 0.7 },
  { id: 'qwen3.7-plus', name: '通义千问 3.7 Plus', desc: '均衡通用，日常问答', provider: '', model: 'qwen3.7-plus', temperature: 0.7 },
  { id: 'huagong', name: '华工大模型', desc: '华南理工，视觉/深度', provider: 'huagong', model: '', temperature: 0.6 },
]

/** 工作记录字段候选值：语音里没说清楚的字段，先给常用项让用户点选，不合适再自己填写 */
const FIELD_SUGGESTIONS: Record<string, string[]> = {
  section: ['1号楼', '2号楼', '3号楼', '地下室', '主体结构', '基础工程'],
  location: ['一层', '二层', '三层', '屋面', '基坑', '外墙'],
  work_content: ['钢筋绑扎', '模板安装', '混凝土浇筑', '砌体施工', '防水施工', '脚手架搭设'],
  weather: ['晴', '多云', '阴', '小雨', '中雨', '大雨', '雪'],
  inspection_remark: ['现场情况正常', '无异常', '已口头提醒施工单位'],
  problems: ['未发现问题', '发现轻微问题已当场整改', '存在安全隐患需整改'],
  treatment: ['口头通知整改', '下发监理通知单', '已复查合格'],
  acceptance_type: ['隐蔽工程验收', '分项工程验收', '检验批验收', '材料进场验收'],
  acceptance_content: ['钢筋规格数量', '模板尺寸标高', '混凝土外观质量', '防水层搭接'],
  acceptance_result: ['合格', '不合格', '整改后合格'],
  station_project_name: ['混凝土浇筑', '土方回填', '桩基施工', '防水施工'],
  station_location: ['地下室', '一层', '二层', '屋面'],
  station_content: ['混凝土浇筑旁站', '钢筋绑扎旁站', '防水施工旁站'],
  station_supervision_status: ['旁站正常，未发现问题', '发现问题已要求整改', '施工符合规范要求'],
  witness_material_name: ['钢筋', '水泥', '砂石', '外加剂', '防水卷材', '混凝土试块'],
  witness_quantity: ['1组', '2组', '3组', '1批'],
  witness_unit: ['组', '批', '吨', 'm³'],
  witness_accept_result: ['合格', '待检测', '不合格'],
}

const WORK_FIELD_LABELS: Record<string, string> = {"section": "工程部位", "location": "具体位置", "work_content": "现场施工/工作内容", "weather": "天气", "inspection_remark": "巡视备注", "problems": "发现问题", "treatment": "处理措施", "worker_count": "施工人数（正式提交必填）", "inspection_start_time": "巡视开始时间 YYYY-MM-DD HH:mm", "inspection_end_time": "巡视结束时间 YYYY-MM-DD HH:mm", "record_date": "业务日期 YYYY-MM-DD（默认现场当天）", "acceptance_type": "验收类型", "acceptance_content": "验收内容", "acceptance_basis": "验收依据", "acceptance_participants": "实际参验单位及人员", "acceptance_result": "人工确认的验收结论", "station_project_name": "旁站工程名称", "station_location": "旁站关键部位", "station_process": "旁站关键工序", "start_time": "旁站开始时间 YYYY-MM-DD HH:mm 或 HH:mm", "end_time": "旁站结束时间 YYYY-MM-DD HH:mm 或 HH:mm", "station_content": "旁站施工情况", "station_supervision_status": "旁站监理情况", "station_conclusion": "旁站结论", "witness_material_name": "材料名称", "witness_material_spec": "规格型号", "witness_quantity": "取样数量", "witness_unit": "计量单位", "witness_cert_no": "合格证/检测报告编号", "witness_accept_result": "进场验收结论", "manufacturer": "生产厂家", "remark": "备注"}

Page(themed({
  discardRecording: false,
  longPressTimer: null as number | null,
  touchStartTime: 0,
  /** 录音真正开始的时间戳：用于「按太短＝误触」判定，避免上传空音频 */
  recordingStartedAt: 0,
  /** 授权/启动尚未完成、用户就已松手 → 置真取消本次启动（松手即结束语义的另一半） */
  pendingRecordCancel: false,
  /** 当前会话在本地历史里的 id（稳定不变，重复保存=覆盖同一条，避免产生重复历史） */
  currentSessionId: 0,
  inlinePlanSaveQueues: {} as Record<string, Promise<void>>,

  data: {
    messages: [] as Message[],
    userName: '',
    // ima 风格空状态：竖排建议按钮（icon + 文案），点击走 handleActionTap
    heroActions: [
      { icon: 'ic-mic', text: '开启语音，记录现场工作', command: '工作记录' },
      { icon: 'ic-camera', text: '拍照识别安全隐患', command: '拍照' },
      { icon: 'ic-book', text: '查询规范资料库', command: '知识库' },
      { icon: 'ic-doc', text: '生成今日监理日志', command: '生成监理日志' },
    ] as { icon: string; text: string; command: string }[],
    // 侧边栏「工作文书」子项：对应工作记录生成的各类文书（PDF/Word）
    docEntries: [
      { key: 'xunshi', label: '巡视记录', url: '/pages/documents/index?type=xunshi' },
      { key: 'pangzhan', label: '旁站记录', url: '/pages/documents/index?type=pangzhan' },
      { key: 'jianzheng', label: '见证记录', url: '/pages/documents/index?type=jianzheng' },
      { key: 'yanshou', label: '验收记录', url: '/pages/documents/index?type=yanshou' },
      { key: 'daiding', label: '待定记录', url: '/pages/documents/index?type=daiding' },
    ] as { key: string; label: string; url: string }[],
    input: '',
    loading: false,
    recording: false,
    speaking: false,
    showHistory: false,
    // 侧边栏各组默认收起（点分组标题展开），避免抽屉过长
    drawerDocsExpanded: false,
    drawerLedgerExpanded: false,
    drawerHistoryExpanded: false,
    historyList: [] as ChatHistory[],
    mode: 'chat' as 'chat' | 'hazard',

    showProjectForm: false,
    /** 'create' 新建项目 | 'edit' 修改当前项目概况（侧边栏「项目概况」点进来） */
    projectFormMode: 'create' as 'create' | 'edit',
    projectForm: { projectName: '', location: '', description: '', constructionUnit: '', supervisionUnit: '' },
    projectCreated: false,
    projectName: '',
    projectLocation: '',
    showProjectPicker: false,
    currentProjectId: 0,
    projectList: [] as { id: number; name: string; code: string; location?: string; description?: string; construction_unit?: string; supervision_unit?: string }[],
    switchingProject: false,
    projectSummary: '',

    statusBarHeight: 0,
    navBarHeight: 44,

    pendingImage: '',
    hazardMode: false,
    knowledgeScope: 'all',

    // 回答模型（2026-10-09 改版：输入区左侧「模型」下拉，可切换多个文本大模型；华工走独立供应商）
    models: CHAT_MODELS,
    currentModel: 'qwen3.7-max',
    modelName: '通义千问 3.7 Max',
    showModelMenu: false,
    showPlusPanel: false,
    /** 待发送附件：图片/文件可多选先挂在这里，补充文字后一次性发送识别 */
    attachments: [] as Attachment[],
    canSend: false,

    breadcrumb: [] as string[],

    showLogDialog: false,
    logDate: '',
    logWeather: '',
    logTempLow: '',
    logTempHigh: '',
    logGenerating: false,
    // 与省统表 schema（provform draft-2026.09）select 选项完全一致
    weatherOptions: ['晴', '多云', '阴', '小雨', '中雨', '大雨', '雪'],

    showWorkForm: false,
    workFormFields: [] as { key: string; label: string }[],
    workFormType: 'inspection',
    workForm: {} as Record<string, string>,
    workFormSubmitting: false,
    voiceSessionActive: false,
    // 录音上传失败后进入待同步队列的条数（PRD 19.5）
    pendingVoiceCount: 0,
    pendingVoiceRetrying: false,
    voiceSessionTexts: [] as string[],
    voiceSessionExtracting: false,
    voiceSessionExtracted: false,
    voiceSessionInfo: '',
    // 语音拍照自动创建工作记录
    voiceWorkMode: false,
    voiceWorkPending: false,
    voiceWorkPhoto: '',
    voiceWorkAccumulated: '',
    voiceWorkCreating: false,
    /** 本次语音记录是否已问过「是否需要补齐现场照片」 */
    workPhotoAsked: false,
    /** 待补充的工作记录字段（点选候选值即填入） */
    workMissing: [] as { field: string; label: string; options: string[] }[],

    showTaskForm: false,
    taskForm: { title: '', description: '', assignee_id: '', priority: 'normal' },
    assignableUsers: [] as { id: number; real_name: string }[],
    taskSubmitting: false,

    showDocForm: false,
    docForm: { title: '', content: '', doc_type: '巡视记录' },
    // 必须与后端 DocType 枚举完全一致，否则创建文书返回 422
    docTypes: ['巡视记录', '监理日志', '旁站记录', '见证取样记录', '监理工程师通知单（质量类）', '安全隐患整改通知单'],
    docPolishing: false,
    docSubmitting: false,
    // 待补全草稿（聊天即操作的半成品，落库持久化、跨会话可见）：顶部横幅用
    pendingDrafts: [] as Array<{ tool: string; label: string; missingText: string }>,
    draftFocus: false,
  },

  /** 语音记录现场工作：说完自动提取的防抖定时器 */
  voiceAutoTimer: null as number | null,

  onLoad(options: Record<string, string>) {
    const sysInfo = wx.getWindowInfo()
    this.setData({
      statusBarHeight: sysInfo.statusBarHeight || 20,
      navBarHeight: 44,
    })

    const user = session.getUser()
    const savedModel = String(wx.getStorageSync(`gdjl_${config.storageScope}_agent_model`) || 'qwen3.7-max')
    const saved = this.data.models.find(m => m.id === savedModel) || this.data.models[0]
    this.setData({
      userName: user?.real_name || '监理人员',
      currentModel: saved.id,
      modelName: saved.name,
    })
    if (user?.project_id) {
      this.setData({ projectCreated: true, projectName: user.project_name || '', currentProjectId: user.project_id })
    } else {
      this.setData({ showProjectForm: true })
    }

    const now = new Date()
    this.setData({
      logDate: `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`,
    })

    this.initMessages()
    this.initRecorder()
    if (['inspection', 'station', 'acceptance', 'witness'].includes(options.createRecord || '')) {
      this.openWorkForm(options.createRecord!)
    }
  },

  onShow() {
    if (!session.getToken()) {
      wx.redirectTo({ url: '/pages/login/index' })
      return
    }
    this.refreshPendingVoice()
    this.loadPendingDrafts()
  },

  onHide() {
    if (this.voiceAutoTimer !== null) {
      clearTimeout(this.voiceAutoTimer)
      this.voiceAutoTimer = null
    }
    // 底栏切页用 redirectTo 会销毁本页，先落盘当前会话，避免对话丢失
    this.saveCurrentSession()
    if (this.data.recording) {
      this.discardRecording = true
      recorder.stop()
    }
    if (this.data.speaking) player.stop()
  },

  onUnload() {
    this.saveCurrentSession()
    player.stop()
  },

  /** 拉取当前用户的待补全草稿，用于在聊天页顶部挂「待补全」提示横幅 */
  async loadPendingDrafts() {
    try {
      const res = await request<{
        count: number
        drafts: Array<{ tool: string; label: string; missing: string[] }>
      }>('/agent/drafts', { data: { conversation_id: conversationIdFor(this.currentSessionId) } })
      const drafts = (res.drafts || []).map(d => ({
        tool: d.tool,
        label: d.label,
        missingText: (d.missing || []).join('、'),
      }))
      this.setData({ pendingDrafts: drafts })
    } catch {
      this.setData({ pendingDrafts: [] })
    }
  },

  /** 「继续填写」：聚焦输入框（先失焦再聚焦，保证每次点击都能重新唤起键盘） */
  focusDraftInput() {
    this.setData({ draftFocus: false })
    setTimeout(() => this.setData({ draftFocus: true }), 60)
  },

  /** 放弃当前横幅展示的那条草稿 */
  discardDraft() {
    const d = this.data.pendingDrafts[0]
    if (!d) return
    wx.showModal({
      title: '放弃草稿',
      content: `放弃这条未完成的「${d.label}」？放弃后无法恢复。`,
      confirmText: '放弃',
      success: async (r) => {
        if (!r.confirm) return
        try {
          await request('/agent/drafts/clear', { method: 'POST', data: {
            tool: d.tool, conversation_id: conversationIdFor(this.currentSessionId),
          } })
          this.setData({ pendingDrafts: [] })
          this.addMessage({ role: 'assistant', text: `已放弃「${d.label}」草稿。` })
        } catch (e) {
          wx.showToast({ title: (e as Error).message, icon: 'none' })
        }
      },
    })
  },

  initMessages() {
    if (!this.currentSessionId) this.currentSessionId = Date.now()
    // 欢迎语仅作占位：messages.length <= 1 时页面显示 ima 风格 Hero（大问候语 + 竖排建议），不渲染气泡
    this.setData({
      messages: [{
        role: 'assistant',
        text: '您好，我是白泽监理智能助手。可以询问规范流程、生成文书、管理隐患等。',
      }],
    })
  },

  initRecorder() {
    recorder.onStop(async (result) => {
      this.setData({ recording: false, loading: true })
      if (this.discardRecording) {
        this.discardRecording = false
        this.setData({ loading: false })
        return
      }
      try {
        const transcript = await uploadAudio<{ text: string }>(result.tempFilePath)
        if (!transcript.text) throw new Error('没有识别到有效语音')
        if (this.data.voiceWorkMode) {
          // 语音拍照自动创建模式：累计文本
          this.setData({ voiceSessionTexts: [...this.data.voiceSessionTexts, transcript.text] })
          wx.showToast({ title: '语音已记录', icon: 'success' })
          // 停 2.2 秒内没有新的语音 → 自动提取并创建，无需再手动点「完成记录」
          if (this.voiceAutoTimer !== null) clearTimeout(this.voiceAutoTimer)
          this.voiceAutoTimer = setTimeout(() => {
            this.voiceAutoTimer = null
            if (this.data.voiceWorkCreating) return
            if (!this.data.voiceSessionTexts.length) return
            // 还没拍现场照片 → 先问一句「是否需要补齐现场照片」，再生成记录
            if (this.askWorkPhoto()) return
            void this.autoCreateWorkSession()
          }, 2200) as unknown as number
          // 如果 AI 正在追问（voiceWorkPending），立刻触发再次提取
          if (this.data.voiceWorkPending && !this.data.voiceWorkCreating) {
            this.setData({ voiceWorkPending: false })
            if (!this.askWorkPhoto()) void this.autoCreateWorkSession()
          }
        } else if (this.data.voiceSessionActive) {
          this.setData({ voiceSessionTexts: [...this.data.voiceSessionTexts, transcript.text] })
          wx.showToast({ title: '语音片段已记录', icon: 'success' })
        } else {
          this.setData({ input: transcript.text })
          await this.send()
        }
      } catch (error) {
        // PRD 19.5：录音上传失败进入本地待同步队列，用户可取消或重试
        const saved = await persistTempFile(result.tempFilePath)
        uploadQueue.enqueue('audio', saved, {}, (error as Error).message)
        this.refreshPendingVoice()
        wx.showModal({
          title: '语音上传失败',
          content: `${(error as Error).message}\n已存入待同步队列，可点击「重试语音上传」补传。`,
          showCancel: false,
        })
      } finally {
        this.setData({ loading: false })
      }
    })
    recorder.onError((error) => {
      this.setData({ recording: false })
      const msg = error.errMsg || ''
      // 真机 `operateRecorder:fail:start record fail` 实测（2026-10-13）：授权状态
      // 决定根因——denied=去设置；granted 却仍启动失败 ≈ 平台《用户隐私保护指引》
      // 未声明「麦克风」或未过审（平台禁用，2026-10-09 chooseMedia 同款机制），
      // 或麦克风被音视频通话等占用。按 getSetting 结果分流提示，不再弹天书 errMsg。
      wx.getSetting({
        success: (res) => {
          const record = res.authSetting['scope.record']
          if (record === false || /auth|deny/i.test(msg)) {
            wx.showModal({
              title: '需要麦克风权限',
              content: '请在设置中允许使用麦克风',
              confirmText: '去设置',
              success: (s) => { if (s.confirm) wx.openSetting({}) },
            })
          } else if (record === true) {
            wx.showModal({
              title: '录音暂不可用',
              content: '麦克风权限已开启，但录音启动失败。多为小程序后台《用户隐私保护指引》尚未生效，请稍后重试；若反复出现，请完全退出微信后重新打开再试。',
              showCancel: false,
              confirmText: '知道了',
            })
          } else {
            wx.showToast({ title: '录音启动失败，请再按住试一次', icon: 'none' })
          }
        },
        fail: () => {
          wx.showToast({ title: msg || '录音失败', icon: 'none' })
        },
      })
    })
    player.onEnded(() => this.setData({ speaking: false }))
    player.onStop(() => this.setData({ speaking: false }))
    player.onError(() => {
      this.setData({ speaking: false })
      wx.showToast({ title: '音频播放失败', icon: 'none' })
    })
  },

  matchCommand(text: string): CommandRoute | null {
    const lower = text.toLowerCase().trim()
    if (COMMAND_ROUTES[lower]) return COMMAND_ROUTES[lower]
    // 创建/录入类意图不走路由跳转：直连智能体在对话里建记录（聊天即操作）。
    // 修复「我要创建一条巡视记录」被子串命中「巡视记录」跳到工作记录页的问题。
    if (/创建|新建|新增|录入|登记|建一条|写一条|记录一条|生成一条/.test(lower)) return null
    // 子串兜底只认「短指令」（≤10 字且非疑问句）：修复
    // 「混凝土浇筑旁站监理的要点有哪些？」因含「旁站」被劫持跳工作记录的问题。
    // 长句 / 疑问句一律落到 AI 问答。
    if (lower.length > 10) return null
    if (/[?？]|哪些|什么|怎么|如何|为什么|要点|介绍|讲解|说明|区别/.test(lower)) return null
    for (const key of Object.keys(COMMAND_ROUTES)) {
      if (lower.includes(key)) return COMMAND_ROUTES[key]
    }
    return null
  },

  async send() {
    const text = this.data.input.trim()
    const attachments = this.data.attachments
    if ((!text && attachments.length === 0) || this.data.loading) return
    if (!this.data.projectCreated) {
      wx.showToast({ title: '请先创建项目', icon: 'none' })
      return
    }

    // 有附件 → 多图/多文件 + 补充文字，一次性识别
    if (attachments.length) {
      await this.sendAttachments(attachments, text)
      return
    }

    // 语音记录现场工作进行中：输入框里的文字作为字段补充，而不是开启新话题
    if (this.data.voiceWorkMode) {
      this.addMessage({ role: 'user', text })
      this.setData({
        input: '',
        canSend: false,
        voiceSessionTexts: [...this.data.voiceSessionTexts, text],
      })
      if (this.voiceAutoTimer !== null) {
        clearTimeout(this.voiceAutoTimer)
        this.voiceAutoTimer = null
      }
      void this.autoCreateWorkSession()
      return
    }

    const userMsg: Message = { role: 'user', text }
    this.setData({ input: '', canSend: false })

    // 上一轮是智能体追问（带快速选项/待补充字段）→ 本句按「答案」处理，直连智能体。
    // 否则「巡视」「晴」这类短答会被指令表跳转或被知识范围拦截，中断建记录流程。
    const lastAssistant = [...this.data.messages].reverse().find(m => m.role === 'assistant')
    if (lastAssistant && ((lastAssistant.chips && lastAssistant.chips.length) || lastAssistant.hint)) {
      this.handleLlmChat(text, userMsg)
      return
    }

    const route = this.matchCommand(text)
    if (route) {
      this.handleCommand(route, userMsg)
      return
    }

    if (this.data.knowledgeScope !== 'all') {
      this.handleKnowledgeQuery(text, userMsg)
      return
    }

    this.handleLlmChat(text, userMsg)
  },

  /** 多附件识别：先读水印，再交视觉模型逐张识别，文件交文档解析 */
  async sendAttachments(items: Attachment[], note: string) {
    const images = items.filter(i => i.kind === 'image')
    const files = items.filter(i => i.kind === 'file')
    const parts: string[] = []
    if (images.length) parts.push(`${images.length} 张图片`)
    if (files.length) parts.push(`${files.length} 个文件`)
    const summary = parts.join(' + ')

    const userMsg: Message = {
      role: 'user',
      text: note || `请识别这${summary}`,
      images: images.map(i => i.path),
    }
    const messages = [...this.data.messages, userMsg] as Message[]
    this.setData({
      input: '',
      attachments: [],
      canSend: false,
      messages,
      loading: true,
    })

    const blocks: string[] = []
    const runHazardDetection = needsHazardDetection(note)
    /** 每张图的原始识别结果：聊天里只展示结论，落库时复用同一份结论（不再调一次视觉模型） */
    const detections: { path: string; raw: any }[] = []
    const watermarks: Record<string, string>[] = []
    /** 水印读到的拍摄时间/地点/天气——相机记的比转述可靠，用来预填记录字段 */
    const wmFields: Record<string, string> = {}
    try {
      // 普通记录只提取水印；只有用户明确提到隐患/安全问题时才调用隐患视觉模型。
      if (images.length) {
        const results = await Promise.all(images.map(async (img) => {
          const wm = await this.readWatermark(img.path)
          const det = runHazardDetection ? await this.detectImageOnce(img.path) : null
          return { path: img.path, det, wm }
        }))
        watermarks.push(...results.map(result => result.wm))
        results.forEach((res, i) => {
          if (res.det) detections.push({ path: res.path, raw: res.det.raw })
          const wmLine = this.formatWatermark(res.wm)
          const detectionLine = res.det ? `\n${res.det.text}` : ''
          blocks.push(wmLine
            ? `【图片 ${i + 1}】\n${wmLine}${detectionLine}`
            : `【图片 ${i + 1}】${detectionLine || '\n已收到图片，未进行隐患识别'}`)
          for (const key of Object.keys(res.wm)) {
            if (res.wm[key] && !wmFields[key]) wmFields[key] = res.wm[key]
          }
        })
      }
      // 文件：逐个解析文本
      for (const file of files) {
        try {
          const parsed = await uploadDocument<{ filename?: string; text?: string }>(file.path)
          const content = (parsed.text || '').trim()
          blocks.push(content
            ? `【文件《${parsed.filename || file.name}》】\n${content.slice(0, 3000)}`
            : `【文件《${file.name}》】未能提取到文本内容`)
        } catch (error) {
          blocks.push(`【文件《${file.name}》】解析失败：${(error as Error).message}`)
        }
      }

      const material = blocks.join('\n\n')
      if (!runHazardDetection) {
        if (images.length) {
          await this.createReviewPlanFromAttachments(note, images, watermarks)
          return
        }
        await this.runAgentWithMaterial(
          note || '记录今天的工作', material, this.watermarkPrefill(wmFields), messages, wmFields,
          images.length > 0, images.map(i => i.path),
        )
        return
      }
      // 明确的隐患分析通道：识别命中后再征求是否建档
      this.setData({ hazardMode: true, mode: 'hazard' })

      const question = note
        ? `${note}\n\n以下是随消息发送的素材内容（系统已识别，你无需再识别）：\n${material}\n\n请结合以上素材回答我的问题。`
        : `以下是现场发送的素材（图片识别结果、水印与文件内容）：\n${material}\n\n请综合判断是否存在安全隐患或需要监理处理的事项，并给出处理建议。`

      const selModel = this.data.models.find(m => m.id === this.data.currentModel)
      const result = await request<{ answer?: string; reply?: string }>('/local/llm/chat', {
        method: 'POST',
        data: {
          message: question,
          temperature: selModel?.temperature ?? 0.7,
          provider: selModel?.provider || undefined,
          model: selModel?.model || undefined,
          system_prompt: '你是建筑工程监理智能助手白泽，请基于用户提供的图片识别结果与文件内容，给出准确、可执行、简洁的结论与建议。注意：你只负责识别与建议，记录的入库由系统完成——禁止声称「已生成记录」「已保存」「已录入」，落库结果由系统另行提示。',
        },
        timeout: 120000,
      })
      // 识别命中隐患 → 按用户意图落库（用户没说要建档时先问一句，避免库里凭空多出记录）
      const recordLine = await this.maybeSaveHazardRecords(detections, note)
      this.addMessage({
        role: 'assistant',
        text: (result.answer || result.reply || '识别未返回内容') + recordLine,
      })
    } catch (error) {
      this.addMessage({ role: 'assistant', text: `识别失败：${(error as Error).message}` })
    } finally {
      this.setData({ loading: false })
    }
  },

  async createReviewPlanFromAttachments(note: string, images: Attachment[], watermarks: Record<string, string>[]) {
    try {
      if (!this.currentSessionId) this.currentSessionId = Date.now()
      const conversationId = conversationIdFor(this.currentSessionId)
      const evidence: ReviewPlan['evidence'] = []
      for (const [index, image] of images.entries()) {
        const uploaded = await uploadImage<{ id: number; url: string; watermark?: Record<string, string> }>(image.path, {
          source: 'agent_review_plan', trigger_detection: false,
        })
        const watermark = watermarks[index] && Object.keys(watermarks[index]).length
          ? watermarks[index]
          : uploaded.watermark || {}
        const takenAt = String(watermark.taken_at || '')
        const observedDate = this.watermarkDate(takenAt)
        const details = [watermark.location, watermark.section, takenAt].filter(Boolean).join(' · ')
        evidence.push({
          id: String(uploaded.id), mediaId: uploaded.id,
          image: absoluteMediaUrl(uploaded.url), observedDate,
          note: details || '未读取到水印信息',
        })
      }
      const dateGroups = new Map<string, string[]>()
      for (const item of evidence) {
        if (!item.observedDate) continue
        dateGroups.set(item.observedDate, [...(dateGroups.get(item.observedDate) || []), item.id])
      }
      const hasActionIntent = this.wantsActionIntent(note)
      const formByIntent: Array<[RegExp, string]> = [
        [/危大/, '危大工程巡视记录'], [/旁站/, '旁站记录'], [/平行检验/, '平行检验记录'],
        [/验收/, '验收记录'], [/巡视|巡查/, '巡视记录'],
      ]
      const suggestedForms = formByIntent.filter(([pattern]) => pattern.test(note)).map(([, form]) => form)
      const selectedForms = suggestedForms.length === 1 ? suggestedForms : []
      const events: ReviewPlan['events'] = hasActionIntent ? [...dateGroups.entries()].map(([date, evidenceIds], index) => ({
        id: `event-${index + 1}`, title: note.trim().slice(0, 80) || '现场工作事项（待核对）',
        location: '', date, dateConfirmed: false, evidenceIds, forms: selectedForms, content: note.trim(),
      })) : []
      const datedIds = new Set(events.flatMap(event => event.evidenceIds))
      const undatedIds = hasActionIntent ? evidence.filter(item => !datedIds.has(item.id)).map(item => item.id) : []
      if (note.trim() && undatedIds.length) events.push({
        id: `event-${events.length + 1}`, title: note.trim().slice(0, 80) || '现场工作事项（待核对）',
        location: '', date: '', dateConfirmed: false, evidenceIds: undatedIds, forms: selectedForms, content: note.trim(),
      })
      if (hasActionIntent && events.length === 0) events.push({
        id: 'event-1', title: note.trim().slice(0, 80) || '现场工作事项（待核对）',
        location: '', date: '', dateConfirmed: false, evidenceIds: evidence.map(item => item.id), forms: selectedForms, content: note.trim(),
      })
      const assigned = new Set(events.flatMap(event => event.evidenceIds))
      const plan: ReviewPlan = {
        schemaVersion: 1, scenario: 0, revision: 1, requestText: note.trim() || '用户仅上传图片，尚未说明整理用途',
        evidence, events, unassignedIds: evidence.filter(item => !assigned.has(item.id)).map(item => item.id),
        confirmedRevision: null,
      }
      const saved = await createReviewPlan(conversationId, plan)
      this.addMessage({
        role: 'assistant',
        text: `已整理 ${evidence.length} 张图片${dateGroups.size ? `，识别到 ${dateGroups.size} 个照片日期` : ''}。请在对话中核对日期并选择要生成的记录类型。确认前不会创建正式记录。`,
        reviewPlan: saved,
        reviewEvents: this.inlineReviewEvents(saved),
        reviewIssues: reviewIssues(saved),
      })
    } catch (error) {
      this.addMessage({ role: 'assistant', text: `整理计划创建失败：${(error as Error).message}。已上传的资料不会自动创建业务记录。` })
    } finally {
      this.setData({ loading: false })
    }
  },

  watermarkDate(value: string): string {
    const matched = String(value || '').match(/(\d{4})\s*[-/.年]\s*(\d{1,2})\s*[-/.月]\s*(\d{1,2})/)
    if (!matched) return ''
    const year = Number(matched[1]), month = Number(matched[2]), day = Number(matched[3])
    const date = new Date(Date.UTC(year, month - 1, day))
    if (date.getUTCFullYear() !== year || date.getUTCMonth() !== month - 1 || date.getUTCDate() !== day) return ''
    return `${matched[1]}-${matched[2].padStart(2, '0')}-${matched[3].padStart(2, '0')}`
  },

  inlineReviewEvents(plan: ReviewPlan) {
    return plan.events.map(event => ({
      id: event.id, title: event.title, date: event.date, dateConfirmed: event.dateConfirmed,
      dateOptions: [...new Set(plan.evidence.filter(item => event.evidenceIds.includes(item.id)).map(item => item.observedDate).filter(Boolean))],
      forms: event.forms, formsText: event.forms.join('、'),
      formOptions: INLINE_REVIEW_FORMS.map(label => ({ label, checked: event.forms.includes(label) })),
      evidenceCount: event.evidenceIds.length,
    }))
  },

  updateInlineReview(index: number, candidate: ReviewPlan) {
    const messages = [...this.data.messages]
    const message = { ...messages[index], reviewPlan: candidate, reviewEvents: this.inlineReviewEvents(candidate), reviewIssues: reviewIssues(candidate), reviewResult: '' }
    messages[index] = message
    this.setData({ messages })
    if (!candidate.serverId) return Promise.reject(new Error('整理计划尚未保存到服务端'))
    const key = String(candidate.serverId)
    const previous = this.inlinePlanSaveQueues[key] || Promise.resolve()
    const task = previous.catch(() => {}).then(async () => {
      const current = this.data.messages[index]?.reviewPlan
      if (!current?.serverId || (current.serverRevision || 0) >= current.revision) return
      const saved = await saveReviewPlan(current, current.serverRevision || current.revision - 1)
      const latestMessages = [...this.data.messages]
      const latest = latestMessages[index]
      if (!latest?.reviewPlan || latest.reviewPlan.serverId !== saved.serverId) return
      const latestPlan = latest.reviewPlan
      const plan = latestPlan.revision === current.revision
        ? saved
        : { ...latestPlan, serverRevision: saved.serverRevision }
      latestMessages[index] = { ...latest, reviewPlan: plan, reviewEvents: this.inlineReviewEvents(plan), reviewIssues: reviewIssues(plan) }
      this.setData({ messages: latestMessages })
    })
    this.inlinePlanSaveQueues[key] = task
    return task
  },

  changeInlineReviewDate(event: WechatMiniprogram.PickerChange) {
    const index = Number(event.currentTarget.dataset.index)
    const id = String(event.currentTarget.dataset.id)
    const plan = this.data.messages[index]?.reviewPlan
    if (!plan) return
    const candidate = editEvent(plan, id, { date: String(event.detail.value), dateConfirmed: true })
    void this.updateInlineReview(index, candidate).catch(error => wx.showToast({ title: `日期保存失败：${(error as Error).message}`, icon: 'none' }))
  },

  adoptInlineReviewDate(event: WechatMiniprogram.TouchEvent) {
    const index = Number(event.currentTarget.dataset.index)
    const id = String(event.currentTarget.dataset.id)
    const date = String(event.currentTarget.dataset.date)
    const plan = this.data.messages[index]?.reviewPlan
    if (!plan) return
    const candidate = editEvent(plan, id, { date, dateConfirmed: true })
    void this.updateInlineReview(index, candidate).catch(error => wx.showToast({ title: `日期保存失败：${(error as Error).message}`, icon: 'none' }))
  },

  toggleInlineReviewForm(event: WechatMiniprogram.TouchEvent) {
    const index = Number(event.currentTarget.dataset.index)
    const id = String(event.currentTarget.dataset.id)
    const form = String(event.currentTarget.dataset.form)
    const plan = this.data.messages[index]?.reviewPlan
    const target = plan?.events.find(item => item.id === id)
    if (!plan || !target) return
    const forms = target.forms.includes(form) ? target.forms.filter(value => value !== form) : [...target.forms, form]
    const candidate = editEvent(plan, id, { forms })
    void this.updateInlineReview(index, candidate).catch(error => wx.showToast({ title: `表式保存失败：${(error as Error).message}`, icon: 'none' }))
  },

  async confirmInlineReview(event: WechatMiniprogram.TouchEvent) {
    const index = Number(event.currentTarget.dataset.index)
    const message = this.data.messages[index]
    let plan = message?.reviewPlan
    if (!plan || message.reviewBusy) return
    const issues = reviewIssues(plan)
    if (issues.length) {
      wx.showToast({ title: issues[0], icon: 'none' })
      return
    }
    const messages = [...this.data.messages]
    messages[index] = { ...message, reviewBusy: true, reviewResult: '' }
    this.setData({ messages })
    try {
      const key = String(plan.serverId || '')
      await this.inlinePlanSaveQueues[key]
      plan = this.data.messages[index]?.reviewPlan || plan
      plan = confirmPlan(plan)
      const confirmed = await confirmReviewPlan(plan, plan.serverRevision || plan.revision)
      const savedMessages = [...this.data.messages]
      savedMessages[index] = { ...savedMessages[index], reviewPlan: confirmed, reviewEvents: this.inlineReviewEvents(confirmed) }
      this.setData({ messages: savedMessages })
      const executed = await executeReviewPlan(confirmed, confirmed.serverRevision || confirmed.revision)
      const finalMessages = [...this.data.messages]
      finalMessages[index] = { ...finalMessages[index], reviewPlan: executed, reviewEvents: this.inlineReviewEvents(executed), reviewResult: `已生成 ${executed.executionResult?.created_count || 0} 条记录。` }
      this.setData({ messages: finalMessages })
    } catch (error) {
      const latest = [...this.data.messages]
      latest[index] = { ...latest[index], reviewResult: `暂未生成：${(error as Error).message}` }
      this.setData({ messages: latest })
    } finally {
      const latest = [...this.data.messages]
      if (latest[index]) latest[index] = { ...latest[index], reviewBusy: false }
      this.setData({ messages: latest })
    }
  },

  /** 用户这句话是否要求建档：识别「只识别」与「识别并落库」两种意图 */
  wantsRecordIntent(note: string): boolean {
    return /(创建|建立|生成|保存|存档|建档|落库|入库|归档|录入|记录一下|存一下|记一下)/.test(note)
  },

  /** 识别结果是否命中隐患（与后端 detect 的判定口径保持一致） */
  isHazardHit(raw: any): boolean {
    return !!raw && (!!raw.has_hazard || raw.risk_status === '存在隐患')
  },

  /** 把已识别到隐患的图片落库成隐患记录，返回给用户看的结果文案 */
  async maybeSaveHazardRecords(
    detections: { path: string; raw: any }[],
    note: string,
  ): Promise<string> {
    const hits = detections.filter(d => this.isHazardHit(d.raw))
    if (!hits.length) return ''

    let shouldSave = this.wantsRecordIntent(note)
    if (!shouldSave) {
      const confirm = await wx.showModal({
        title: `识别到 ${hits.length} 处隐患`,
        content: '是否立即生成隐患记录？生成后可在「工作记录 → 隐患」查看、派单并生成整改文书。',
        confirmText: '生成记录',
        confirmColor: '#13795b',
      })
      shouldSave = !!confirm.confirm
    }
    if (!shouldSave) {
      return `\n\n（本次仅识别，未生成记录。需要建档时重新发送图片并注明「创建隐患记录」即可。）`
    }

    const saved = await this.commitDetections(hits)
    if (!saved.ids.length) {
      return `\n\n⚠️ 隐患记录落库失败${saved.lastError ? `：${saved.lastError}` : ''}，请到「隐患识别」页重拍一次。`
    }
    return (
      `\n\n✅ 已生成 ${saved.ids.length} 条隐患记录（${saved.ids.map(id => '#' + id).join('、')}）` +
      (saved.failed ? `，另有 ${saved.failed} 条落库失败` : '') +
      '。可在「工作记录 → 隐患」查看、派单或生成整改文书。'
    )
  },

  /** 落库已识别结果（/hazard/commit-detection，不重复调用视觉模型） */
  async commitDetections(
    hits: { path: string; raw: any }[],
  ): Promise<{ ids: number[]; failed: number; lastError: string }> {
    const token = session.getToken()
    const ids: number[] = []
    let failed = 0
    let lastError = ''
    for (const hit of hits) {
      try {
        const res = await new Promise<any>((resolve, reject) => {
          wx.uploadFile({
            url: `${config.apiBaseUrl}/hazard/commit-detection`,
            filePath: hit.path,
            name: 'image',
            formData: { detection: JSON.stringify(hit.raw) },
            header: { Authorization: `Bearer ${token}` },
            success: (r) => {
              if (r.statusCode >= 200 && r.statusCode < 300) {
                try {
                  resolve(JSON.parse(r.data))
                } catch {
                  reject(new Error('落库返回格式异常'))
                }
              } else {
                reject(new Error(`落库失败（HTTP ${r.statusCode}）`))
              }
            },
            fail: (err) => reject(new Error(err.errMsg || '上传失败')),
          })
        })
        if (res?.hazard_id) ids.push(res.hazard_id)
      } catch (error) {
        failed += 1
        lastError = (error as Error).message
      }
    }
    if (ids.length) invalidateCache(CACHE_KEYS.HAZARD_LIST)
    return { ids, failed, lastError }
  },

  /** 单张图片视觉识别，返回可读文本 + 原始识别结果（原始结果供落库复用） */
  async detectImageOnce(imagePath: string): Promise<{ text: string; raw: any }> {
    const token = session.getToken()
    const result = await new Promise<any>((resolve, reject) => {
      wx.uploadFile({
        url: `${config.apiBaseUrl}/hazard/detect`,
        filePath: imagePath,
        name: 'image',
        formData: { prompt: '请识别这张图片中的安全隐患与现场情况' },
        header: { Authorization: `Bearer ${token}` },
        success: (res) => {
          if (res.statusCode >= 200 && res.statusCode < 300) {
            try {
              resolve(JSON.parse(res.data))
            } catch {
              reject(new Error('识别返回格式异常'))
            }
          } else {
            reject(new Error(friendlyServerError(res.statusCode)))
          }
        },
        fail: (err) => reject(new Error(err.errMsg || '上传失败')),
      })
    })

    if (result.has_hazard || result.risk_status === '存在隐患') {
      let text = `风险状态：${result.risk_status || '存在隐患'}\n风险类别：${result.risk_category || '未分类'}\n主要风险：${result.top1_risk || '未知'}`
      if (result.evidence?.length) text += `\n证据：${result.evidence.join('、')}`
      if (result.recheck_suggestion?.length) text += `\n复查建议：${result.recheck_suggestion.join('、')}`
      return { text, raw: result }
    }
    return { text: '未识别到明显安全隐患。', raw: result }
  },

  /** 读图片水印（拍摄时间/地点/天气等）；失败静默返回空对象，不影响主流程 */
  async readWatermark(imagePath: string): Promise<Record<string, string>> {
    const token = session.getToken()
    try {
      const result = await new Promise<any>((resolve, reject) => {
        wx.uploadFile({
          url: `${config.apiBaseUrl}/media/watermark/recognize`,
          filePath: imagePath,
          name: 'file',
          header: { Authorization: `Bearer ${token}` },
          success: (res) => {
            if (res.statusCode >= 200 && res.statusCode < 300) {
              try {
                resolve(JSON.parse(res.data))
              } catch {
                reject(new Error('水印返回格式异常'))
              }
            } else {
              reject(new Error(`水印识别失败（HTTP ${res.statusCode}）`))
            }
          },
          fail: (err) => reject(new Error(err.errMsg || '上传失败')),
        })
      })
      const fields = (result && result.fields) || {}
      const out: Record<string, string> = {}
      for (const key of Object.keys(fields)) {
        const value = String(fields[key] || '').trim()
        if (value) out[key] = value
      }
      return out
    } catch {
      return {}
    }
  },

  /** 水印字段 → 一行可读文案；没有识别到就返回空串 */
  formatWatermark(fields: Record<string, string>): string {
    const labels: [string, string][] = [
      ['taken_at', '拍摄时间'], ['location', '地点'], ['project', '项目'],
      ['section', '部位'], ['weather', '天气'], ['person', '人员'],
    ]
    const parts: string[] = []
    for (const [key, label] of labels) {
      if (fields[key]) parts.push(`${label}：${fields[key]}`)
    }
    return parts.length ? `【水印】${parts.join('；')}` : ''
  },

  /** 水印字段 → Agent 建记录参数（只填用户没说、水印能确定的） */
  watermarkPrefill(fields: Record<string, string>): Record<string, string> {
    const out: Record<string, string> = {}
    const takenAt = fields.taken_at || ''
    // 原样带上拍摄时间：服务端按记录类型换算成对应的时间参数
    // （巡视 inspection_start_time / 旁站 station_start_time），前端不猜字段名
    if (takenAt) out.taken_at = takenAt
    const matched = takenAt.match(/(\d{4})\s*[-/年]\s*(\d{1,2})\s*[-/月]\s*(\d{1,2})/)
    if (matched) {
      out.record_date = `${matched[1]}-${matched[2].padStart(2, '0')}-${matched[3].padStart(2, '0')}`
    }
    if (fields.location) out.location = fields.location
    if (fields.section) out.section = fields.section
    if (fields.weather) out.weather = fields.weather
    return out
  },

  /** 是否「办事」（记录/建档/生成）——决定带图消息走 Agent 还是纯问答 */
  wantsActionIntent(note: string): boolean {
    return /(记录|记一下|记一条|填写|填表|建档|建单|新建|创建|生成|存档|落库|入库|录入|归档|整理)/.test(note)
  },

  /** 带素材走 Agent 工具路由：办事类消息只有走这里才会真的落库 */
  async runAgentWithMaterial(
    text: string,
    material: string,
    prefill: Record<string, string>,
    messages: Message[],
    wmFields: Record<string, string>,
    hasImage = false,
    imagePaths: string[] = [],
  ) {
    const selModel = this.data.models.find(m => m.id === this.data.currentModel)
    const history = messages
      .slice(0, -1)
      .filter(m => m.role === 'user' || m.role === 'assistant')
      .slice(-6)
      .map(m => ({ role: m.role, text: m.text }))

    try {
      const result = await request<AgentChatResponse>('/agent/chat', {
        method: 'POST',
        data: {
          message: text,
          conversation_id: conversationIdFor(this.currentSessionId),
          material: material || undefined,
          prefill,
          history,
          mode: this.data.mode,
          provider: selModel?.provider || undefined,
          model: selModel?.model || undefined,
        },
        timeout: 120000,
      })
      // 让用户看得见「水印确实读了」，否则会怀疑系统没看图
      const wmText = this.formatWatermark(wmFields)
      const reply = String(result.reply || '')
      if (wmText) {
        // 服务端已回执「已从照片水印自动填入…」时不重复列一遍
        if (!reply.includes('自动填入')) {
          result.reply = [wmText, reply].filter(Boolean).join('\n')
        }
      } else if (hasImage) {
        // 普通记录场景只做水印读取；没有水印不等于用户没有提供事实，
        // 也不能把未调用隐患模型说成“图片识别结果”。
        result.reply = [
          '【水印】这张照片没读到水印文字（水印要清晰、别被裁掉）。图片已作为附件保留，不影响你在文字中提供的日期、地点和工作内容。',
          reply,
        ].filter(Boolean).join('\n')
      }
      this.applyAgentReply(result, messages)
      // 上传图片且 Agent 建的是工作记录 → 自动把这张照片挂到该记录
      // （复用 /media/images business_type=work_record 链路，后端会自动同步 inspection_photos/witness_photos/content_json.media_ids）
      // 单独 try 包裹：挂图失败绝不能冲掉已建好的记录卡片（落到外层 catch 会用原始 messages 覆盖）。
      if (imagePaths.length) {
        try {
          const recCard =
            (result.cards && result.cards.find(c => c.kind === 'work_record' && c.id)) ||
            (result.card && result.card.kind === 'work_record' ? result.card : undefined)
          if (recCard && recCard.id) {
            const { ok, failed, lastError } = await this.uploadPhotosToRecord(recCard.id, imagePaths)
            if (ok) {
              this.addMessage({
                role: 'assistant',
                text: `📎 已自动把这 ${ok} 张照片挂到记录 #${recCard.id}` +
                  (failed ? `（另有 ${failed} 张失败${lastError ? `：${lastError}` : ''}）` : '') + '。',
              })
            } else if (failed) {
              this.addMessage({
                role: 'assistant',
                text: `⚠️ 照片自动挂记录失败${lastError ? `：${lastError}` : ''}，可点记录卡「补充照片」手动添加。`,
              })
            }
          }
        } catch (attachErr) {
          console.warn('自动挂照片失败（不影响已建记录）：', attachErr)
        }
      }
    } catch (error) {
      this.setData({
        messages: [...messages, { role: 'assistant', text: `调用失败：${(error as Error).message}` }],
      })
    } finally {
      this.setData({ loading: false })
      void this.loadPendingDrafts()
    }
  },

  /** 把 /agent/chat 回执渲染成一条助手消息（卡片/动作/chips/缺项提示） */
  applyAgentReply(result: AgentChatResponse, messages: Message[]) {
    const cards: AgentCard[] = result.cards && result.cards.length
      ? result.cards
      : (result.card ? [result.card] : [])
    const actions: { label: string; command: string }[] = (result.actions || [])
      .filter(a => a.url)
      .map(a => ({ label: a.label, command: `navigate:${a.url}` }))
    if (result.undo) {
      actions.push({ label: '撤销', command: `agent-undo:${result.undo.entity}:${result.undo.id}` })
    }
    // 工作记录卡：附「补充照片」（落库挂到该记录，见 /media/images business_type=work_record）与「生成文书」
    for (const cd of cards) {
      if (cd.kind === 'work_record' && cd.id) {
        actions.push({ label: '补充照片', command: `attach-photo:${cd.id}` })
        actions.push({ label: '生成 PDF/Word', command: `gen-doc:${cd.id}` })
      }
    }
    // 追问候选值 → 快速选项 chips（点击即作为回答发送）
    const chips: string[] = []
    if (result.need_more && result.need_more.length && result.options) {
      for (const key of Object.keys(result.options)) {
        for (const opt of result.options[key] || []) {
          if (!chips.includes(opt)) chips.push(opt)
        }
      }
    }
    const missing = result.need_more && result.need_more.length
      ? `还需要补充：${result.need_more.join('、')}`
      : undefined
    this.setData({
      messages: [...messages, {
        role: 'assistant',
        text: result.reply || '智能体未返回内容',
        cards,
        actions,
        chips: chips.length ? chips : undefined,
        hint: missing,
      }],
    })
  },

  async handleCommand(route: CommandRoute, userMsg: Message) {
    const breadcrumb = [route.level1, route.level2, route.level3].filter(Boolean) as string[]

    if (route.reply === '已返回主页') {
      this.setData({
        breadcrumb: [],
        hazardMode: false,
        messages: [...this.data.messages, userMsg, { role: 'assistant', text: route.reply }],
      })
      this.currentSessionId = 0
      this.initMessages()
      return
    }

    this.setData({
      breadcrumb,
      messages: [...this.data.messages, userMsg, { role: 'assistant', text: route.reply }],
    })

    if (route.localAction === 'hazard-recognition') {
      this.setData({ hazardMode: true, mode: 'hazard' })
    } else if (route.localAction === 'daily-log') {
      this.setData({ showLogDialog: true })
    } else if (route.localAction === 'knowledge-query') {
      // 2026-10-09：不再弹「选择知识范围」，直接提示输入问题（范围固定走当前 knowledgeScope）
      this.setData({
        messages: [...this.data.messages, { role: 'assistant', text: '请输入要查询的规范或资料问题：' }],
      })
    } else     if (route.navigate) {
      setTimeout(() => { wx.navigateTo({ url: route.navigate! }) }, 800)
    }
  },

  async handleKnowledgeQuery(text: string, userMsg: Message) {
    this.setData({ loading: true, messages: [...this.data.messages, userMsg] })
    try {
      const result = await request<{ answer?: string; context_results?: Array<{ title: string; source_file: string; snippet: string }> }>('/knowledge/llm-query', {
        method: 'POST',
        data: {
          question: text,
          use_llm: true,
          knowledge_scope: this.data.knowledgeScope,
        },
        timeout: 120000,
      })
      let answer = result.answer || ''
      if (!answer && result.context_results?.length) {
        answer = result.context_results.slice(0, 5).map((r, i) =>
          `${i + 1}. ${r.title || r.source_file}\n   ${r.snippet || ''}`
        ).join('\n')
      }
      this.setData({
        messages: [...this.data.messages, { role: 'assistant', text: answer || '未找到相关知识' }],
      })
    } catch (error) {
      this.setData({
        messages: [...this.data.messages, { role: 'assistant', text: `查询失败：${(error as Error).message}` }],
      })
    } finally {
      this.setData({ loading: false })
    }
  },

  /**
   * 聊天主链路：统一走 /agent/chat（服务端意图路由 + 执行 + 回执）。
   *
   * 服务端行为：
   * - 识别到工具 → 真正落库/查询，回 kind=action（带卡片、导航按钮、可撤销）
   * - 只是问知识 → 回 kind=chat（纯文本）
   * - 模型不支持 JSON 模式 → 服务端自动退化为纯问答（degraded=true）
   * 因此前端无需再按 mode 分两套提示词，隐患识别只是 mode 提示。
   */
  async handleLlmChat(text: string, userMsg: Message) {
    const messages = [...this.data.messages, userMsg] as Message[]
    this.setData({ messages, loading: true })

    const selModel = this.data.models.find(m => m.id === this.data.currentModel)
    // 最近 6 轮纯文本作为上下文（用于「把这条生成日志」这类指代消解）
    const history = messages
      .filter(m => m.role === 'user' || m.role === 'assistant')
      .slice(-7, -1)
      .map(m => ({ role: m.role, text: m.text }))

    try {
      const result = await request<AgentChatResponse>('/agent/chat', {
        method: 'POST',
        data: {
          message: text,
          conversation_id: conversationIdFor(this.currentSessionId),
          history,
          mode: this.data.mode,
          provider: selModel?.provider || undefined,
          model: selModel?.model || undefined,
        },
        timeout: 120000,
      })
      this.applyAgentReply(result, messages)
    } catch (error) {
      this.setData({
        messages: [...messages, { role: 'assistant', text: `调用失败：${(error as Error).message}` }],
      })
    } finally {
      this.setData({ loading: false })
      void this.loadPendingDrafts()
    }
  },

  /** 跳转系统页面：一级页（底栏 4 页 + 工作台）用 redirectTo 保持页面栈语义 */
  openAgentPage(url: string) {
    if (!url) return
    const TAB_PAGES = [
      '/pages/agent/index', '/pages/work/index', '/pages/hazards/index',
      '/pages/kb/index', '/pages/mine/index',
    ]
    const isTab = TAB_PAGES.some(p => url.startsWith(p))
    if (isTab) {
      wx.redirectTo({ url })
      return
    }
    wx.navigateTo({ url })
  },

  /** 撤销聊天里刚创建的对象（卡片上的「撤销」按钮） */
  async undoTabCreated(entity: string, id: number) {
    if (!entity || !id) return
    wx.showLoading({ title: '撤销中...', mask: true })
    try {
      const res = await request<{ ok: boolean; reply: string }>('/agent/undo', {
        method: 'POST',
        data: { entity, id },
        timeout: 30000,
      })
      this.addMessage({ role: 'assistant', text: res.reply || '已撤销' })
    } catch (error) {
      this.addMessage({ role: 'assistant', text: `撤销失败：${(error as Error).message}` })
    } finally {
      wx.hideLoading()
    }
  },

  /** 点击回执卡：跳到对应列表/详情页 */
  openAgentCard(event: WechatMiniprogram.TouchEvent) {
    const kind = String(event.currentTarget.dataset.kind || '')
    const id = Number(event.currentTarget.dataset.id || 0)
    if (kind === 'work_record') {
      wx.navigateTo({ url: `/pages/work/index?focus=${id}` })
    } else if (kind === 'hazard') {
      wx.navigateTo({ url: `/pages/hazards/index?focus=${id}` })
    } else if (kind === 'document') {
      wx.navigateTo({ url: `/pages/documents/detail?id=${id}` })
    }
  },

  /** 把给定本地照片路径逐张上传挂到某工作记录（复用 /media/images work_record 链路） */
  async uploadPhotosToRecord(recordId: number, paths: string[]): Promise<{ ok: number; failed: number; lastError: string }> {
    let ok = 0
    let failed = 0
    let lastError = ''
    for (const path of paths) {
      try {
        await uploadImage<{ id?: number }>(path, {
          business_type: 'work_record',
          business_id: recordId,
          source: 'miniprogram',
        })
        ok += 1
      } catch (error) {
        failed += 1
        lastError = (error as Error).message
      }
    }
    if (ok) {
      invalidateByPrefix('/work-record')
      invalidateCache(CACHE_KEYS.WORK_RECORD_STATS)
    }
    return { ok, failed, lastError }
  },

  /** 工作记录卡「补充照片」：选图（相册/拍摄，最多 9 张）→ 逐张上传挂到该记录 */
  attachPhotoToRecord(recordId: number) {
    wx.chooseMedia({
      count: 9,
      mediaType: ['image'],
      sourceType: ['album', 'camera'],
      sizeType: ['compressed'],
      success: async (res) => {
        const paths = (res.tempFiles || []).map(f => f.tempFilePath).filter(Boolean)
        if (!paths.length) return
        this.addMessage({ role: 'user', images: paths, text: `为记录 #${recordId} 补充 ${paths.length} 张照片` })
        wx.showLoading({ title: '上传中...', mask: true })
        const { ok, failed, lastError } = await this.uploadPhotosToRecord(recordId, paths)
        wx.hideLoading()
        this.addMessage({
          role: 'assistant',
          text: ok
            ? `✅ 已为记录 #${recordId} 补充 ${ok} 张照片${failed ? `，另有 ${failed} 张失败${lastError ? `：${lastError}` : ''}` : ''}。`
            : `照片上传失败${lastError ? `：${lastError}` : ''}，请重试。`,
        })
      },
    })
  },

  /** 工作记录卡「生成文书」：/form-docs/generate → 自动归档到侧边栏「工作文书」对应分类 */
  async generateDocFromRecord(recordId: number) {
    if (this.data.loading) return
    wx.showLoading({ title: '生成文书...', mask: true })
    try {
      const res = await request<{ doc_id: number; title: string }>('/form-docs/generate', {
        method: 'POST',
        data: { record_id: recordId },
        timeout: 60000,
      })
      this.addMessage({
        role: 'assistant',
        text: `✅ 已生成文书《${res.title}》，自动归档到侧边栏「工作文书」对应分类；可在文书管理中查看、签认并导出 PDF / Word。`,
        actions: [{ label: '查看文书', command: `navigate:/pages/documents/detail?id=${res.doc_id}` }],
      })
    } catch (error) {
      this.addMessage({ role: 'assistant', text: `生成文书失败：${(error as Error).message}` })
    } finally {
      wx.hideLoading()
    }
  },

  /** 导入微信聊天/本地文档（pdf/docx/txt），提取内容后由 AI 生成对应工作记录 */
  importFile() {
    if (!this.data.projectCreated) {
      wx.showToast({ title: '请先创建项目', icon: 'none' })
      return
    }
    this.setData({ showPlusPanel: false })
    wx.chooseMessageFile({
      count: 1,
      type: 'file',
      extension: ['pdf', 'docx', 'doc', 'txt', 'md'],
      success: async (res) => {
        const file = res.tempFiles && res.tempFiles[0]
        if (!file) return
        wx.showLoading({ title: '解析文件中...', mask: true })
        try {
          const parsed = await uploadDocument<{ filename?: string; text?: string }>(file.path)
          wx.hideLoading()
          const text = (parsed.text || '').trim()
          if (!text) {
            wx.showToast({ title: '未能从文件提取到文本', icon: 'none' })
            return
          }
          this.generateRecordFromFile(parsed.filename || file.name || '导入文件', text)
        } catch (error) {
          wx.hideLoading()
          wx.showToast({ title: `解析失败：${(error as Error).message}`, icon: 'none' })
        }
      },
    })
  },

  async generateRecordFromFile(filename: string, content: string) {
    const brief = content.slice(0, 3000)
    const userMsg: Message = { role: 'user', text: `导入文件《${filename}》，请根据文件内容生成对应的工作记录` }
    const messages = [...this.data.messages, userMsg] as Message[]
    this.setData({ messages, loading: true })
    try {
      // 走智能体：让服务端真正落库一条工作记录（而不是只回一段文字）
      const result = await request<AgentChatResponse>('/agent/chat', {
        method: 'POST',
        data: {
          message: `请根据下面这份导入文件的内容，创建一条规范的工作记录（自动判断巡视/旁站/验收/见证）并回执给我。\n\n文件《${filename}》内容：\n${brief}`,
          conversation_id: conversationIdFor(this.currentSessionId),
          history: [],
          mode: this.data.mode,
        },
        timeout: 120000,
      })
      const cards: AgentCard[] = result.cards && result.cards.length
        ? result.cards
        : (result.card ? [result.card] : [])
      const actions: { label: string; command: string }[] = (result.actions || [])
        .filter(a => a.url)
        .map(a => ({ label: a.label, command: `navigate:${a.url}` }))
      if (result.undo) {
        actions.push({ label: '撤销', command: `agent-undo:${result.undo.entity}:${result.undo.id}` })
      }
      // 工作记录卡：附「生成文书」（渲染省统表并归档到工作文书对应分类）
      for (const cd of cards) {
        if (cd.kind === 'work_record' && cd.id) {
          actions.push({ label: '生成 PDF/Word', command: `gen-doc:${cd.id}` })
        }
      }
      this.setData({
        messages: [...messages, {
          role: 'assistant',
          text: result.reply || '生成失败：智能体未返回内容',
          cards,
          actions,
          hint: result.need_more && result.need_more.length
            ? `还需要补充：${result.need_more.join('、')}`
            : undefined,
        }],
      })
    } catch (error) {
      this.setData({
        messages: [...messages, { role: 'assistant', text: `生成失败：${(error as Error).message}` }],
      })
    } finally {
      this.setData({ loading: false })
      void this.loadPendingDrafts()
    }
  },

  /** 空状态四个建议按钮：一律在当前聊天框内完成，不再跳转到其它页面 */
  handleActionTap(event: WechatMiniprogram.TouchEvent) {
    const command = event.currentTarget.dataset.command as string
    if (command === 'show-form') {
      this.setData({ input: '' })
      return
    }
    // 智能体回执：导航到系统页面 / 撤销刚创建的对象
    if (command.startsWith('navigate:')) {
      this.openAgentPage(command.slice('navigate:'.length))
      return
    }
    if (command.startsWith('agent-undo:')) {
      const [, entity, id] = command.split(':')
      void this.undoTabCreated(entity, Number(id))
      return
    }
    // 智能体追问的快速候选值：直连 agent 链路（不经 matchCommand，避免「巡视」被当成跳页指令）
    if (command.startsWith('agent-chip:')) {
      const value = command.slice('agent-chip:'.length)
      if (!value || this.data.loading) return
      this.handleLlmChat(value, { role: 'user', text: value })
      return
    }
    // 工作记录卡「补充照片」：选图 → /media/images 挂到该记录
    if (command.startsWith('attach-photo:')) {
      const recordId = Number(command.slice('attach-photo:'.length))
      if (recordId) void this.attachPhotoToRecord(recordId)
      return
    }
    // 工作记录卡「生成文书」：渲染省统表文书，自动归档到侧边栏「工作文书」对应分类
    if (command.startsWith('gen-doc:')) {
      const recordId = Number(command.slice('gen-doc:'.length))
      if (recordId) void this.generateDocFromRecord(recordId)
      return
    }
    // 工作记录：点选字段候选值 / 选择自己填写 / 补拍照片
    if (command.startsWith('work-field-manual:')) {
      const field = command.split(':')[1]
      const label = this.data.workMissing.find(m => m.field === field)?.label || field
      this.setData({ input: '', canSend: false })
      this.addMessage({ role: 'assistant', text: `请在下方输入框填写「${label}」，或按住麦克风说一句，我会自动带上。` })
      return
    }
    if (command.startsWith('work-field:')) {
      this.fillWorkField(command)
      return
    }
    if (command === 'work-photo-now') {
      this.captureWorkPhoto('正在生成工作记录…', true)
      return
    }
    if (command === 'work-photo-skip') {
      void this.autoCreateWorkSession()
      return
    }
    switch (command) {
      case '工作记录':
        this.startVoiceWorkFromChat()
        return
      case '拍照':
        this.captureForRecognize()
        return
      case '知识库':
        this.askScopeFromChat()
        return
      case '生成监理日志':
        void this.generateTodayLogInChat()
        return
      default:
        this.setData({ input: command })
        this.send()
    }
  },

  /** 点选工作记录字段候选值 → 作为补充信息继续提取 */
  fillWorkField(command: string) {
    const [, field, ...rest] = command.split(':')
    const value = rest.join(':')
    const label = this.data.workMissing.find(m => m.field === field)?.label || field
    this.addMessage({ role: 'user', text: `${label}：${value}` })
    this.setData({
      voiceSessionTexts: [...this.data.voiceSessionTexts, `${label}${value}`],
      workMissing: this.data.workMissing.filter(m => m.field !== field),
    })
    if (this.voiceAutoTimer !== null) {
      clearTimeout(this.voiceAutoTimer)
      this.voiceAutoTimer = null
    }
    void this.autoCreateWorkSession()
  },

  /** 空状态① 语音记录现场工作：在当前页进入录音模式，结果直接回到聊天框
   *  2026-10-09：不再弹「先拍照/先说话」选择，直接进入说话流程（结束后会询问是否补照片） */
  startVoiceWorkFromChat() {
    if (!this.data.projectCreated) {
      wx.showToast({ title: '请先创建项目', icon: 'none' })
      return
    }
    this.setData({
      voiceWorkMode: true,
      voiceSessionActive: false,
      voiceWorkPending: false,
      voiceWorkAccumulated: '',
      voiceSessionTexts: [],
      voiceWorkPhoto: '',
      workPhotoAsked: false,
      workMissing: [],
      showPlusPanel: false,
    })
    this.addMessage({
      role: 'assistant',
      text: '请点击下方麦克风说出工作内容，例如「3号楼二层钢筋绑扎，天气晴，一切正常」。',
    })
  },

  /** 拍现场照片（用于语音创建工作记录） */
  captureWorkPhoto(hint: string, autoCreate = false) {
    wx.chooseMedia({
      count: 1,
      mediaType: ['image'],
      sourceType: ['camera'],
      camera: 'back',
      success: (res) => {
        const path = res.tempFiles?.[0]?.tempFilePath || ''
        this.setData({ voiceWorkPhoto: path, workPhotoAsked: true })
        this.addMessage({ role: 'assistant', text: `📸 现场照片已就位。${hint}` })
        if (autoCreate && path) void this.autoCreateWorkSession()
      },
      fail: () => {
        this.setData({ workPhotoAsked: true })
        this.addMessage({ role: 'assistant', text: `未拍照。${hint}` })
        if (autoCreate) void this.autoCreateWorkSession()
      },
    })
  },

  /** 语音识别完成后：还没拍照就先问一句是否补齐现场照片 */
  askWorkPhoto(): boolean {
    if (this.data.voiceWorkPhoto || this.data.workPhotoAsked) return false
    this.setData({ workPhotoAsked: true })
    this.addMessage({
      role: 'assistant',
      text: '是否需要补齐现场照片？',
      actions: [
        { label: '现在补拍', command: 'work-photo-now' },
        { label: '暂不补', command: 'work-photo-skip' },
      ],
    })
    return true
  },

  /** 本地时间 HH:MM —— 供记录时间自动生成使用 */
  currentHm(): string {
    const d = new Date()
    return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
  },

  /** 空状态② 拍照识别：选完图仍在聊天框内识别 */
  captureForRecognize() {
    if (!this.data.projectCreated) {
      wx.showToast({ title: '请先创建项目', icon: 'none' })
      return
    }
    this.setData({ hazardMode: true, mode: 'hazard' })
    wx.showActionSheet({
      itemList: ['拍照', '从相册选择'],
      success: (res) => this.pickImages(res.tapIndex === 0 ? 'camera' : 'album'),
    })
  },

  /** 空状态③ 查资料库：不再弹范围选择，直接提示输入问题（2026-10-09） */
  askScopeFromChat() {
    this.setData({
      messages: [...this.data.messages, { role: 'assistant', text: '请输入要查询的规范或资料问题，我来为你检索资料库：' }],
    })
  },

  /** 空状态④ 生成今日监理日志：结果直接回到聊天框 */
  async generateTodayLogInChat() {
    if (!this.data.projectCreated) {
      wx.showToast({ title: '请先创建项目', icon: 'none' })
      return
    }
    const now = new Date()
    const date = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`
    this.addMessage({ role: 'assistant', text: `正在生成 ${date} 的监理日志…` })
    this.setData({ loading: true, logDate: date })
    try {
      const result = await request<{ doc_id: number; work_count: number; hazard_count: number; summary?: { work_records?: number; hazards?: number } }>(
        `/document/generate-daily-log?date=${date}`,
        { method: 'POST', data: { weather: this.data.logWeather || '', temp_low: '', temp_high: '' }, timeout: 60000 },
      )
      // 后端计数在 summary 里（work_records/hazards），work_count/hazard_count 为顶层别名兜底
      const workCount = result.summary?.work_records ?? result.work_count ?? 0
      const hazardCount = result.summary?.hazards ?? result.hazard_count ?? 0
      this.addMessage({
        role: 'assistant',
        text: `监理日志已生成（文书ID: ${result.doc_id}），包含 ${workCount} 条工作记录、${hazardCount} 条隐患记录。可在「工作记录 → 材料台账」查看与导出。`,
      })
    } catch (error) {
      this.addMessage({ role: 'assistant', text: `生成失败：${(error as Error).message}` })
    } finally {
      this.setData({ loading: false })
    }
  },

  // 「+」功能面板
  togglePlusPanel() {
    this.setData({ showPlusPanel: !this.data.showPlusPanel })
  },

  openDailyLog() {
    this.setData({ showPlusPanel: false, showLogDialog: true })
  },

  /** 工作记录：与底栏「工作记录」统一指向记录列表页（原先走 capture + gdjl_work_intent 意图，已废弃）。
   *  work 是底栏一级页（内部挂 tab-bar，切换用 redirectTo），这里保持同样的切换语义，避免页面栈里压出重复页。 */
  openWork() {
    this.setData({ showHistory: false, showPlusPanel: false })
    wx.redirectTo({ url: '/pages/work/index' })
  },

  // 相册多选 / 拍照单张：先加入附件区，可继续补充文字，再一次性发送识别
  selectImage(event?: WechatMiniprogram.TouchEvent) {
    const source = String(event?.currentTarget?.dataset?.source || '')
    this.pickImages(source === 'camera' ? 'camera' : 'album')
  },

  pickImages(source: 'album' | 'camera') {
    if (!this.data.projectCreated) {
      wx.showToast({ title: '请先创建项目', icon: 'none' })
      return
    }
    const isCamera = source === 'camera'
    this.setData({ showPlusPanel: false })
    wx.chooseMedia({
      count: isCamera ? 1 : 9,
      mediaType: ['image'],
      sourceType: isCamera ? ['camera'] : ['album', 'camera'],
      camera: 'back',
      success: (res) => {
        const picked: Attachment[] = (res.tempFiles || []).map((f, i) => ({
          id: `img-${Date.now()}-${i}`,
          kind: 'image',
          path: f.tempFilePath,
          name: isCamera ? `拍照 ${i + 1}` : `图片 ${i + 1}`,
          size: (f as unknown as { size?: number }).size,
        }))
        this.appendAttachments(picked)
      },
      fail: () => { /* 用户取消 */ },
    })
  },

  /** 「文件」：从微信聊天记录中选文档（pdf/doc/docx/xls/xlsx/txt/md） */
  pickLocalFile() {
    if (!this.data.projectCreated) {
      wx.showToast({ title: '请先创建项目', icon: 'none' })
      return
    }
    this.setData({ showPlusPanel: false })
    wx.chooseMessageFile({
      count: 5,
      type: 'file',
      extension: ['pdf', 'doc', 'docx', 'xls', 'xlsx', 'txt', 'md', 'png', 'jpg', 'jpeg'],
      success: (res) => {
        this.appendAttachments((res.tempFiles || []).map(f => this.toFileAttachment(f)))
      },
      fail: () => { /* 用户取消 */ },
    })
  },

  /** 「微信文件」：从微信聊天记录中选任意类型（文档 / 图片 / 视频） */
  pickWechatFile() {
    if (!this.data.projectCreated) {
      wx.showToast({ title: '请先创建项目', icon: 'none' })
      return
    }
    this.setData({ showPlusPanel: false })
    wx.chooseMessageFile({
      count: 9,
      type: 'all',
      success: (res) => {
        this.appendAttachments((res.tempFiles || []).map(f => this.toFileAttachment(f)))
      },
      fail: () => { /* 用户取消 */ },
    })
  },

  /** 微信文件 → 附件：图片类走视觉识别，其余走文档解析 */
  toFileAttachment(file: { path: string; name?: string; size?: number; type?: string }): Attachment {
    const name = file.name || file.path.split('/').pop() || '微信文件'
    const ext = (name.split('.').pop() || '').toLowerCase()
    const isImage = /^(png|jpe?g|webp|bmp|gif)$/.test(ext) || file.type === 'image'
    return {
      id: `file-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
      kind: isImage ? 'image' : 'file',
      path: file.path,
      name,
      ext: (ext || 'file').toUpperCase(),
      size: file.size,
    }
  },

  appendAttachments(items: Attachment[]) {
    if (!items.length) return
    const attachments = [...this.data.attachments, ...items].slice(0, 9)
    this.setData({ attachments, canSend: Boolean(this.data.input.trim()) || attachments.length > 0, showPlusPanel: false })
  },

  removeAttachment(event: WechatMiniprogram.TouchEvent) {
    const index = Number(event.currentTarget.dataset.index)
    const attachments = this.data.attachments.filter((_, i) => i !== index)
    this.setData({ attachments, canSend: Boolean(this.data.input.trim()) || attachments.length > 0 })
  },

  previewAttachment(event: WechatMiniprogram.TouchEvent) {
    const index = Number(event.currentTarget.dataset.index)
    const item = this.data.attachments[index]
    if (!item || item.kind !== 'image') return
    const urls = this.data.attachments.filter(a => a.kind === 'image').map(a => a.path)
    wx.previewImage({ current: item.path, urls })
  },

  /** 聊天记录里的图片点开预览 */
  previewImageUrl(event: WechatMiniprogram.TouchEvent) {
    const url = String(event.currentTarget.dataset.url || '')
    if (!url) return
    wx.previewImage({ current: url, urls: [url] })
  },

  async sendImage() {
    if (!this.data.pendingImage) return
    const imagePath = this.data.pendingImage
    const userMsg: Message = { role: 'user', text: '请识别这张图片中的隐患', image: imagePath }
    this.setData({ pendingImage: '', messages: [...this.data.messages, userMsg], loading: true })

    try {
      const token = session.getToken()
      const result = await new Promise<any>((resolve, reject) => {
        wx.uploadFile({
          url: `${config.apiBaseUrl}/hazard/detect`,
          filePath: imagePath,
          name: 'image',
          formData: { prompt: '请识别这张图片中的安全隐患' },
          header: { Authorization: `Bearer ${token}` },
          success: (res) => {
            if (res.statusCode >= 200 && res.statusCode < 300) {
              resolve(JSON.parse(res.data))
          } else {
            reject(new Error(friendlyServerError(res.statusCode)))
          }
          },
          fail: (err) => reject(new Error(err.errMsg)),
        })
      })

      let answer: string
      if (result.has_hazard || result.risk_status === '存在隐患') {
        answer = `隐患识别结果：\n风险状态：${result.risk_status || '存在隐患'}\n风险类别：${result.risk_category || '未分类'}\n主要风险：${result.top1_risk || '未知'}\n`
        if (result.evidence?.length) answer += `证据：${result.evidence.join('、')}\n`
        if (result.recheck_suggestion?.length) answer += `复查建议：${result.recheck_suggestion.join('、')}`
      } else {
        answer = '未识别到明显安全隐患。'
      }
      this.setData({
        messages: [...this.data.messages, { role: 'assistant', text: answer }],
      })
    } catch (error) {
      this.setData({
        messages: [...this.data.messages, { role: 'assistant', text: `识别失败：${(error as Error).message}` }],
      })
    } finally {
      this.setData({ loading: false })
    }
  },

  /** 语音拍照自动创建工作记录 — 进入语音会话 */
  startVoiceWorkSession(photoPath: string) {
    const userMsg: Message = photoPath
      ? { role: 'user', text: '用这张照片记录现场工作', image: photoPath }
      : { role: 'user', text: '语音记录现场工作' }
    this.setData({
      showPlusPanel: false,
      voiceSessionActive: true,
      voiceWorkMode: true,
      voiceWorkPending: false,
      voiceWorkPhoto: photoPath,
      voiceWorkAccumulated: '',
      voiceSessionTexts: [],
      pendingImage: photoPath,
      messages: [...this.data.messages, userMsg],
    })
    const prompt = '📸 请按住说话描述现场工作，例如"3号楼二层，钢筋绑扎，天气晴，一切正常"，说完后点「完成记录」。'
    this.addMessage({ role: 'assistant', text: prompt, type: 'text' })
    this.speakText(prompt)
  },

  /** 语音拍照自动创建 — 提取并创建/追问 */
  async autoCreateWorkSession() {
    if (!this.data.voiceSessionTexts.length || this.data.voiceWorkCreating) return
    this.setData({ voiceWorkCreating: true })
    wx.showLoading({ title: 'AI提取创建中…' })
    try {
      const voiceText = this.data.voiceSessionTexts.join('\n')
      // 累计文本
      const accumulated = this.data.voiceWorkAccumulated
        ? this.data.voiceWorkAccumulated + '\n' + voiceText
        : voiceText
      this.setData({ voiceWorkAccumulated: accumulated })

      const token = session.getToken()
      const autoTime = this.currentHm()
      const photoPath = this.data.voiceWorkPhoto
      const result = await new Promise<any>((resolve, reject) => {
        if (!photoPath) {
          // 没拍照（先说话、稍后补拍）→ 走 JSON 端点
          this.autoCreateWorkSessionJson(voiceText, this.data.voiceWorkAccumulated, autoTime).then(resolve, reject)
          return
        }
        wx.uploadFile({
          url: `${config.apiBaseUrl}/work-session/voice-create`,
          filePath: photoPath,
          name: 'photo',
          header: { Authorization: `Bearer ${token}` },
          formData: { voice_text: voiceText, previous_text: this.data.voiceWorkAccumulated, auto_time: autoTime },
          success: (res) => {
            if (res.statusCode >= 200 && res.statusCode < 300) {
              try {
                resolve(JSON.parse(res.data))
              } catch {
                reject(new Error('返回格式异常'))
              }
            } else {
              reject(new Error(`HTTP ${res.statusCode}`))
            }
          },
          fail: (err) => reject(new Error(err.errMsg || '上传失败')),
        })
      })

      if (result.created) {
        // 创建成功（记录时间由后端自动生成到分钟）
        const timeTip = result.record_time ? `，记录时间 ${result.record_time}` : ''
        const successMsg = `✅ ${result.record_type}记录已创建${timeTip}`
        this.addMessage({ role: 'assistant', text: successMsg })
        this.speakText(successMsg)
        this.setData({
          voiceSessionActive: false,
          voiceWorkMode: false,
          voiceWorkPending: false,
          voiceWorkPhoto: '',
          voiceWorkAccumulated: '',
          voiceSessionTexts: [],
          workMissing: [],
          workPhotoAsked: false,
          pendingImage: '',
        })
      } else {
        // 缺字段 → AI 追问，并给出候选值让用户点选（不合适再自己填写）
        const promptText = result.prompt_text || '请补充更多信息'
        this.addMessage({ role: 'assistant', text: promptText })
        this.speakText(promptText)
        const missing = (result.missing_fields || []) as { field: string; label: string }[]
        const chips = missing
          .filter(m => m.field !== 'start_time' && m.field !== 'end_time')
          .map(m => ({ field: m.field, label: m.label, options: FIELD_SUGGESTIONS[m.field] || [] }))
        // 保持语音会话，清空当前片段，等待补充
        this.setData({
          voiceWorkPending: true,
          voiceSessionTexts: [],
          workMissing: chips,
        })
        this.addMessage({
          role: 'assistant',
          text: '点选下面符合的项，也可以直接在下方输入或按住麦克风说一句：',
          commands: chips,
        })
      }
    } catch (error) {
      const errMsg = `创建失败：${(error as Error).message}`
      this.addMessage({ role: 'assistant', text: errMsg, type: 'text' })
      this.speakText(errMsg)
    } finally {
      this.setData({ voiceWorkCreating: false })
      wx.hideLoading()
    }
  },

  /** 无照片时走 JSON 端点（/work-session/voice-create-text），支持「先说话、稍后补拍」 */
  async autoCreateWorkSessionJson(voiceText: string, previousText: string, autoTime = '') {
    return await request<any>('/work-session/voice-create-text', {
      method: 'POST',
      data: {
        voice_text: voiceText,
        previous_text: previousText,
        auto_time: autoTime || this.currentHm(),
      },
      timeout: 120000,
    })
  },

  applyExtractedFields(workType: string, fields: Record<string, string>) {
    const typeKeyMap: Record<string, string> = {
      '巡视': 'inspection',
      '验收': 'acceptance',
      '旁站': 'station',
      '见证': 'witness',
    }
    const typeKey = typeKeyMap[workType] || 'inspection'
    let form: Record<string, string> = {}
    if (typeKey === 'inspection') {
      form = {
        section: fields.section || '',
        location: fields.location || '',
        work_content: fields.work_content || '',
        weather: fields.weather || '',
        worker_count: fields.worker_count || '', inspection_start_time: fields.inspection_start_time || '', inspection_end_time: fields.inspection_end_time || '',
        record_date: fields.record_date || '',
        inspection_remark: fields.inspection_remark || '',
        problems: fields.problems || '',
        treatment: fields.treatment || '',
      }
    } else if (typeKey === 'acceptance') {
      form = {
        acceptance_type: fields.acceptance_type || '',
        section: fields.section || '',
        location: fields.location || '',
        record_date: fields.record_date || '', acceptance_participants: fields.acceptance_participants || '',
        acceptance_content: fields.acceptance_content || '',
        acceptance_basis: fields.acceptance_basis || '',
        acceptance_result: fields.acceptance_result || '',
        treatment: fields.treatment || '',
        remark: fields.remark || '',
      }
    } else if (typeKey === 'station') {
      form = {
        station_project_name: fields.station_project_name || '',
        station_process: fields.station_process || '', record_date: fields.record_date || '',
        station_location: fields.station_location || '',
        weather: fields.weather || '',
        start_time: '',
        end_time: '',
        station_content: fields.station_content || '',
        station_supervision_status: fields.station_supervision_status || '',
        station_conclusion: fields.station_conclusion || '',
        problems: fields.problems || '',
        treatment: fields.treatment || '',
      }
    } else {
      form = {
        witness_material_name: fields.witness_material_name || '',
        witness_material_spec: fields.witness_material_spec || '',
        witness_quantity: fields.witness_quantity || '',
        witness_unit: fields.witness_unit || '',
        witness_cert_no: fields.witness_cert_no || '',
        witness_accept_result: fields.witness_accept_result || '',
        manufacturer: fields.manufacturer || '',
        section: fields.section || '',
        location: fields.location || '',
        remark: fields.remark || '',
      }
    }
    this.setData({
      showWorkForm: true,
      workFormType: typeKey,
      workForm: form,
      workFormFields: Object.keys(form).map(key => ({ key, label: WORK_FIELD_LABELS[key] || key })), 
      voiceSessionActive: false,
    })
  },

  clearVoiceSession() {
    this.setData({
      voiceSessionTexts: [],
      voiceSessionExtracted: false,
      voiceSessionInfo: '',
      voiceWorkMode: false,
      voiceWorkPending: false,
      voiceWorkPhoto: '',
      voiceWorkAccumulated: '',
      voiceWorkCreating: false,
      workMissing: [],
      workPhotoAsked: false,
      pendingImage: '',
    })
  },

  addMessage(msg: Message) {
    if (msg.role === 'assistant' && msg.text) {
      msg = { ...msg, text: cleanMarkdown(msg.text) }
    }
    this.setData({ messages: [...this.data.messages, msg] })
  },

  openWorkForm(type: string) {
    const fields: Record<string, Record<string, string>> = {
      inspection: {
        section: '', location: '', work_content: '', weather: '',
        record_date: '', worker_count: '', inspection_start_time: '', inspection_end_time: '',
        inspection_remark: '', problems: '', treatment: '',
      },
      acceptance: {
        acceptance_type: '', section: '', location: '', acceptance_content: '', record_date: '', acceptance_participants: '',
        acceptance_basis: '', acceptance_result: '', treatment: '', remark: '',
      },
      station: {
        station_project_name: '', station_location: '', weather: '', station_process: '', record_date: '',
        start_time: '', end_time: '', station_content: '',
        station_supervision_status: '', station_conclusion: '', problems: '', treatment: '',
      },
      witness: {
        witness_material_name: '', witness_material_spec: '', witness_quantity: '',
        witness_unit: '', witness_cert_no: '', witness_accept_result: '',
        manufacturer: '', section: '', location: '', remark: '',
      },
    }
    this.setData({
      showWorkForm: true,
      workFormType: type,
      workForm: fields[type] || {},
      workFormFields: Object.keys(fields[type] || {}).map(key => ({ key, label: WORK_FIELD_LABELS[key] || key })), 
    })
  },

  onWorkFormInput(event: WechatMiniprogram.Input) {
    const field = event.currentTarget.dataset.field as string
    this.setData({ [`workForm.${field}`]: event.detail.value })
  },

  /** 项目表单（新建/修改共用）输入。
   *  注意：不能复用 onWorkFormInput —— 那个写的是 workForm.*，
   *  input 的 value 绑的却是 projectForm.*，会导致「打字打不进去」。 */
  onProjectFormInput(event: WechatMiniprogram.Input) {
    const field = event.currentTarget.dataset.field as string
    this.setData({ [`projectForm.${field}`]: event.detail.value })
  },

  onWorkFormPickerChange(event: WechatMiniprogram.PickerChange) {
    const field = event.currentTarget.dataset.field as string
    const options = (event.currentTarget.dataset.options as string[]) || []
    this.setData({ [`workForm.${field}`]: options[Number(event.detail.value)] || '' })
  },

  async submitWorkForm() {
    const type = this.data.workFormType
    const form: Record<string, any> = { ...this.data.workForm }
    for (const key of ['start_time', 'end_time', 'inspection_start_time', 'inspection_end_time', 'record_date']) { if (!form[key]) delete form[key] }
    if ('worker_count' in form) form.worker_count = form.worker_count === '' ? null : Number(form.worker_count)
    // schema 必填字段校验（provform draft-2026.09）
    const required: Record<string, Array<[string, string]>> = {
      inspection: [['section', '巡视部位'], ['work_content', '施工情况']],
      acceptance: [['acceptance_type', '验收类型'], ['section', '施工区域'], ['acceptance_content', '验收内容'], ['acceptance_result', '验收结果']],
      station: [['station_location', '旁站部位'], ['station_process', '关键工序']],
      witness: [['witness_material_name', '材料名称'], ['witness_quantity', '取样数量']],
    }
    for (const [field, label] of required[type] || []) {
      if (!String(form[field] || '').trim()) {
        wx.showToast({ title: `请填写${label}`, icon: 'none' })
        return
      }
    }
    // 旁站时间为 HH:MM（省统表 schema），后端为 datetime：与当日日期拼成 ISO 提交
    if (type === 'station') {
      const today = this.data.logDate || new Date().toISOString().slice(0, 10)
      const payload = { ...form }
      if (payload.start_time && /^\d{2}:\d{2}$/.test(payload.start_time)) payload.start_time = `${form.record_date || today}T${payload.start_time}`
      if (payload.end_time && /^\d{2}:\d{2}$/.test(payload.end_time)) payload.end_time = `${form.record_date || today}T${payload.end_time}`
      await this.postWorkForm(type, payload)
      return
    }
    await this.postWorkForm(type, form)
  },

  async postWorkForm(type: string, payload: Record<string, any>) {
    this.setData({ workFormSubmitting: true })
    const endpoint = type === 'inspection' ? '/work-record/inspection'
      : type === 'acceptance' ? '/work-record/acceptance'
      : type === 'station' ? '/work-record/station'
      : '/work-record/witness'
    try {
      await request(endpoint, { method: 'POST', data: payload })
      wx.showToast({ title: '创建成功', icon: 'success' })
      this.setData({ showWorkForm: false })
      this.setData({
        messages: [...this.data.messages, {
          role: 'assistant',
          text: `${type === 'inspection' ? '巡视' : type === 'acceptance' ? '验收' : type === 'station' ? '旁站' : '见证'}记录已创建`,
        }],
      })
    } catch (error) {
      wx.showToast({ title: (error as Error).message, icon: 'none' })
    } finally {
      this.setData({ workFormSubmitting: false })
    }
  },

  async submitDailyLog() {
    this.setData({ logGenerating: true })
    try {
      const result = await request<{ doc_id: number; work_count: number; hazard_count: number; summary?: { work_records?: number; hazards?: number } }>(
        `/document/generate-daily-log?date=${this.data.logDate}`, {
          method: 'POST',
          data: { weather: this.data.logWeather, temp_low: this.data.logTempLow, temp_high: this.data.logTempHigh },
          timeout: 60000,
        },
      )
      // 后端计数在 summary 里（work_records/hazards），work_count/hazard_count 为顶层别名兜底
      const workCount = result.summary?.work_records ?? result.work_count ?? 0
      const hazardCount = result.summary?.hazards ?? result.hazard_count ?? 0
      this.setData({
        showLogDialog: false,
        messages: [...this.data.messages, {
          role: 'assistant',
          text: `监理日志已生成（文书ID: ${result.doc_id}），包含 ${workCount} 条工作记录、${hazardCount} 条隐患记录。`,
        }],
      })
    } catch (error) {
      wx.showToast({ title: (error as Error).message, icon: 'none' })
    } finally {
      this.setData({ logGenerating: false })
    }
  },

  async openTaskForm() {
    this.setData({ showTaskForm: true })
    try {
      const users = await request<{ id: number; real_name: string }[]>('/task/assignable-users')
      this.setData({ assignableUsers: users || [] })
    } catch { /* ignore */ }
  },

  onTaskFormInput(event: WechatMiniprogram.Input) {
    const field = event.currentTarget.dataset.field as string
    this.setData({ [`taskForm.${field}`]: event.detail.value })
  },

  onTaskAssigneeChange(event: WechatMiniprogram.PickerChange) {
    const idx = Number(event.detail.value)
    const user = this.data.assignableUsers[idx]
    this.setData({ 'taskForm.assignee_id': String(user?.id || '') })
  },

  async submitTaskForm() {
    const form = this.data.taskForm
    if (!form.title.trim()) { wx.showToast({ title: '请输入任务标题', icon: 'none' }); return }
    this.setData({ taskSubmitting: true })
    try {
      await request('/task', {
        method: 'POST',
        data: { ...form, assignee_id: Number(form.assignee_id) || undefined },
      })
      wx.showToast({ title: '派单成功', icon: 'success' })
      this.setData({ showTaskForm: false })
      this.setData({
        messages: [...this.data.messages, { role: 'assistant', text: `任务「${form.title}」已派发` }],
      })
    } catch (error) {
      wx.showToast({ title: (error as Error).message, icon: 'none' })
    } finally {
      this.setData({ taskSubmitting: false })
    }
  },

  async openDocForm() {
    this.setData({ showDocForm: true })
  },

  onDocFormInput(event: WechatMiniprogram.Input) {
    const field = event.currentTarget.dataset.field as string
    this.setData({ [`docForm.${field}`]: event.detail.value })
  },

  onDocTypeChange(event: WechatMiniprogram.PickerChange) {
    const idx = Number(event.detail.value)
    this.setData({ 'docForm.doc_type': this.data.docTypes[idx] })
  },

  async polishDocContent() {
    if (!this.data.docForm.content.trim()) { wx.showToast({ title: '请先输入内容', icon: 'none' }); return }
    this.setData({ docPolishing: true })
    try {
      const result = await request<{ content: string }>(
        '/document/generate-with-llm', {
          method: 'POST',
          data: {
            doc_type: this.data.docForm.doc_type,
            title: this.data.docForm.title,
            extra_context: this.data.docForm.content,
          },
          timeout: 60000,
        },
      )
      if (result.content) {
        this.setData({ 'docForm.content': result.content })
        wx.showToast({ title: 'AI润色完成', icon: 'success' })
      }
    } catch (error) {
      wx.showToast({ title: (error as Error).message, icon: 'none' })
    } finally {
      this.setData({ docPolishing: false })
    }
  },

  async submitDocForm() {
    const form = this.data.docForm
    if (!form.title.trim()) { wx.showToast({ title: '请输入标题', icon: 'none' }); return }
    this.setData({ docSubmitting: true })
    try {
      // 后端 DocCreateRequest.content 为字典，自由文本正文统一放入 details 字段
      const result = await request<{ doc_id: number; status: string; doc_number?: string }>('/document/create', {
        method: 'POST',
        data: { title: form.title, content: { details: form.content.trim() }, doc_type: form.doc_type },
      })
      wx.showToast({ title: '文书已创建', icon: 'success' })
      this.setData({ showDocForm: false })
      this.setData({
        messages: [...this.data.messages, {
          role: 'assistant',
          text: `监理文书「${form.title}」已创建（ID: ${result.doc_id}）`,
        }],
      })
    } catch (error) {
      wx.showToast({ title: (error as Error).message, icon: 'none' })
    } finally {
      this.setData({ docSubmitting: false })
    }
  },

  closeOverlay() {
    // 关闭面板时停止 TTS 播报
    if (this.data.speaking) {
      player.stop()
      this.setData({ speaking: false })
    }
    this.setData({
      showProjectForm: false, showLogDialog: false, showWorkForm: false,
      showTaskForm: false, showDocForm: false,
      showModelMenu: false, showPlusPanel: false,
      voiceSessionActive: false, voiceSessionTexts: [],
      voiceSessionExtracted: false, voiceSessionInfo: '',
      voiceWorkMode: false, voiceWorkPending: false,
      voiceWorkPhoto: '', voiceWorkAccumulated: '', voiceWorkCreating: false,
      pendingImage: '',
    })
  },

  cancelProject() {
    this.setData({ showProjectForm: false, projectFormMode: 'create' })
  },

  /** 侧边栏「项目概况」→ 点进修改当前项目（名称/地点/参建单位/描述） */
  async openProjectEdit() {
    if (!this.data.currentProjectId) {
      wx.showToast({ title: '请先创建项目', icon: 'none' })
      return
    }
    let current = this.data.projectList.find((p) => p.id === this.data.currentProjectId)
    if (!current) {
      // 列表还没拉回来（或缓存过期）→ 现拉一次，保证施工单位回填不空
      try {
        const projects = await request<Array<{ id: number; name: string; code: string; construction_unit?: string; supervision_unit?: string }>>('/project/list')
        this.setData({ projectList: projects })
        current = projects.find((p) => p.id === this.data.currentProjectId)
      } catch { /* 拉失败就按空回填，保存时用户重新填 */ }
    }
    this.setData({
      showHistory: false,
      projectFormMode: 'edit',
      projectForm: {
        projectName: this.data.projectName,
        location: this.data.projectLocation,
        description: this.data.projectSummary,
        constructionUnit: current?.construction_unit || '',
        supervisionUnit: current?.supervision_unit || '',
      },
      showProjectForm: true,
    })
  },

  /** 保存项目信息：mode=create 走创建，mode=edit 走 PUT 更新后回填侧边栏 */
  async submitProject() {
    const form = this.data.projectForm
    const name = form.projectName.trim()
    if (!name) { wx.showToast({ title: '请输入项目名称', icon: 'none' }); return }
    const location = form.location.trim()
    const description = form.description.trim()
    const constructionUnit = form.constructionUnit.trim()
    const supervisionUnit = form.supervisionUnit.trim()
    const isEdit = this.data.projectFormMode === 'edit' && this.data.currentProjectId > 0
    this.setData({ loading: true })
    try {
      if (isEdit) {
        const result = await request<{ id: number; name: string; location?: string; description?: string; construction_unit?: string; supervision_unit?: string }>(
          `/project/${this.data.currentProjectId}`,
          {
            method: 'PUT',
            data: {
              name, location, description,
              // 表单已回填当前值：用户清空即视为清除，总是提交
              construction_unit: constructionUnit || null,
              supervision_unit: supervisionUnit || null,
            },
          },
        )
        const user = session.getUser()
        if (user) {
          user.project_name = result.name
          session.save(session.getToken(), user)
        }
        const list = this.data.projectList.map((p) => (
          p.id === this.data.currentProjectId
            ? {
                ...p,
                name: result.name,
                location: result.location || '',
                description: result.description || '',
                construction_unit: result.construction_unit || undefined,
                supervision_unit: result.supervision_unit || undefined,
              }
            : p
        ))
        this.setData({
          showProjectForm: false,
          projectFormMode: 'create',
          loading: false,
          projectName: result.name,
          projectLocation: result.location || '',
          projectSummary: result.description || '',
          projectList: list,
        })
        wx.showToast({ title: '项目信息已更新', icon: 'success' })
        return
      }

      const code = `P${Date.now().toString(36).toUpperCase()}`
      const result = await request<{ id: number; name: string }>('/project/create', {
        method: 'POST',
        data: {
          name, code, location, description: description || name,
          construction_unit: constructionUnit || undefined,
          supervision_unit: supervisionUnit || undefined,
        },
      })
      const user = session.getUser()
      if (user) {
        user.project_id = result.id
        user.project_name = result.name
        session.save(session.getToken(), user)
      }
      this.setData({
        showProjectForm: false, projectFormMode: 'create', projectCreated: true,
        projectName: result.name, loading: false, projectLocation: location,
        currentProjectId: result.id,
        projectSummary: description || name,
        messages: [
          ...this.data.messages,
          { role: 'user', text: `创建项目：${name}` },
          { role: 'assistant', text: `项目「${result.name}」已创建成功！现在可以开始工作了。` },
        ],
      })
    } catch (error) {
      wx.showToast({ title: (error as Error).message, icon: 'none' })
      this.setData({ loading: false })
    }
  },

  saveCurrentSession() {
    const messages = this.data.messages
    if (!messages.some(m => m.role === 'user')) return
    const userMsg = messages.find(m => m.role === 'user')
    const title = userMsg ? userMsg.text.slice(0, 15) : '新对话'
    const d = new Date()
    const now = `${d.getMonth() + 1}/${d.getDate()} ${d.getHours()}:${String(d.getMinutes()).padStart(2, '0')}`
    const list = (wx.getStorageSync(HISTORY_KEY) || []) as ChatHistory[]
    // 复用当前会话 id：同一会话多次保存只覆盖同一条，不产生重复记录
    const id = this.currentSessionId || Date.now()
    this.currentSessionId = id
    const entry: ChatHistory = {
      id,
      title,
      modelId: this.data.currentModel,
      messages,
      updatedAt: now,
    }
    wx.setStorageSync(HISTORY_KEY, [entry, ...list.filter(h => h.id !== id)].slice(0, 30))
  },

  openHistory() {
    this.saveCurrentSession()
    this.setData({ historyList: wx.getStorageSync(HISTORY_KEY) || [], showHistory: true })
    this.loadProjectSummary()
  },

  /** 拉取当前项目概况（名称/地点/描述），用于侧边栏顶部展示与「点进去修改」回填 */
  async loadProjectSummary() {
    if (!this.data.currentProjectId) return
    try {
      const projects = await request<Array<{ id: number; name: string; code: string; location?: string; description?: string; construction_unit?: string; supervision_unit?: string }>>('/project/list')
      const target = projects.find((p) => p.id === this.data.currentProjectId)
      if (!target) return
      this.setData({
        projectName: target.name || this.data.projectName,
        projectLocation: target.location || '',
        projectSummary: target.description || '',
        projectList: projects,
      })
    } catch {
      // 概况拉取失败不阻塞侧边栏
    }
  },

  closeHistory() { this.setData({ showHistory: false }) },

  /** 侧边栏分组折叠：data-group = docs | ledger | history
   *  （tools 组 2026-09-30 去重时整组移除，入口统一走「+」面板；
   *    kb 组 2026-10-08 整组移除——资料库升为底栏 tab，规范资料库/我的笔记挂到「+」面板） */
  toggleDrawerGroup(event: WechatMiniprogram.TouchEvent) {
    const group = String((event.currentTarget.dataset as { group?: string }).group || '')
    if (group === 'docs') {
      this.setData({ drawerDocsExpanded: !this.data.drawerDocsExpanded })
    } else if (group === 'ledger') {
      this.setData({ drawerLedgerExpanded: !this.data.drawerLedgerExpanded })
    } else if (group === 'history') {
      this.setData({ drawerHistoryExpanded: !this.data.drawerHistoryExpanded })
    }
  },

  async switchProject() {
    try {
      const projects = await request<Array<{ id: number; name: string; code: string; location?: string; description?: string; construction_unit?: string; supervision_unit?: string }>>('/project/list')
      const target = projects.find((p) => p.id === this.data.currentProjectId)
      this.setData({
        projectList: projects,
        showProjectPicker: true,
        projectLocation: target?.location || this.data.projectLocation,
        projectSummary: target?.description || this.data.projectSummary,
      })
    } catch (error) {
      wx.showToast({ title: (error as Error).message, icon: 'none' })
    }
  },

  closeProjectPicker() { this.setData({ showProjectPicker: false }) },

  /**
   * 「切换项目」弹层 → 进入「创建新项目」表单。
   * 该表单此前只在 onLoad 发现用户没绑项目时自动弹出，一旦绑过项目就再无入口（本次补上）。
   */
  openProjectCreate() {
    this.setData({
      showProjectPicker: false,
      projectFormMode: 'create',
      showProjectForm: true,
      projectForm: { projectName: '', location: '', description: '', constructionUnit: '', supervisionUnit: '' },
    })
  },

  async pickProject(event: WechatMiniprogram.TouchEvent) {
    const projectId = Number(event.currentTarget.dataset.id)
    if (projectId === this.data.currentProjectId || this.data.switchingProject) return
    this.setData({ switchingProject: true })
    try {
      await request(`/project/${projectId}/switch`, { method: 'POST', data: {} })
      const user = session.getUser()
      const target = this.data.projectList.find((p) => p.id === projectId)
      if (user && target) {
        user.project_id = projectId
        user.project_name = target.name
        session.save(session.getToken(), user)
      }
      this.setData({
        projectCreated: true,
        projectName: target?.name || '',
        projectLocation: target?.location || '',
        currentProjectId: projectId,
        showProjectPicker: false,
        projectSummary: target?.description || '',
      })
      wx.showToast({ title: `已切换至${target?.name || ''}`, icon: 'success' })
      this.currentSessionId = 0
      this.initMessages()
      void this.loadPendingDrafts()
    } catch (error) {
      wx.showModal({ title: '切换失败', content: (error as Error).message, showCancel: false })
    } finally {
      this.setData({ switchingProject: false })
    }
  },

  loadHistoryItem(event: WechatMiniprogram.TouchEvent) {
    const id = Number(event.currentTarget.dataset.id)
    const list = (wx.getStorageSync(HISTORY_KEY) || []) as ChatHistory[]
    const item = list.find(h => h.id === id)
    if (!item) return
    // 接续该历史会话：继续对话时覆盖同一条历史，而不是新建
    this.currentSessionId = item.id
    this.setData({
      showHistory: false,
      mode: 'chat',
      hazardMode: false,
      messages: item.messages,
    })
    void this.loadPendingDrafts()
  },

  deleteHistoryItem(event: WechatMiniprogram.TouchEvent) {
    const id = Number(event.currentTarget.dataset.id)
    const list = ((wx.getStorageSync(HISTORY_KEY) || []) as ChatHistory[]).filter(h => h.id !== id)
    wx.setStorageSync(HISTORY_KEY, list)
    this.setData({ historyList: list })
  },

  newChat() {
    this.saveCurrentSession()
    this.currentSessionId = 0
    this.setData({ showHistory: false })
    this.initMessages()
    void this.loadPendingDrafts()
    wx.showToast({ title: '已开启新对话', icon: 'none' })
  },

  navigateTo(event: WechatMiniprogram.TouchEvent) {
    const url = event.currentTarget.dataset.url as string
    this.setData({ showHistory: false, showPlusPanel: false })
    wx.navigateTo({ url })
  },

  // 输入卡左侧的模型下拉（千问 / 华工）
  toggleModelMenu() {
    this.setData({ showModelMenu: !this.data.showModelMenu })
  },

  selectModel(event: WechatMiniprogram.TouchEvent) {
    const id = event.currentTarget.dataset.model as string
    const model = this.data.models.find(m => m.id === id)
    if (!model) return
    wx.setStorageSync(`gdjl_${config.storageScope}_agent_model`, model.id)
    this.setData({ currentModel: model.id, modelName: model.name, showModelMenu: false })
    this.addMessage({ role: 'assistant', text: `已切换为「${model.name}」模型。` })
  },

  onInput(event: WechatMiniprogram.Input) {
    const input = event.detail.value
    this.setData({ input, canSend: Boolean(input.trim()) || this.data.attachments.length > 0 })
  },

  onLogDateChange(event: WechatMiniprogram.PickerChange) {
    this.setData({ logDate: String(event.detail.value) })
  },

  onLogWeatherChange(event: WechatMiniprogram.PickerChange) {
    const idx = Number(event.detail.value)
    this.setData({ logWeather: this.data.weatherOptions[idx] || '' })
  },

  onLogTempInput(event: WechatMiniprogram.Input) {
    const field = event.currentTarget.dataset.field as string
    this.setData({ [field]: event.detail.value } as Record<string, string>)
  },

  onInputTouchStart() {
    this.touchStartTime = Date.now()
    this.longPressTimer = setTimeout(() => {
      this.startRecord()
    }, 300)
  },

  onInputTouchEnd() {
    if (this.longPressTimer) {
      clearTimeout(this.longPressTimer)
      this.longPressTimer = null
    }
    // 松手即结束：已在录则停止转写；若授权/启动还没完成，stopRecord 内部会取消这次启动
    this.stopRecord()
  },

  onInputTouchCancel() {
    if (this.longPressTimer) {
      clearTimeout(this.longPressTimer)
      this.longPressTimer = null
    }
    // 手指滑走 / 手势被系统打断 → 作废本次录音（同微信语音「上滑取消」语义）
    this.pendingRecordCancel = true
    if (this.data.recording) {
      this.discardRecording = true
      recorder.stop()
    }
  },

  async startRecord() {
    if (this.data.loading) return
    // 录音时打断 TTS 播报
    if (this.data.speaking) {
      player.stop()
      this.setData({ speaking: false })
    }
    this.pendingRecordCancel = false
    const allowed = await ensurePrivacy('录音用于语音提问监理智能体，')
    if (!allowed) return
    // 等隐私弹窗 / 麦克风授权期间用户就已松手 → 放弃本次启动。
    // 否则会出现「手指已经松开了，录音才开始」的错位，松手即结束就无从谈起。
    if (this.pendingRecordCancel) {
      this.pendingRecordCancel = false
      return
    }
    // 不做 wx.authorize('scope.record') 预检：authorize 对隐私类 scope 从不弹窗，
    // 用户没走过官方授权链路时必定直接 fail，会把用户引到「去设置」死路。
    // 直接 recorder.start()，由微信依次弹「官方隐私弹窗 → 系统麦克风授权」；
    // 用户此前拒绝过时 onError 兜底引导 openSetting。
    this.discardRecording = false
    this.recordingStartedAt = Date.now()
    recorder.start({ duration: 60000, sampleRate: 16000, numberOfChannels: 1, encodeBitRate: 48000, format: 'mp3' })
    this.setData({ recording: true })
  },

  /**
   * 结束录音 —— 「松手即结束」的唯一出口（右上麦克风 / 长按输入框 / 语音会话面板共用）。
   * 已在录 → 停止并转写；按得太短（<600ms）→ 视为误触，丢弃不上传；
   * 还没开录（授权/启动中）→ 标记取消启动，避免松手之后才开始录。
   */
  stopRecord() {
    if (!this.data.recording) {
      this.pendingRecordCancel = true
      return
    }
    if (Date.now() - this.recordingStartedAt < 600) {
      this.discardRecording = true
      recorder.stop()
      wx.showToast({ title: '说话时间太短', icon: 'none' })
      return
    }
    recorder.stop()
  },

  /**
   * 右上角独立麦克风：与输入框统一为「按住说话、松手结束」（2026-10-13 由「点一下开始/再点一下结束」改为按住）。
   * 松手＝结束并转写；touchcancel（手指滑出按钮范围/被系统打断）按松手处理，
   * 避免轻微滑动就把刚说的一段话静默丢掉。
   */
  onMicTouchStart() {
    if (this.data.loading) return
    void this.startRecord()
  },

  onMicTouchEnd() {
    this.stopRecord()
  },

  refreshPendingVoice() {
    const count = uploadQueue.list().filter((item) => item.mediaType === 'audio').length
    if (count !== this.data.pendingVoiceCount) this.setData({ pendingVoiceCount: count })
  },

  /** 重试一段待补传语音，成功后把识别文本按当前模式处理 */
  async retryPendingVoice() {
    if (this.data.pendingVoiceRetrying || this.data.loading) return
    const pending = uploadQueue.list().filter((item) => item.mediaType === 'audio')
    if (pending.length === 0) {
      this.setData({ pendingVoiceCount: 0 })
      return
    }
    this.setData({ pendingVoiceRetrying: true, loading: true })
    try {
      const result = await uploadQueue.retryOne(pending[0].id)
      const text = result?.text || ''
      if (!text) throw new Error('没有识别到有效语音')
      if (this.data.voiceSessionActive) {
        this.setData({ voiceSessionTexts: [...this.data.voiceSessionTexts, text] })
        wx.showToast({ title: '语音片段已记录', icon: 'success' })
      } else {
        this.setData({ input: text })
        await this.send()
      }
    } catch (error) {
      wx.showToast({ title: (error as Error).message, icon: 'none' })
    } finally {
      this.setData({ pendingVoiceRetrying: false, loading: false })
      this.refreshPendingVoice()
    }
  },

  async discardPendingVoice() {
    const pending = uploadQueue.list().filter((item) => item.mediaType === 'audio')
    if (pending.length === 0) return
    const confirmed = await new Promise<boolean>((resolve) => {
      wx.showModal({
        title: '取消待上传语音',
        content: `将丢弃 ${pending.length} 段未上传的语音，确认取消？`,
        success: (result) => resolve(Boolean(result.confirm)),
      })
    })
    if (!confirmed) return
    pending.forEach((item) => uploadQueue.remove(item.id))
    this.refreshPendingVoice()
    wx.showToast({ title: '已取消', icon: 'none' })
  },

  async speak(event: WechatMiniprogram.TouchEvent) {
    const text = String(event.currentTarget.dataset.text || '').trim()
    if (!text) return
    this.speakText(text)
  },

  /** TTS 播报文本，支持随时打断：若正在播报则先停再播新文本 */
  async speakText(text: string) {
    const trimmed = (text || '').replace(/[📸✅❌]/g, '').trim()
    if (!trimmed) return
    // 正在播报 → 先打断当前播放
    if (this.data.speaking) {
      player.stop()
      this.setData({ speaking: false })
    }
    this.setData({ speaking: true })
    try {
      const result = await request<{ audio_url: string }>('/local/tts/synthesize', {
        method: 'POST', data: { text: trimmed }, timeout: 60000,
      })
      player.src = absoluteMediaUrl(result.audio_url)
      player.play()
    } catch {
      this.setData({ speaking: false })
    }
  },
}))
