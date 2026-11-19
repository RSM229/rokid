import { request, abortableRequest } from '../../services/request'
import { isExecutionRecoverable, newOperationId, waitForAgentExecution } from '../../services/agent-execution'
import { session } from '../../services/session'
import { uploadAudio, uploadDocument, uploadImage, friendlyServerError } from '../../services/upload'
import { persistTempFile, uploadQueue } from '../../services/uploadQueue'
import { absoluteMediaUrl, config } from '../../config'
import { getCached, invalidateCache, invalidateByPrefix, CACHE_KEYS } from '../../services/cache'
import { ensurePrivacy } from '../../utils/privacy'
import { themed } from '../../utils/theme'
import { cleanMarkdown } from '../../utils/markdown'
import { normalizeDateTimeInput, resolveDateTimeRange } from '../../utils/date-time-picker'
import { MODEL_CATALOG } from '../../services/modelCatalog'
import { FORM_CODE_BY_LABEL, createReviewPlan, saveReviewPlan, listReviewPlans, confirmReviewPlan, executeReviewPlan, editEvent, editFormField, editEvidenceWatermark, addEvidenceWatermark, confirmPlan, reviewIssues, evidenceTargetConflicts, reviewPlanFromItemPlan, mergeEvents, splitEvidence, releaseEvidence, assignEvidence, addEvent, removeEvent, type ReviewPlan, type RequestPlanItemPlan } from '../../services/work-review'

const INLINE_RECORD_TYPES = ['巡视', '验收', '见证', '旁站', '平行检验', '起重机械安拆检查', '待定']

interface AgentProgressEventView {
  eventId: string
  sequence: number
  status: 'running' | 'completed' | 'failed' | 'waiting' | 'skipped' | string
  title: string
  detailText: string
  createdAt: string
}

interface AgentProgressTraceResponse {
  progress_events?: Array<Record<string, unknown>>
  latest_progress_sequence?: number
  status?: string
}

let progressPollTimer: number | null = null
let progressClockTimer: number | null = null
let progressWriteQueue: Promise<void> = Promise.resolve()
let activeProgressRequestId = ''
let activeProgressMessageIndex = -1
let activeProgressSequence = 0
let activeProgressPollInFlight = false
/** 进度轮询遇到 404/403 的宽限期：新建轮次的首个 GET 可能早于 POST 建追踪（竞态），
 *  追踪会在毫秒级出现，故仅在「已开始超过该时长仍 404/403」才判定失效并停轮询。 */
const PROGRESS_404_GRACE_MS = 8000

const PROGRESS_FIELD_LABELS: Record<string, string> = {
  record_date: '业务日期', date: '日期', start_time: '开始时间', end_time: '结束时间',
  inspection_start_time: '巡视开始时间', inspection_end_time: '巡视结束时间',
  station_start_time: '旁站开始时间', station_end_time: '旁站结束时间',
  record_type: '记录类型', project: '项目', project_name: '项目', location: '地点',
  section: '工程部位', work_content: '工作内容', content: '内容', inspector: '检查人员',
  construction_unit: '施工单位', supervision_unit: '监理单位', weather: '天气',
  worker_count: '作业人数', material_name: '材料名称', material_spec: '规格型号',
  sample_date: '取样日期', acceptance_result: '验收结论', problems: '发现问题',
  treatment: '处理情况',
}

function progressScalar(value: unknown): string {
  if (value == null) return ''
  if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') return String(value)
  try { return JSON.stringify(value) } catch { return String(value) }
}

function progressFieldEntries(value: unknown): string[] {
  if (!Array.isArray(value)) return []
  return value.slice(0, 24).flatMap((item: any) => {
    if (!item || typeof item !== 'object') return []
    const field = String(item.field || item.canonical || item.label || '').trim()
    const fieldValue = progressScalar(item.value).trim()
    if (!field || !fieldValue) return []
    const label = PROGRESS_FIELD_LABELS[field] || field
    const source = String(item.source || '').trim()
    return [`${label}：${fieldValue}${source ? `（${source}）` : ''}`]
  })
}

function progressDetailText(detail: unknown): string {
  if (!detail || typeof detail !== 'object' || Array.isArray(detail)) return progressScalar(detail).slice(0, 900)
  const data = detail as Record<string, unknown>
  const parts: string[] = []
  for (const key of ['facts', 'explicit_user_fields', 'recognized_fields', 'provided_fields', 'extra_fields']) {
    const entries = progressFieldEntries(data[key])
    if (entries.length) parts.push(entries.join('；'))
  }
  for (const key of ['observation', 'ocr_text', 'text_excerpt', 'file_excerpt', 'response_excerpt', 'error', 'degraded_reason']) {
    const value = progressScalar(data[key]).trim()
    if (value) parts.push(value)
  }
  const labels: Record<string, string> = {
    model: '模型', intent: '意图', action: '动作', proposed_action: '模型候选动作',
    record_type: '记录类型', proposed_record_type: '模型候选类型', form: '表式',
    file_name: '文件', filename: '文件', extracted_characters: '提取字符数',
    image_index: '图片序号', image_count: '图片数', file_count: '文件数',
    evidence_count: '证据数', recognized: '识别到字段', available: '服务可用',
    has_json_result: '结构化结果', missing_fields: '待补字段', created_id: '创建编号',
    created_ids: '创建编号', media_id: '资料编号', status: '状态',
    page_count: '页数', candidate_count: '候选事项数', event_count: '事项数',
    item_count: '字段数', duration_ms: '耗时毫秒', file_type: '文件类型',
  }
  for (const [key, label] of Object.entries(labels)) {
    if (data[key] == null || data[key] === '') continue
    const value = progressScalar(data[key])
    if (value && !parts.some(part => part.includes(`${label}：`))) parts.push(`${label}：${value}`)
  }
  if (!parts.length) return JSON.stringify(data).slice(0, 900)
  return parts.join(' · ').slice(0, 1200)
}

function progressEventView(raw: Record<string, unknown>): AgentProgressEventView | null {
  const eventId = String(raw.event_id || raw.eventId || '').trim()
  if (!eventId) return null
  const created = String(raw.created_at || raw.createdAt || '')
  const parsed = created ? new Date(created) : new Date()
  const createdAt = Number.isNaN(parsed.getTime())
    ? created
    : `${String(parsed.getHours()).padStart(2, '0')}:${String(parsed.getMinutes()).padStart(2, '0')}:${String(parsed.getSeconds()).padStart(2, '0')}`
  return {
    eventId,
    sequence: Number(raw.sequence || 0),
    status: String(raw.status || 'completed'),
    title: String(raw.title || raw.name || '处理事件'),
    detailText: progressDetailText(raw.detail),
    createdAt,
  }
}

/** 对话时间戳：完整长格式「YYYY-MM-DD HH:mm:ss」（2026-11-03 确认）。 */
function formatMessageTime(value?: string | number | Date): string {
  const date = value == null ? new Date() : new Date(value)
  if (Number.isNaN(date.getTime())) return ''
  const pad = (part: number) => String(part).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`
}

/** 把一次处理的进度轨迹合并进最终助手回复，保持「步骤在正文前」。 */
function mergeAgentProgress(target: Message, progress: Message): Message {
  return {
    ...target,
    timeText: progress.timeText || target.timeText,
    progressRequestId: progress.progressRequestId,
    progressEvents: progress.progressEvents,
    progressExpanded: progress.progressExpanded,
    progressHeadline: progress.progressHeadline,
    progressElapsedSeconds: progress.progressElapsedSeconds,
    progressStartedAt: progress.progressStartedAt,
    progressTerminal: progress.progressTerminal,
  }
}

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

interface AgentFormField {
  name: string
  key?: string
  canonical?: string
  definition_source?: string
  label: string
  control: 'text' | 'textarea' | 'select' | 'date' | 'time' | 'datetime' | 'number' | string
  required: boolean
  value: string
  options: string[]
  placeholder?: string
  hint?: string
  section?: string
  showSection?: boolean
  recommendation?: string
  reason?: string
}

interface AgentForm {
  id: string
  kind: string
  expanded?: boolean
  schema_version?: number
  field_schema?: string
  form_code?: string
  record_id?: number
  title: string
  hint?: string
  fields: AgentFormField[]
  filled?: Array<{ label: string; value: string }>
  attachments?: { enabled: boolean; label: string; max: number; hint: string; picked: Attachment[] }
  submit_label?: string
  skippable?: boolean
  busy?: boolean
  target?: { date?: string; project?: string; location?: string; section?: string }
  actions?: Array<{ type: string; label: string; url?: string; record_id?: number; command?: string }>
}

interface HazardCandidateView {
  id: string
  image: string
  localImage: string
  title: string
  riskCategory: string
  evidence: string[]
  section: string
  location: string
  weather: string
  takenAt: string
  cameraCapture: boolean
  detection: Record<string, any>
  selected: boolean
  status: 'pending' | 'committing' | 'committed' | 'discarded'
  statusText: string
  mediaId?: number
  hazardId?: number
  error?: string
}

interface Message {
  role: 'user' | 'assistant'
  text: string
  /** 消息头展示时间（备案助手样式：名称 + 时间） */
  timeText?: string
  /** 发送失败标记（2026-11-19）：失败的用户消息显示「点击重发」，不再让文字默默丢掉 */
  failed?: boolean
  hint?: string
  image?: string
  images?: string[]
  /** 服务端返回的持久化图片地址（带 access_token）。本地临时路径失效（重进会话/历史回看）
   * 时用于显示兜底；与 images（会话内本地临时路径，必然可渲染）分开存，避免互相覆盖。 */
  remoteImages?: string[]
  type?: 'text' | 'actions' | 'stats'
  actions?: { label: string; command: string }[]
  /** 工作记录字段候选（点选即填入，也可自己填写） */
  commands?: { field: string; label: string; options: string[] }[]
  /** 智能体回执卡（由 /agent/chat 返回，点击可跳详情） */
  cards?: AgentCard[]
  /** 多条回执卡默认收起，用户点开才展开（2026-11-12） */
  cardsExpanded?: boolean
  /** 追问的快速候选值（点击即作为回答发送，如 巡视/旁站/验收/见证） */
  chips?: string[]
  agentForm?: AgentForm
  /** 长期偏好建议卡片（由 /agent/chat 返回；用户点「记住」才真正落库） */
  preferenceSuggestion?: {
    rule: string
    category: string
    reason: 'assistant' | 'repeated'
    evidence?: string
    saved?: boolean
    busy?: boolean
  }
  hazardCandidates?: HazardCandidateView[]
  hazardCandidateBusy?: boolean
  hazardCandidateResult?: string
  hazardRequestId?: string
  hazardExecutionId?: string
  hazardPendingCount?: number
  reviewPlan?: ReviewPlan
  reviewEvents?: Array<{
    id: string
    title: string
    date: string
    dateConfirmed: boolean
    dateOptions: string[]
    dateSource: string
    recordType: string
    recordTypeConfidence: number
    recordTypeConfidenceText: string
    recordTypeReason: string
    recordTypeOptions: Array<{ label: string; checked: boolean }>
    evidenceCount: number
    evidenceFields: Array<{ id: string; image: string; status: string; items: Array<{ label: string; value: string; index: number }> }>
    location: string
    locationSource: string
    section: string
    sectionSource: string
    missingFields: string[]
    issues: string[]
    mergeSelected: boolean
    content: string
    watermarkSummary: string
    sourceLabels: string[]
    sourceText: string
  }>
  reviewUnassignedEvidence?: Array<{ id: string; image: string; status: string; note: string; items: Array<{ label: string; value: string }> }>
  reviewMergeSelection?: string[]
  reviewEventOptions?: Array<{ id: string; title: string }>
  reviewPurposeOptions?: Array<{ value: string; label: string }>
  reviewRecommendation?: { recordType: string; confidence: number; reason: string }
  reviewCanConfirm?: boolean
  reviewFollowupPlanId?: number
  reviewBusy?: boolean
  reviewResult?: string
  reviewIssues?: string[]
  reviewSummary?: string
  /** 工作记录草稿详情默认收起，点按钮再展开 */
  reviewDetailsExpanded?: boolean
  progressRequestId?: string
  progressEvents?: AgentProgressEventView[]
  progressExpanded?: boolean
  progressHeadline?: string
  progressElapsedSeconds?: number
  progressStartedAt?: number
  progressTerminal?: boolean
  reviewProgress?: Array<{ label: string; state: 'done' | 'current' | 'pending' }>
  /** 建档后服务端算出的缺项（只标黄提示，不拦保存、不追问） */
  docMissing?: Array<{ label: string; required: boolean }>
  /** 独立会话的文书复核意见，并进同一条回执、默认收起 */
  docReview?: { items: Array<{ id: string; level: string; text: string }>; status: string }
  docReviewExpanded?: boolean
}

/** /agent/chat 响应：kind=action 表示服务端已执行动作，chat 表示纯问答 */
interface AgentChatResponse {
  kind: 'action' | 'chat'
  conversation_id?: string
  history_saved?: boolean
  tool?: string
  reply: string
  card?: AgentCard | null
  cards?: AgentCard[]
  actions?: { type: string; label: string; url?: string }[]
  need_more?: string[]
  options?: Record<string, string[]>
  /** 服务端下发的快速动作 chips（如最终确认态「确认生成」），点击即以该词发送 */
  chips?: string[]
  input_components?: Array<{ field: string; label: string; control: string; required?: boolean; options?: string[] }>
  recommendations?: Array<{ field: string; value: string; reason?: string }>
  form?: AgentForm | null
  undo?: { entity: string; id: number } | null
  /** 工具回执业务数据；多图分组建单时带 split=true，表示后端已按组分配好照片 */
  data?: { split?: boolean; record_ids?: number[] }
  model?: string
  degraded?: boolean
  request_plan?: AgentRequestPlan
  /** 长期偏好沉淀建议：模型判定或重复表达检测命中时返回，前端出「记住/不用」卡片 */
  preference_suggestion?: {
    rule: string
    category: string
    reason: 'assistant' | 'repeated'
    evidence?: string
  }
  /** 建档后：缺项列表（占位词也算缺，只提示不拦保存） */
  missing_fields?: Array<{ key: string; label: string; required: boolean; reason: string }>
  /** 建档后：独立会话复核意见（只提意见不改稿，并进同一条回执） */
  review?: {
    items?: Array<{ level: 'must_fix' | 'suggest'; text: string }>
    status?: string
    reason?: string
  }
}

interface AgentRequestPlanClarification {
  field: string
  question: string
  control: string
  options: string[]
  required: boolean
}

interface AgentRequestPlanAction {
  id: string
  confidence: number
  reason: string
}

interface AgentRequestPlan {
  request_id?: string
  schema_version: number
  intent: 'create_record' | 'archive' | 'analyze_hazard' | 'query' | 'generate_document' | 'navigate' | 'undo' | 'chat' | 'clarify'
  actions: AgentRequestPlanAction[]
  record_type: string | null
  record_type_confidence: number
  facts: Array<{ field: string; value: unknown; source: string; status: string; confidence?: number }>
  field_status: Record<string, unknown>
  field_conflicts: Array<{
    field: string
    label?: string
    system_value?: unknown
    system_source?: string
    user_value: unknown
    evidence_values: unknown[]
    evidence_sources?: string[]
    preferred_value: unknown
    preferred_source: string
    resolution: string
    options?: string[]
    needs_confirmation?: boolean
  }>
  item_plan: RequestPlanItemPlan
  needs_hazard_analysis: boolean
  attachment_purposes: string[]
  clarifications: AgentRequestPlanClarification[]
  choice: Array<{ value: string; label: string; kind: string; record_type?: string }>
  selected_tool: string | null
  execution_mode: 'clarify' | 'single_tool' | 'plan_only'
  write_allowed: boolean
  requires_confirmation: boolean
  reason: string
  degraded: boolean
  model?: string
  trace?: Record<string, unknown>
}

interface PurposeRecommendation {
  intent: 'create_record' | 'archive' | 'analyze_hazard' | 'clarify'
  recommendation: { record_type: string; confidence: number; reason: string } | null
  alternatives: string[]
  choices: Array<{ value: string; label: string; kind: string }>
  plan?: AgentRequestPlan
  degraded?: boolean
}

interface ChatHistory {
  id: number
  conversationId?: string
  title: string
  modelId: string
  messages: Message[]
  updatedAt: string
  messageCount?: number
  remote?: boolean
  /** 归属项目：切换项目后本地历史只展示当前项目，避免跨项目串聊天记录。 */
  projectId?: number
}

interface RemoteConversationSummary {
  conversation_id: string
  title: string
  message_count: number
  updated_at: string
  updatedAt: string
}

interface RemoteConversationDetail extends RemoteConversationSummary {
  messages: Array<Record<string, any>>
}

function conversationIdFor(sessionId: number): string {
  return sessionId ? `wx-${sessionId}` : ''
}

/** 服务端 upload_image（/media/images）返回的水印识别结果 */
interface ServerWatermark {
  available?: boolean
  recognized?: boolean
  fields?: Record<string, any>
  labeled_items?: Array<{ label?: string; value?: string }>
  lines?: string[]
}

/** upload_image 响应（只列工作台用到的字段） */
interface UploadedImageResult {
  id: number
  url: string
  watermark?: ServerWatermark
  capture_time?: string
  capture_time_source?: string
}

/** 待发送附件：来自相册/相机/文件的本地临时路径 */
interface Attachment {
  id: string
  kind: 'image' | 'file'
  path: string
  name: string
  ext?: string
  size?: number
  cameraCapture?: boolean
  /** 准备阶段已上传：服务端媒体 id / 远端 url / 水印识别结果（唯一来源），下游复用，不再二次上传或 OCR */
  mediaId?: number
  remoteUrl?: string
  serverWatermark?: ServerWatermark
  captureTime?: string
  captureTimeSource?: string
}

const LEGACY_HISTORY_KEY = `gdjl_${config.storageScope}_chat_history`

function historyStorageKey(userId?: number): string {
  return `${LEGACY_HISTORY_KEY}_${userId || 'anonymous'}`
}

function sessionIdFromConversationId(conversationId: string): number {
  const match = /(\d+)$/.exec(conversationId || '')
  const parsed = match ? Number(match[1]) : 0
  return Number.isSafeInteger(parsed) && parsed > 0 ? parsed : Date.now()
}

// 🔴 2026-10-26（方案①）：原前端关键词指令表 COMMAND_ROUTES / matchCommand / handleCommand 已删除。
// 自由文本「打开XX页」统一交后端 /agent/chat 的 navigate 工具（页面词典 = agent_tools.py _PAGE_ALIASES），
// 后端回执 actions[{type:'navigate', url}] → agentReplyToMessage 映射为 navigate: 指令 → 点击即跳。
// 空态建议卡（语音记录现场工作 / 查规范资料库）由 heroActions → handleActionTap 触发，
// 但它们不是功能页入口：点开先在本页对话里给引导与可点选项，由智能体带用户一步步走完，
// 需要确定性动作（录音/选图）时仍走本地链路，知识问答走 /agent/chat。

/** 工作台「模型」下拉可选的大模型清单。
 * 统一从 services/modelCatalog 引入（与"智能助手"页共用同一份目录与选择器组件）：
 *  - provider='' 走默认文本大模型端点（阿里云 token-plan），用 model 字段覆盖具体模型名；
 *  - provider='huagong' 走华南理工独立供应商（未配置则后端自动回退默认模型）。 */
const CHAT_MODELS = MODEL_CATALOG

interface AtBizItem {
  key: string
  icon: string
  label: string
  desc: string
  ref: string
  /** ref=直接插入引用；folder=可下钻的文件夹；folder-ref=引用整个文件夹；doc=单个文件 */
  kind?: 'ref' | 'folder' | 'folder-ref' | 'doc'
  folderId?: number
  docId?: number
  path?: string
  children?: Array<Record<string, unknown>>
}

/** 工作记录字段候选值：语音里没说清楚的字段，先给常用项让用户点选，不合适再自己填写 */
const FIELD_SUGGESTIONS: Record<string, string[]> = {
  section: ['1号楼', '2号楼', '3号楼', '地下室', '主体结构', '基础工程'],
  location: ['一层', '二层', '三层', '屋面', '基坑', '外墙'],
  work_content: ['钢筋绑扎', '模板安装', '混凝土浇筑', '砌体施工', '防水施工', '脚手架搭设'],
  weather: ['晴', '多云', '阴', '小雨', '中雨', '大雨', '雪'],
  inspection_remark: ['现场情况正常', '无异常', '已口头提醒施工单位'],
  problems: ['未发现问题', '发现轻微问题已当场整改', '存在安全隐患需整改'],
  treatment: ['口头通知整改', '下发监理通知单', '已复查合格'],
  acceptance_type: ['隐蔽工程', '检验批', '进场验收', '分项工程验收', '分部工程验收', '单位工程验收', '竣工验收', '预验收'],
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

const WORK_FIELD_LABELS: Record<string, string> = {"section": "工程部位", "location": "具体位置", "work_content": "现场施工/工作内容", "weather": "天气", "inspection_remark": "巡视备注", "problems": "发现问题", "treatment": "处理措施", "worker_count": "施工人数（正式提交必填）", "inspection_start_time": "巡视开始时间", "inspection_end_time": "巡视结束时间", "record_date": "业务日期", "acceptance_type": "验收类型", "acceptance_content": "验收内容", "acceptance_basis": "验收依据", "acceptance_participants": "实际参验单位及人员", "acceptance_result": "人工确认的验收结论", "station_project_name": "旁站工程名称", "station_location": "旁站关键部位", "station_process": "旁站关键工序", "start_time": "旁站开始时间", "end_time": "旁站结束时间", "station_content": "旁站施工情况", "station_supervision_status": "旁站监理情况", "station_conclusion": "旁站结论", "witness_material_name": "材料名称", "witness_material_spec": "规格型号", "witness_quantity": "取样数量", "witness_unit": "计量单位", "witness_cert_no": "合格证/检测报告编号", "witness_accept_result": "进场验收结论", "manufacturer": "生产厂家", "remark": "备注"}

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
  /** 页面正在卸载（切 tab / 切后台 / 关闭）：此时在飞的请求会被微信中止，
   *  属预期，catch 里据此不打刺眼「调用失败」。 */
  pageUnloading: false,
  /** 用户主动点了「停止生成」（2026-11-19）：区分「用户停」与「真失败」的文案 */
  chatAborted: false,
  /** 在飞的 /agent/chat 的 abort 句柄（停止生成用） */
  activeChatAbort: null as (() => void) | null,
  /** @ 面板搜索防抖计时器（2026-11-19） */
  atSearchTimer: null as number | null,
  /** @ 面板本次是否由「打字打出 @」唤起（2026-11-19）：决定打字同步/删词收起行为 */
  atOpenedByTyping: false,
  /** 历史搜索防抖计时器（2026-11-19） */
  historySearchTimer: null as number | null,

  data: {
    messages: [] as Message[],
    // 消息流滚动锚点：新回执追加后跟到底部（固定值只在首次渲染生效，
    // 长回执会停在开头，用户以为「后面的内容没显示」，2026-11-18 真机）。
    scrollAnchor: 'bottom',
    // 全屏图片预览（带上一张/下一张按钮；替代 wx.previewImage，见组件注释）
    imageViewerVisible: false,
    imageViewerUrls: [] as string[],
    imageViewerCurrent: 0,
    // 空态问候语：按时间段 + 待办/草稿动态生成，比固定文案「活」
    heroGreeting: '您好！今天有什么工作需要我帮您处理?',
    // ima 风格空状态：竖排建议按钮（icon + 文案），点击走 handleActionTap
    // 🔴 2026-11-16 用户口径：只留「开启语音，记录现场工作」「查询规范资料库」两条。
    //    卡片不是跳转页面的功能入口，也不替用户一键执行 —— 点开后在当前聊天里
    //    先讲清步骤、给可点选项，由智能体带用户一步步完成（startVoiceWorkFromChat
    //    / askScopeFromChat）。「拍照识别安全隐患」「生成今日监理日志」已下架。
    // needProject：会产生项目级业务数据的入口对无项目用户隐藏，改由 refreshHeroActions
    //    补「加入项目」引导，避免点一个被拦一个。
    heroActions: [
      { icon: 'ic-mic', text: '开启语音，记录现场工作', command: '工作记录', needProject: true },
      { icon: 'ic-book', text: '查询规范资料库', command: '知识库', needProject: false },
      { icon: 'ic-doc', text: '加入项目（填邀请码）', command: 'join-project', needProject: false, onlyNoProject: true },
    ] as { icon: string; text: string; command: string; needProject: boolean; onlyNoProject?: boolean }[],
    /** 实际渲染的快捷动作：无项目时滤掉写业务的、并补一个「加入项目」入口 */
    visibleHeroActions: [] as { icon: string; text: string; command: string }[],
    input: '',
    loading: false,
    activeProgressIndex: -1,
    activeProgressRequestId: '',
    recording: false,
    speaking: false,
    showHistory: false,
    todoPendingCount: 0,
    // 侧边栏历史对话默认收起，避免抽屉过长
    drawerHistoryExpanded: false,
    historyList: [] as ChatHistory[],
    // 历史搜索（2026-11-19）：全量 + 当前过滤视图
    historyQuery: '',
    historyListAll: [] as ChatHistory[],
    mode: 'chat' as 'chat' | 'hazard',

    showProjectForm: false,
    /** 'create' 新建项目 | 'edit' 修改当前项目概况（侧边栏「项目概况」点进来） */
    projectFormMode: 'create' as 'create' | 'edit',
    projectForm: { projectName: '', location: '', description: '', constructionUnit: '', supervisionUnit: '' },
    projectCreated: false,
    /**
     * 🔴 尚未加入任何项目（自助注册出来的裸用户）。
     *
     * 与 `projectCreated` 是同一事实的两种表述，单独留一个标志是为了让 wxml
     * 能直接用它渲染「引导加入项目」横幅，而不必到处写 `!projectCreated`。
     *
     * 2026-10-21：此前无项目时是**自动弹「创建新项目」表单**，但后端
     * `/project/create` 自 2026-10-10 起要求 `project_create` 权限（仅超管持有），
     * 而超管又不允许登录小程序 ⇒ 那个表单对小程序里**任何人都必然 403**，
     * 点「取消」只是关掉弹窗、不给出路，用户卡死在首页。
     */
    noProject: false,
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
    currentModel: 'deepseek-v4.1-flash-1017',
    modelName: 'DeepSeek V4.1 Flash',
    modelShortName: 'DeepSeek V4.1',
    showModelMenu: false,
    showPlusPanel: false,
    /** @ 提及面板：输入卡里的资料/记录引用选择器（支持文件夹下钻到具体文件） */
    showAtPanel: false,
    atQuery: '',
    atBiz: [] as AtBizItem[],
    atVisibleItems: [] as AtBizItem[],
    atLoading: false,
    /** @ 面板 — 资料库下钻：kbId / 层级栈 / 当前层文件列表 */
    atKbId: 0,
    atDrillStack: [] as Array<{ path: string; items: AtBizItem[] }>,
    atDrillItems: [] as AtBizItem[],
    atDrillPath: '',
    atDrillLoading: false,
    /** 待发送附件：图片/文件可多选先挂在这里，补充文字后一次性发送识别 */
    attachments: [] as Attachment[],
    /**
     * 本会话已上传、但还没关联到任何工作记录的现场照片。
     * 带图那一轮常常只是在追问记录类型，真正建档发生在后面的纯文字轮；
     * attachments 发出即清空，若不在这里留一份，建单时就再也拿不到 mediaId，
     * 照片会变成媒体库里的孤儿，记录详情「影像资料」整块不渲染。
     */
    pendingRecordPhotos: [] as Attachment[],
    /** 登记时的会话 id，用于避免把上个会话的照片误挂到新会话建出的记录 */
    pendingRecordPhotosSession: 0,
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
    const savedModel = String(wx.getStorageSync(`gdjl_${config.storageScope}_agent_model`) || 'deepseek-v4.1-flash-1017')
    const saved = this.data.models.find(m => m.id === savedModel) || this.data.models[0]
    this.setData({
      currentModel: saved.id,
      modelName: saved.name,
    })
    if (user?.project_id) {
      this.setData({ projectCreated: true, noProject: false, projectName: user.project_name || '', currentProjectId: user.project_id })
    } else {
      // 🔴 不再自动弹「创建新项目」表单（该接口仅超管可调用，小程序里必然失败）。
      // 改为：放开对话框让人先用不需要项目的功能（AI 问答、查规范、查天气），
      // 并在顶部挂常驻引导条指向「加入项目」。
      this.setData({ projectCreated: false, noProject: true })
    }
    this.refreshHeroActions()

    const now = new Date()
    this.setData({
      logDate: `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`,
    })

    this.initMessages()
    const openingSessionId = this.currentSessionId
    this.initRecorder()
    if (['inspection', 'station', 'acceptance', 'witness'].includes(options.createRecord || '')) {
      this.openWorkForm(options.createRecord!)
    } else if (options.dispatch === '1') {
      // 🔴 「我的」页派单入口（2026-11-18 方案②）：不弹表单，进入对话引导——
      // 用户口述「派给谁、做什么」，后端 dispatch_task 工具落地并通知对方。
      // 无项目时按 startVoiceWorkFromChat 同款模式弹「去加入项目」引导。
      if (!this.data.projectCreated) {
        this.guideJoinProject('派单会创建任务并通知项目成员')
      } else {
        this.beginDispatchConversation()
      }
    } else {
      void this.restoreLatestConversation(openingSessionId)
    }
  },

  onShow() {
    if (!session.getToken()) {
      wx.redirectTo({ url: '/pages/login/index' })
      return
    }
    // 🔴 重新同步项目状态：用户可能刚去「加入项目」页填了邀请码回来，
    // 此时 session 里的 project_id 已由 joinProjectByInvite 写回，
    // 不在这里刷新的话界面还停在「无项目」态、Hero 动作也不会变。
    const user = session.getUser()
    if (user?.project_id) {
      this.setData({
        projectCreated: true,
        noProject: false,
        projectName: user.project_name || this.data.projectName,
        currentProjectId: user.project_id,
      })
      this.refreshHeroActions()
    } else if (this.data.projectCreated) {
      // 反向：原本有项目但 session 里没了（被移出项目等）→ 退回无项目态
      this.setData({ projectCreated: false, noProject: true, currentProjectId: 0, projectName: '' })
      this.refreshHeroActions()
    }
    this.refreshPendingVoice()
    this.loadPendingDrafts()
    void this.loadTodoPendingCount()
    void this.resumeAgentProgress()
    this.refreshHeroGreeting()
  },

  /** 空态问候语按时间与待办动态生成（纯文案，不引入任何选择器/候选，守 11-06 铁律） */
  refreshHeroGreeting() {
    const h = new Date().getHours()
    const part = h < 6 ? '凌晨好' : h < 9 ? '早上好' : h < 12 ? '上午好' : h < 14 ? '中午好' : h < 18 ? '下午好' : '晚上好'
    let greeting = `${part}！`
    const todo = this.data.todoPendingCount || 0
    const drafts = this.data.pendingDrafts || []
    if (todo > 0) {
      greeting += `你有 ${todo} 项待办没处理，查一下？`
    } else if (drafts.length > 0) {
      greeting += `还有一笔「${drafts[0].label}」没记完，要接着写吗？`
    } else {
      greeting += '今天有什么工作需要我帮您处理?'
    }
    this.setData({ heroGreeting: greeting })
  },

  onHide() {
    this.stopAgentProgressTimers()
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
    this.pageUnloading = true
    this.stopAgentProgressTimers()
    this.saveCurrentSession()
    player.stop()
  },

  toggleAgentProgress(event: WechatMiniprogram.TouchEvent) {
    const index = Number(event.currentTarget.dataset.index)
    const messages = [...this.data.messages]
    if (!messages[index]?.progressRequestId) return
    messages[index] = { ...messages[index], progressExpanded: !messages[index].progressExpanded }
    this.setData({ messages })
  },

  toggleAgentForm(event: WechatMiniprogram.TouchEvent) {
    const messageIndex = Number(event.currentTarget.dataset.message)
    this.updateAgentForm(messageIndex, form => ({ ...form, expanded: !form.expanded }))
  },

  /** 分组建单一次回多张记录卡：默认收起，避免刷屏；点开才展示，单条仍直接显示 */
  toggleAgentCards(event: WechatMiniprogram.TouchEvent) {
    const index = Number(event.currentTarget.dataset.index)
    const messages = [...this.data.messages]
    if (!messages[index]?.cards?.length) return
    messages[index] = { ...messages[index], cardsExpanded: !messages[index].cardsExpanded }
    this.setData({ messages })
  },

  /** 独立复核意见并进同一条回执，默认收起，点开才看（避免多插一个气泡） */
  toggleDocReview(event: WechatMiniprogram.TouchEvent) {
    const index = Number(event.currentTarget.dataset.index)
    const messages = [...this.data.messages]
    if (!messages[index]?.docReview?.items?.length) return
    messages[index] = { ...messages[index], docReviewExpanded: !messages[index].docReviewExpanded }
    this.setData({ messages })
  },

  updateAgentProgressMessage(requestId: string, update: (message: Message) => Message) {
    const index = this.data.messages.findIndex(message => message.progressRequestId === requestId)
    if (index < 0) return -1
    const messages = [...this.data.messages]
    messages[index] = update(messages[index])
    this.setData({ messages })
    return index
  },

  upsertAgentProgressEvent(requestId: string, event: AgentProgressEventView) {
    this.updateAgentProgressMessage(requestId, message => {
      const events = [...(message.progressEvents || [])]
      const existingIndex = events.findIndex(item => item.eventId === event.eventId)
      if (existingIndex >= 0) events[existingIndex] = { ...events[existingIndex], ...event }
      else events.push(event)
      events.sort((left, right) => {
        if (left.sequence > 0 && right.sequence > 0) return left.sequence - right.sequence
        return left.createdAt.localeCompare(right.createdAt)
      })
      const headline = [...events].reverse().find(item => item.status === 'running' || item.status === 'waiting')
        || events[events.length - 1]
      return { ...message, progressEvents: events, progressHeadline: headline?.title || '等待处理事件' }
    })
  },

  async reportAgentProgress(
    requestId: string,
    stage: string,
    status: AgentProgressEventView['status'],
    title: string,
    detail: Record<string, unknown> = {},
    terminal = false,
  ) {
    if (!requestId) return
    const eventId = newOperationId('progress')
    const createdAt = new Date().toISOString()
    const local = progressEventView({
      event_id: eventId, sequence: 0, status, title, detail, created_at: createdAt,
    })
    if (local) this.upsertAgentProgressEvent(requestId, local)
    const write = progressWriteQueue.catch(() => undefined).then(async () => {
      const response = await request<{ event?: Record<string, unknown> | null }>(
        `/agent/requests/${encodeURIComponent(requestId)}/progress`,
        {
          method: 'POST',
          data: { event_id: eventId, stage, status, title, detail, terminal },
          timeout: 10000,
        },
      )
      const persisted = response.event && progressEventView(response.event)
      if (persisted) this.upsertAgentProgressEvent(requestId, persisted)
    })
    progressWriteQueue = write.then(() => undefined, () => undefined)
    await write.catch(() => undefined)
  },

  async pollAgentProgress(requestId: string) {
    if (!requestId || activeProgressPollInFlight) return
    activeProgressPollInFlight = true
    try {
      const response = await request<AgentProgressTraceResponse>(
        `/agent/requests/${encodeURIComponent(requestId)}?after_sequence=${activeProgressSequence}&progress_only=true`,
        { timeout: 10000 },
      )
      const events = (response.progress_events || [])
        .map(progressEventView).filter((item): item is AgentProgressEventView => item !== null)
        .sort((left, right) => left.sequence - right.sequence)
      for (const item of events) {
        this.upsertAgentProgressEvent(requestId, item)
        activeProgressSequence = Math.max(activeProgressSequence, item.sequence)
      }
      activeProgressSequence = Math.max(activeProgressSequence, Number(response.latest_progress_sequence || 0))
      const hasTerminalEvent = (response.progress_events || []).some(item => item.terminal === true)
      const activeTurn = activeProgressRequestId === requestId && this.data.loading
      if (hasTerminalEvent || (!activeTurn && response.status && response.status !== 'running')) {
        // 思考完成：收起为一行摘要（用户仍可点标题栏回看），避免每轮都留一大块展开的思考过程。
        this.updateAgentProgressMessage(requestId, message => ({ ...message, progressTerminal: true, progressExpanded: false }))
        if (activeProgressRequestId === requestId) this.stopAgentProgressTimers()
      }
    } catch (error) {
      const statusCode = (error as { statusCode?: number })?.statusCode
      // 404=服务端无此请求追踪；403=追踪不属于当前用户。两者都意味着这条进度轮次
      // 永远轮询不到（典型：本地/远端遗留的未终态轮次，其追踪未落库到当前后端，或已被清理）。
      // 若不停下，会每 1.2s 打一次 404 刷屏且空耗请求。
      if ((statusCode === 404 || statusCode === 403) && activeProgressRequestId === requestId) {
        const startedAt = this.data.messages.find(m => m.progressRequestId === requestId)?.progressStartedAt || 0
        const elapsed = startedAt ? Date.now() - startedAt : Number.MAX_SAFE_INTEGER
        if (elapsed >= PROGRESS_404_GRACE_MS) {
          this.stopAgentProgressTimers()
          this.updateAgentProgressMessage(requestId, message => ({
            ...message,
            progressTerminal: true,
            progressHeadline: message.progressHeadline || (statusCode === 403 ? '该轮次无权查看' : '该轮次进度记录已失效'),
          }))
          return
        }
      }
      // 其余错误（网络抖动/超时）保持原策略：保留已收到的事件，下一轮从断点续轮询。
    } finally {
      activeProgressPollInFlight = false
    }
  },

  startAgentProgressPolling(requestId: string) {
    this.stopAgentProgressTimers()
    activeProgressRequestId = requestId
    activeProgressSequence = 0
    void this.pollAgentProgress(requestId)
    progressPollTimer = setInterval(() => {
      if (activeProgressRequestId === requestId) void this.pollAgentProgress(requestId)
    }, 1200) as unknown as number
    progressClockTimer = setInterval(() => {
      const startedAt = this.data.messages.find(message => message.progressRequestId === requestId)?.progressStartedAt || Date.now()
      this.updateAgentProgressMessage(requestId, message => ({
        ...message, progressElapsedSeconds: Math.max(0, Math.floor((Date.now() - startedAt) / 1000)),
      }))
    }, 1000) as unknown as number
  },

  stopAgentProgressTimers() {
    if (progressPollTimer !== null) clearInterval(progressPollTimer)
    if (progressClockTimer !== null) clearInterval(progressClockTimer)
    progressPollTimer = null
    progressClockTimer = null
    activeProgressPollInFlight = false
  },

  beginAgentProgress(requestId: string, messageIndex: number, title: string, detail: Record<string, unknown> = {}) {
    if (!requestId || messageIndex < 0) return
    const messages = [...this.data.messages]
    if (!messages[messageIndex]) return
    // 处理过程必须属于助手消息：用户消息右侧只保留提问，步骤插到它后面。
    let progressIndex = messageIndex
    if (messages[progressIndex].role === 'user') {
      progressIndex += 1
      messages.splice(progressIndex, 0, { role: 'assistant', text: '', timeText: formatMessageTime() })
    }
    messages[progressIndex] = {
      ...messages[progressIndex], progressRequestId: requestId, progressEvents: [],
      // 「会思考」体验（2026-11-03）：处理过程中自动展开「思考过程」，让用户实时看到推理步骤在跑；
      // 终态事件到达后由 pollAgentProgress 自动收起为一行摘要（仍可点标题栏回看）。
      // 历史回读保持收起（见 applyConversationDetail），避免一次性展开一长串旧记录。
      progressExpanded: true, progressStartedAt: Date.now(), progressElapsedSeconds: 0,
      progressTerminal: false, progressHeadline: title,
    }
    this.setData({ messages, activeProgressIndex: progressIndex, activeProgressRequestId: requestId })
    this.startAgentProgressPolling(requestId)
    void this.reportAgentProgress(requestId, 'input_received', 'running', title, detail)
  },

  async finishAgentProgress(requestId: string, status: 'completed' | 'failed' | 'waiting' | 'cancelled' | 'skipped', title: string, detail: Record<string, unknown> = {}) {
    await this.reportAgentProgress(requestId, 'turn_result', status, title, detail, true)
    if (activeProgressRequestId === requestId) this.stopAgentProgressTimers()
    this.updateAgentProgressMessage(requestId, message => ({ ...message, progressTerminal: true }))
    this.saveCurrentSession()
  },

  async resumeAgentProgress() {
    if (this.data.loading) {
      const active = [...this.data.messages].reverse().find(message => message.progressRequestId && !message.progressTerminal)
      if (active?.progressRequestId) this.startAgentProgressPolling(active.progressRequestId)
      return
    }
    const pending = [...this.data.messages].reverse().find(message => message.progressRequestId && !message.progressTerminal)
    if (!pending?.progressRequestId) return
    this.startAgentProgressPolling(pending.progressRequestId)
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
        text: '您好，我是广东建设智慧监理智能助手。可以询问规范流程、生成文书、管理隐患等。',
        timeText: formatMessageTime(),
      }],
    })
  },

  /** 登录后恢复当前账号最近一次会话；新对话尚未写入时保持欢迎页。 */
  async restoreLatestConversation(expectedSessionId: number) {
    try {
      const result = await request<{ conversations: RemoteConversationSummary[] }>('/agent/conversations', {
        data: { limit: 1 },
      })
      const latest = result.conversations?.[0]
      if (!latest) return
      const detail = await request<RemoteConversationDetail>(
        `/agent/conversations/${encodeURIComponent(latest.conversation_id)}`,
      )
      if (this.currentSessionId !== expectedSessionId || this.data.messages.some(message => message.role === 'user')) {
        return
      }
      this.applyConversationDetail(detail)
    } catch {
      // 网络不可用时保留本地欢迎页，历史入口仍会回退到本地缓存。
    }
  },

  historyMessageFromRemote(raw: Record<string, any>): Message {
    const response = raw.response as AgentChatResponse | undefined
    if (raw.role === 'assistant' && response && typeof response === 'object') {
      return this.agentReplyToMessage(response)
    }
    const rawImages = Array.isArray(raw.images)
      ? raw.images.filter((u: any) => typeof u === 'string' && u.trim()).slice(0, 9) as string[]
      : []
    return {
      role: raw.role === 'assistant' ? 'assistant' : 'user',
      text: String(raw.text || ''),
      images: rawImages.length ? rawImages.map(u => absoluteMediaUrl(u)) : undefined,
      timeText: raw.created_at || raw.createdAt ? formatMessageTime(raw.created_at || raw.createdAt) : undefined,
    }
  },

  applyConversationDetail(detail: RemoteConversationDetail) {
    const messages = (detail.messages || [])
      .map(message => this.historyMessageFromRemote(message))
    for (let index = 0; index < (detail.messages || []).length; index += 1) {
      const raw = detail.messages[index]
      if (raw?.role !== 'assistant' || !raw.response || typeof raw.response !== 'object') continue
      const requestId = String(raw.response.request_id || '').trim()
      const previous = messages[index - 1]
      if (!requestId || previous?.role !== 'user') continue
      messages[index] = {
        ...messages[index], progressRequestId: requestId, progressEvents: [],
        progressExpanded: false, progressStartedAt: Date.now(), progressTerminal: false,
      }
    }
    const visibleMessages = messages.filter(message =>
      Boolean(message.text || message.images?.length || message.cards?.length || message.agentForm))
    if (!visibleMessages.length) return
    this.currentSessionId = sessionIdFromConversationId(detail.conversation_id)
    this.setData({
      showHistory: false,
      mode: 'chat',
      hazardMode: false,
      messages: visibleMessages,
    })
    void this.loadPendingDrafts()
    void this.restorePendingReviewPlan(detail.conversation_id)
    void this.resumeAgentProgress()
  },

  async restorePendingReviewPlan(conversationId: string) {
    try {
      const plans = await listReviewPlans(conversationId)
      const saved = plans.find(plan => plan.status === 'draft' || plan.status === 'confirmed')
      if (!saved?.serverId || this.data.messages.some(message => message.reviewPlan?.serverId === saved.serverId)) return
      const issueList = reviewIssues(saved)
      this.addMessage({
        role: 'assistant',
        text: issueList.length
          ? `已恢复上次未完成的记录草稿，尚未生成正式工作记录。请先处理：${issueList.join('；')}。`
          : '已恢复上次未完成的记录草稿。请核对后生成正式工作记录。',
        reviewPlan: saved,
        reviewEvents: this.inlineReviewEvents(saved),
        reviewUnassignedEvidence: this.inlineReviewUnassignedEvidence(saved),
        reviewMergeSelection: [],
        reviewEventOptions: this.inlineReviewEventOptions(saved),
        reviewIssues: issueList,
        reviewCanConfirm: issueList.length === 0,
        reviewResult: issueList.length ? '尚未生成正式记录：请先处理上方待确认项。' : '',
        reviewProgress: this.reviewProgress(saved),
      })
    } catch {
      // 草稿恢复是增强能力；失败时不影响已加载的聊天历史。
    }
  },

  initRecorder() {
    recorder.onStop(async (result) => {
      this.setData({ recording: false, loading: true })
      if (this.discardRecording) {
        this.discardRecording = false
        this.setData({ loading: false })
        return
      }
      let handedOffToSend = false
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
          this.setData({ input: transcript.text, loading: false })
          // 🔴 必须先清 loading 再 send：send() 入口用 this.data.loading 做防重入，
          // onStop 开头置的 loading 是转写态，不清掉会让 send() 直接 return（自锁），
          // 语音识别完却永远不自动发送（2026-11-02 修复）。
          // 交接成功后 finally 不再清 loading——AI 回复中的 loading 由 send 下游自管。
          handedOffToSend = true
          try {
            await this.send()
          } catch (sendErr) {
            // 发送失败不应被上面的 catch 误判为「上传失败」而把语音重复入队
            console.warn('语音转写后发送失败', sendErr)
          }
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
        if (!handedOffToSend) this.setData({ loading: false })
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

  async send() {
    const text = this.data.input.trim()
    const attachments = this.data.attachments
    if ((!text && attachments.length === 0) || this.data.loading) return
    // 🔴 2026-10-21：无项目时不再一刀切拦住发送。
    // 原来这里是 `if (!projectCreated) return showToast('请先创建项目')`，
    // 但后端 /agent/chat 对无项目用户其实已经做了正确处理：
    //   · read 类工具（查规范 query_regulation / 查天气 / 查记录，permission=None）
    //     → 无项目照常执行；
    //   · write 类工具（建工作记录/隐患/文书）→ agent.py 直接返回
    //     「你还没有所属项目，先加入项目后再记录」+ 一张「去加入项目」导航卡。
    // 前端把对话框整个禁掉，等于把后端这套降级与引导全埋没了，
    // 导致裸用户连「问一句规范」都做不到。
    // 附件（拍照/文件）必然落到写业务数据的路径，仍需项目 → 单独拦（见下）。
    if (!this.data.projectCreated && attachments.length) {
      this.guideJoinProject('拍照与文件识别会产生工作记录，需要先加入项目')
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

    const userMsg: Message = { role: 'user', text, timeText: formatMessageTime() }
    // 发送时收掉可能开着的 @ 面板（打字唤起后未选直接发送的场景）
    this.setData({ input: '', canSend: false, ...(this.data.showAtPanel ? { showAtPanel: false, atQuery: '' } : {}) })
    this.atOpenedByTyping = false

    // 确定性本地动作：「返回主页」精确匹配 → 重置会话（其余自然语言交后端智能体）
    if (this.tryLocalReset(text, userMsg)) return

    // Pending evidence plan owns short follow-ups such as "巡视"; update that
    // draft in place instead of sending the answer to the general chat router.
    const lastAssistantIndex = [...this.data.messages].map((message, index) => ({ message, index }))
      .reverse().find(({ message }) => message.role === 'assistant')?.index
    const lastAssistant = lastAssistantIndex === undefined ? undefined : this.data.messages[lastAssistantIndex]
    const pendingReviewIndex: number = lastAssistant?.reviewPlan && lastAssistant.reviewPlan.status !== 'executed'
      ? (lastAssistantIndex ?? -1)
      : lastAssistant?.reviewFollowupPlanId
        ? this.data.messages.findIndex(message => message.reviewPlan?.serverId === lastAssistant.reviewFollowupPlanId)
        : -1
    if (pendingReviewIndex >= 0 && !/(取消|放弃|先不记|不用记|换个话题)/.test(text)) {
      await this.applyReviewPurposeReply(pendingReviewIndex, text, userMsg)
      return
    }

    // 上一轮是智能体追问（带快速选项/待补充字段）→ 本句按「答案」处理，直连智能体。
    // 否则「巡视」「晴」这类短答会被知识范围拦截，中断建记录流程。
    const previousAssistant = [...this.data.messages].reverse().find(m => m.role === 'assistant')
    if (previousAssistant && ((previousAssistant.chips && previousAssistant.chips.length) || previousAssistant.hint)) {
      this.handleLlmChat(text, userMsg)
      return
    }

    // 🔴 2026-10-26（方案①）：不再用前端关键词表（matchCommand）猜用户想跳哪个页。
    // 「打开台账」「巡视记录」等自由文本统一交 /agent/chat，由后端 navigate 工具决定
    // 跳哪个页并回 navigate 动作（agentReplyToMessage 已映射为 navigate: 指令，点击即跳）。
    // 消除「前端指令表 + 后端意图路由」双轨漂移；确定性本地动作仍由宫格/按钮直接触发。
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
    const requestId = newOperationId('agent-turn')

    const userMsg: Message = {
      role: 'user',
      text: note || `请识别这${summary}`,
      images: images.map(i => i.path),
      timeText: formatMessageTime(),
    }
    const messages = [...this.data.messages, userMsg] as Message[]
    this.setData({
      input: '',
      attachments: [],
      canSend: false,
      messages,
      loading: true,
    })
    this.beginAgentProgress(requestId, messages.length - 1,
      `收到，我先看看怎么帮你处理`,
      { image_count: images.length, file_count: files.length, text_excerpt: note.slice(0, 500) },
    )
    const userMsgIndex = messages.length - 1

    const blocks: string[] = []
    /** 每张图的原始识别结果：聊天里只展示结论，落库时复用同一份结论（不再调一次视觉模型） */
    const detections: { path: string; raw: any }[] = []
    const watermarks: Array<Record<string, any>> = []
    const observations: string[] = []
    const filePlanEvidence: Array<Record<string, any>> = []
    /** 水印读到的拍摄时间/地点/天气——相机记的比转述可靠，用来预填记录字段 */
    const wmFields: Record<string, string> = {}
    try {
      // 图片统一证据准备：一次上传（服务端随上传完成水印 OCR + EXIF 并返回结果），
      // 外加通用场景观察。是否调用隐患模型由后端请求计划决定，前端不再用关键词
      // 把图片直接送进某个业务分支；也不再前端二次 OCR（水印唯一来源 = upload_image 返回）。
      if (images.length) {
        const results = await Promise.all(images.map(async (img, index) => {
          const imageIndex = index + 1
          void this.reportAgentProgress(requestId, 'evidence_upload', 'running',
            `图片 ${imageIndex}/${images.length}：正在上传；服务端会在同一次请求中提取水印 OCR 和 EXIF`,
            { image_index: imageIndex, image_count: images.length, file_name: img.name },
          )
          void this.reportAgentProgress(requestId, 'watermark_ocr', 'running',
            `图片 ${imageIndex}/${images.length}：等待上传接口返回水印 OCR 结果`,
            { image_index: imageIndex, image_count: images.length, file_name: img.name },
          )
          const uploaded = await this.uploadEvidence(img, 'agent_workbench')
          const wm = this.normalizeWatermark(uploaded)
          const watermarkResult = uploaded?.watermark
          const recognizedFields = Object.entries(watermarkResult?.fields || {})
            .filter(([key, value]) => !key.startsWith('_') && key !== 'labeled_items' && String(value || '').trim())
            .map(([field, value]) => ({ field, value: String(value) }))
          const ocrText = (watermarkResult?.lines || []).map(String).join('\n').slice(0, 1200)
          const ocrStatus = watermarkResult?.available ? 'completed' : 'failed'
          const ocrTitle = watermarkResult?.recognized
            ? `图片 ${imageIndex}/${images.length}：水印 OCR 完成，识别到 ${recognizedFields.length} 个字段`
            : watermarkResult?.available
              ? `图片 ${imageIndex}/${images.length}：水印 OCR 完成，但没有识别到可读字段`
              : `图片 ${imageIndex}/${images.length}：上传接口未提供可用的水印 OCR 结果`
          void this.reportAgentProgress(requestId, 'evidence_upload', uploaded ? 'completed' : 'failed',
            uploaded
              ? `图片 ${imageIndex}/${images.length}：上传完成，媒体编号 ${uploaded.id}`
              : `图片 ${imageIndex}/${images.length}：图片上传失败${((this as any).lastUploadErrorMsg) ? `：${(this as any).lastUploadErrorMsg}` : ''}`,
            { image_index: imageIndex, image_count: images.length, file_name: img.name,
              media_id: uploaded?.id || null, capture_time: uploaded?.capture_time || '',
              capture_time_source: uploaded?.capture_time_source || '' },
          )
          void this.reportAgentProgress(requestId, 'watermark_ocr', ocrStatus, ocrTitle, {
            image_index: imageIndex, image_count: images.length, file_name: img.name,
            recognized_fields: recognizedFields, ocr_text: ocrText,
            capture_time: uploaded?.capture_time || '',
            capture_time_source: uploaded?.capture_time_source || '',
          })

          void this.reportAgentProgress(requestId, 'image_observation', 'running',
            `图片 ${imageIndex}/${images.length}：开始读取现场画面内容`,
            { image_index: imageIndex, image_count: images.length, file_name: img.name },
          )
          const observationResult = await this.observeImageWithStatus(img.path)
          void this.reportAgentProgress(requestId, 'image_observation',
            observationResult.state === 'failed' || observationResult.state === 'unavailable' ? 'failed' : 'completed',
            observationResult.observation
              ? `图片 ${imageIndex}/${images.length}：画面内容读取完成`
              : observationResult.state === 'empty'
                ? `图片 ${imageIndex}/${images.length}：画面读取已完成，没有返回可用描述`
                : `图片 ${imageIndex}/${images.length}：画面内容读取未能完成`,
            {
              image_index: imageIndex, image_count: images.length, file_name: img.name,
              observation: observationResult.observation, error: observationResult.error || '',
            },
          )
          return { path: img.path, wm, observation: observationResult.observation, url: uploaded?.url || '' }
        }))
        watermarks.push(...results.map(result => result.wm))
        observations.push(...results.map(result => result.observation))
        // 会话内显示始终用本地临时路径（设备上的文件，真机必然可渲染，不受 downloadFile
        // 合法域名限制）；服务端 URL 单独存进 remoteImages，用于历史回看兜底（见 onMsgImgError
        // 与 loadHistoryItem 归一化）。此前整条覆盖 images 为远程 URL，真机加载失败时照片直接「没了」。
        const uploadedUrls = results.map(result => result.url).filter(Boolean) as string[]
        if (uploadedUrls.length) {
          const updated = [...this.data.messages]
          if (updated[userMsgIndex]?.role === 'user') {
            updated[userMsgIndex] = { ...updated[userMsgIndex], remoteImages: uploadedUrls.map(absoluteMediaUrl) }
            this.setData({ messages: updated })
          }
        }
        // 部分图片上传失败：给出轻量提示，避免「图发丢了却无任何反馈」。
        if (uploadedUrls.length < images.length) {
          wx.showToast({
            title: uploadedUrls.length
              ? `有 ${images.length - uploadedUrls.length} 张图片上传失败，可能无法识别`
              : '图片上传失败，可能无法识别',
            icon: 'none',
          })
        }
        results.forEach((res, i) => {
          const wmLine = this.formatWatermark(res.wm)
          const observationLine = res.observation ? `【图片内容观察】${res.observation}` : ''
          // 2026-11-06 修复：水印横幅/原始识别文字（如「隐蔽工程验收公示牌」）此前因
          // formatWatermark 只输出 6 个结构化字段而被丢弃，导致服务端推断不出验收子类型、
          // 反过来问用户「这是什么类型」。把原始识别行一并纳入素材（服务端据此关键词匹配）。
          const wmRawLines = Array.isArray((res.wm as Record<string, any>)._lines)
            ? (res.wm as Record<string, any>)._lines
              .filter((t: string) => t && String(t).trim())
              .join(' ')
            : ''
          const wmRawBlock = wmRawLines && !wmLine.includes(wmRawLines)
            ? `【水印识别原文】${wmRawLines}` : ''
          const wmBlock = [wmLine, wmRawBlock].filter(Boolean).join('；')
          blocks.push(wmBlock
            ? `【图片 ${i + 1}】\n${wmBlock}${observationLine ? `\n${observationLine}` : ''}`
            : `【图片 ${i + 1}】${observationLine ? `\n${observationLine}` : '\n已收到图片'}`)
          for (const key of Object.keys(res.wm)) {
            if (res.wm[key] && !wmFields[key]) wmFields[key] = res.wm[key]
          }
        })
      }
      // 文件：逐个解析文本
      for (const file of files) {
        void this.reportAgentProgress(requestId, 'document_parse', 'running',
          `正在解析文件《${file.name}》`,
          { file_name: file.name, file_type: file.ext || '', file_count: files.length },
        )
        try {
          const parsed = await uploadDocument<{ filename?: string; text?: string }>(file.path)
          const content = (parsed.text || '').trim()
          void this.reportAgentProgress(requestId, 'document_parse', content ? 'completed' : 'waiting',
            content ? `文件《${parsed.filename || file.name}》解析完成` : `文件《${parsed.filename || file.name}》已解析，但没有提取到文本`,
            {
              file_name: parsed.filename || file.name, file_type: file.ext || '',
              extracted_characters: content.length, text_excerpt: content.slice(0, 1400),
              status: content ? '文本已提取' : '无可用文本',
            },
          )
          blocks.push(content
            ? `【文件《${parsed.filename || file.name}》】\n${content.slice(0, 3000)}`
            : `【文件《${file.name}》】未能提取到文本内容`)
          filePlanEvidence.push({
            kind: 'file',
            note: `文件《${parsed.filename || file.name}》`,
            observation: content.slice(0, 4000),
          })
        } catch (error) {
          const message = `文件《${file.name}》解析失败：${(error as Error).message}`
          void this.reportAgentProgress(requestId, 'document_parse', 'failed', message, {
            file_name: file.name, file_type: file.ext || '', error: (error as Error).message,
          })
          blocks.push(`【${message}】`)
          filePlanEvidence.push({ kind: 'file', note: message, observation: '' })
        }
      }

      const planEvidence = this.buildPlanEvidence(images, watermarks, observations)
      void this.reportAgentProgress(requestId, 'planning', 'running',
        '正在把用户原话和本轮识别到的证据提交给结构化规划器',
        { image_count: images.length, file_count: files.length, evidence_count: planEvidence.length + filePlanEvidence.length },
      )
      const requestPlan = await this.requestAgentPlan(
        note, [...planEvidence, ...filePlanEvidence], files.length, requestId,
      )
      if (requestPlan.degraded) {
        void this.reportAgentProgress(requestId, 'planning', 'failed',
          '规划服务没有返回可用候选；当前使用保守的待确认结果，未自动创建记录',
          { degraded_reason: String(requestPlan.trace?.degraded_reason || requestPlan.reason || '规划结果不可用'),
            explicit_user_fields: requestPlan.facts.filter(item => item.source === 'user_message_deterministic') },
        )
      }

      if (requestPlan.needs_hazard_analysis && images.length) {
        void this.reportAgentProgress(requestId, 'hazard_analysis', 'running',
          '规划结果要求进行安全隐患分析，正在逐张提交图片进行识别',
          { image_count: images.length },
        )
        const hazardResults = await Promise.all(images.map(async (image, index) => {
          try {
            const result = await this.detectImageOnce(image.path)
            return { path: image.path, result, error: '' }
          } catch (error) {
            return { path: image.path, result: { text: '', raw: null }, error: (error as Error).message }
          }
        }))
        hazardResults.forEach((item, index) => {
          if (item.result.raw) detections.push({ path: item.path, raw: item.result.raw })
          const detectionLine = item.result.text ? `【安全隐患分析】${item.result.text}` : ''
          observations[index] = [observations[index], detectionLine].filter(Boolean).join('\n')
          blocks.push(item.error
            ? `【图片 ${index + 1} 安全隐患分析失败】\n${item.error}`
            : `【图片 ${index + 1} 安全隐患分析】\n${item.result.text}`)
          void this.reportAgentProgress(requestId, 'hazard_analysis', item.error ? 'failed' : 'completed',
            item.error ? `图片 ${index + 1}/${images.length}：安全分析失败` : `图片 ${index + 1}/${images.length}：安全分析完成`,
            { image_index: index + 1, image_count: images.length,
              observation: item.result.text, error: item.error },
          )
          if (requestPlan) requestPlan.attachment_purposes = [...new Set([...requestPlan.attachment_purposes, 'hazard_evidence'])]
        })
      }

      const material = blocks.join('\n\n')
      // 多轮建单补挂：uploadEvidence 已把 mediaId 写回 images，attachments 随后被清空。
      // 这里留一份「已上传未关联」的媒体，若本轮没有建档，后续纯文字轮建出记录时
      // 由 applyAgentReply 统一 /media/link 到该记录（见 attachPendingPhotosFromReply）。
      if (images.length) {
        const uploadedImages = images.filter(img => img.mediaId !== undefined)
        if (uploadedImages.length) {
          this.setData({
            pendingRecordPhotos: [...this.data.pendingRecordPhotos, ...uploadedImages].slice(-9),
            pendingRecordPhotosSession: this.currentSessionId || 0,
          })
        }
      }
      const actionIds = new Set(requestPlan.actions.map(action => action.id))
      const wantsRecordPlan = images.length > 0 && (
        actionIds.has('record_work') || actionIds.has('archive_evidence') ||
        requestPlan.execution_mode === 'clarify'
      )
      if (wantsRecordPlan) {
        // 整理计划执行时会按 evidence 自己把 media 关联到各条记录，且要求 media
        // 尚未关联业务（agent_review_execute.py 会抛 409）；这里若先 /media/link，
        // 整理计划将直接建档失败，所以清空待挂队列、交给计划自己管。
        this.setData({ pendingRecordPhotos: [], pendingRecordPhotosSession: 0 })
        if (detections.length) {
          const reviewMessageIndex = await this.createReviewPlanFromAttachments(note, images, observations, requestPlan, false)
          if (reviewMessageIndex === null) return
          await this.appendHazardCandidateMessage(
            detections,
            images,
            watermarks,
            '工作记录候选与安全隐患候选已分开整理。隐患识别不会自动建档，请分别核对后确认。',
          )
          await this.finishAgentProgress(requestId, 'waiting', '工作记录候选与隐患候选已分别整理，等待用户核对', {
            candidate_count: requestPlan.item_plan?.candidate_count || 0,
            hazard_candidate_count: detections.length,
          })
        } else {
          const reviewMessageIndex = await this.createReviewPlanFromAttachments(note, images, observations, requestPlan)
          if (reviewMessageIndex === null) return
          const savedReview = [...this.data.messages].reverse().find(message => message.reviewPlan)?.reviewPlan
          await this.finishAgentProgress(requestId, 'waiting',
            savedReview?.events?.length
              ? `已保存 ${savedReview.events.length} 个候选事项的待确认草稿，等待用户核对`
              : '资料草稿已保存，记录用途或类型仍需用户确认',
            { event_count: savedReview?.events?.length || 0, evidence_count: savedReview?.evidence?.length || 0 },
          )
        }
        return
      }

      if (requestPlan.needs_hazard_analysis) {
        this.setData({ hazardMode: true, mode: 'hazard' })

        const question = note
          ? `${note}\n\n以下是随消息发送的素材内容（系统已识别，你无需再识别）：\n${material}\n\n请结合以上素材回答我的问题。`
          : `以下是现场发送的素材（图片识别结果、水印与文件内容）：\n${material}\n\n请综合判断是否存在安全隐患或需要监理处理的事项，并给出处理建议。`

        const selModel = this.data.models.find(m => m.id === this.data.currentModel)
        void this.reportAgentProgress(requestId, 'hazard_answer', 'running',
          '正在根据实际识别结果生成安全分析答复',
          { image_count: images.length, file_count: files.length },
        )
        const result = await request<{ answer?: string; reply?: string }>('/local/llm/chat', {
          method: 'POST',
          data: {
            message: question,
            temperature: selModel?.temperature ?? 0.7,
            provider: selModel?.provider || undefined,
            model: selModel?.model || undefined,
            system_prompt: '你是广东建设智慧监理工程监理智能助手，请基于用户提供的图片识别结果与文件内容，给出准确、可执行、简洁的结论与建议。注意：你只负责识别与建议，记录的入库由系统完成，禁止声称「已生成记录」「已保存」「已录入」。',
          },
          timeout: 120000,
        })
        await this.appendHazardCandidateMessage(
          detections,
          images,
          watermarks,
          result.answer || result.reply || '识别未返回内容',
        )
        await this.finishAgentProgress(requestId, 'completed', '安全分析结果已返回工作台', {
          response_excerpt: String(result.answer || result.reply || '').slice(0, 900),
          hazard_candidate_count: detections.length,
        })
        return
      }

      await this.runAgentWithMaterial(
        note || '记录今天的工作', material, this.watermarkPrefill(wmFields), messages, wmFields,
        images.length > 0, images, requestPlan,
      )
    } catch (error) {
      this.addMessage({ role: 'assistant', text: `识别失败：${(error as Error).message}` })
      await this.finishAgentProgress(requestId, 'failed', '本轮资料处理未能完成', { error: (error as Error).message })
    } finally {
      this.setData({ loading: false })
    }
  },

  async createReviewPlanFromAttachments(
    note: string,
    images: Attachment[],
    observations: string[] = [],
    requestPlan?: AgentRequestPlan,
    manageLoading = true,
  ): Promise<number | null> {
    try {
      if (!this.currentSessionId) this.currentSessionId = Date.now()
      const conversationId = conversationIdFor(this.currentSessionId)
      const evidence: ReviewPlan['evidence'] = []
      for (const [index, image] of images.entries()) {
        // 复用准备阶段已上传的媒体（含服务端水印识别结果）；未上传的（异常路径）兜底补传一次
        const alreadyUploaded = image.mediaId !== undefined
        const requestId = String(requestPlan?.request_id || '')
        void this.reportAgentProgress(requestId, 'evidence_upload', 'running',
          alreadyUploaded
            ? `图片 ${index + 1}/${images.length}：复用已上传的媒体编号 ${image.mediaId}`
            : `图片 ${index + 1}/${images.length}：准备补传并登记为本次计划证据`,
          { image_index: index + 1, image_count: images.length, file_name: image.name,
            media_id: image.mediaId || null, reused: alreadyUploaded },
        )
        const uploaded = alreadyUploaded
          ? { id: image.mediaId, url: image.remoteUrl || '', watermark: image.serverWatermark, capture_time: image.captureTime, capture_time_source: image.captureTimeSource } as UploadedImageResult
          : await this.uploadEvidence(image, 'agent_review_plan')
        if (!uploaded) {
          // 带上客户端真实 errMsg。真机 wx.uploadFile 的失败（域名未配 uploadFile 合法域名、
          // 临时文件已被清理、弱网超时）都发生在请求出网之前，服务端零日志无从定位，
          // 只有把 errMsg 透出来才能区分「域名问题」还是「本地文件失效」。
          const reason = String((this as any).lastUploadErrorMsg || '').trim()
          throw new Error(`图片证据上传失败${reason ? `（${reason}）` : ''}，请重试`)
        }
        void this.reportAgentProgress(requestId, 'evidence_upload', 'completed',
          alreadyUploaded
            ? `图片 ${index + 1}/${images.length}：已复用本次计划证据`
            : `图片 ${index + 1}/${images.length}：补传完成并登记为本次计划证据`,
          { image_index: index + 1, image_count: images.length, file_name: image.name,
            media_id: uploaded.id, reused: alreadyUploaded },
        )
        const serverFields = uploaded.watermark?.fields || {}
        const watermark: Record<string, any> = {
          ...serverFields,
          _available: uploaded.watermark?.available === false ? 'false' : uploaded.watermark?.recognized ? 'true' : uploaded.watermark?.available ? 'empty' : 'false',
          _lines: Array.isArray(uploaded.watermark?.lines) ? uploaded.watermark!.lines : [],
          labeled_items: Array.isArray(serverFields.labeled_items) && serverFields.labeled_items.length
            ? serverFields.labeled_items
            : (uploaded.watermark?.labeled_items || []),
        }
        const exifTakenAt = uploaded.capture_time_source === 'exif' ? String(uploaded.capture_time || '') : ''
        const takenAt = String(watermark.taken_at || exifTakenAt)
        const observedDate = this.watermarkDate(takenAt)
        const observedDateSource = watermark.taken_at ? 'watermark' : exifTakenAt ? 'exif' : ''
        const timeSource = watermark.taken_at ? `水印时间：${takenAt}` : exifTakenAt ? `EXIF时间：${takenAt}` : ''
        const activity = [watermark.work_area, watermark.hazard_name].filter(Boolean).join('；')
        const labeledItems = Array.isArray(watermark.labeled_items)
          ? (watermark.labeled_items as Array<{ label?: string; value?: string }>).filter(item => item.label && item.value).map(item => `${item.label}：${item.value}`)
          : []
        const ocrLines = Array.isArray(watermark._lines) ? (watermark._lines as string[]).slice(0, 30).map(line => line.slice(0, 200)).join('；') : ''
        const details = [watermark.location ? `地点：${watermark.location}` : '',
          activity ? `施工区域/危大工程：${activity}` : '', ...labeledItems, timeSource,
          ocrLines ? `OCR文字：${ocrLines}` : '',
          observations[index] ? `图片观察：${observations[index]}` : ''].filter(Boolean).join(' · ')
        const watermarkFields = Object.fromEntries(
          Object.entries(watermark).filter(([key, value]) => !key.startsWith('_') && typeof value === 'string' && key !== 'labeled_items') as [string, string][],
        )
        const watermarkItems = Array.isArray(watermark.labeled_items)
          ? watermark.labeled_items.map((item: { label?: string; value?: string }) => ({ label: String(item.label || ''), value: String(item.value || '') }))
          : Object.entries(watermarkFields).map(([label, value]) => ({ label, value }))
        const knownOcrValues = new Set(watermarkItems.map(item => item.value).filter(Boolean))
        for (const line of Array.isArray(watermark._lines) ? watermark._lines as string[] : []) {
          if (line && !knownOcrValues.has(line)) watermarkItems.push({ label: '识别文字', value: line })
        }
        for (const item of labeledItems) {
          const separator = item.indexOf('：')
          if (separator > 0) watermarkFields[`label:${item.slice(0, separator)}`] = item.slice(separator + 1)
        }
        evidence.push({
          id: String(uploaded.id), mediaId: uploaded.id, observedDateSource,
          image: absoluteMediaUrl(uploaded.url), observedDate,
          project: String(watermark.project || watermark.project_name || ''),
          section: String(watermark.section || watermark.work_area || ''),
          location: watermark.location || '', activity, observation: observations[index] || '',
          watermarkAvailable: watermark._available === 'true', watermarkFields, watermarkItems,
          watermarkStatus: watermark._available === 'true' ? 'recognized' : watermark._available === 'empty' ? 'empty' : 'unavailable',
          note: details || '未读取到水印或EXIF时间信息',
        })
      }
      const purpose = requestPlan
        ? this.purposeFromPlan(requestPlan)
        : await this.requestPurposeRecommendation(note, evidence)
      const notices = (requestPlan?.field_conflicts || []).map(conflict => {
        const label: Record<string, string> = {
          record_date: '日期', project: '项目', location: '地点', section: '部位', weather: '天气',
          owner_unit: '建设单位', construction_unit: '施工单位', supervision_unit: '监理单位',
          supervision_org: '项目监理机构', inspector: '检查人员',
        }
        const candidates = [
          conflict.system_value ? `系统项目资料：${String(conflict.system_value)}` : '',
          conflict.user_value ? `你的描述：${String(conflict.user_value)}` : '',
          conflict.evidence_values?.length ? `照片水印/EXIF：${conflict.evidence_values.map(String).join('、')}` : '',
        ].filter(Boolean)
        const source = conflict.preferred_source === 'watermark_or_exif' || conflict.preferred_source === 'watermark'
          ? '照片水印/EXIF' : conflict.preferred_source === 'system_project'
            ? '系统项目资料' : conflict.preferred_source === 'user_profile' ? '登录用户资料' : '你的描述'
        return `${conflict.label || label[conflict.field] || conflict.field}存在不一致（${candidates.join('；') || '候选值不同'}）。已按${source}的“${String(conflict.preferred_value || '未知')}”自动填写，如需修改可在事项中点选更改。`
      })
      const itemPlan: RequestPlanItemPlan = requestPlan?.item_plan || {
        items: [], candidate_count: 0,
        unassigned_evidence_indexes: evidence.map((_item, index) => index),
        requires_confirmation: false,
        issues: ['规划服务暂不可用，资料保留为待分配证据；请选择整理用途或继续补充。'],
        grouping_fields: [], write_allowed: false,
      }
      const plan = reviewPlanFromItemPlan(
        itemPlan, evidence, note, notices,
        requestPlan ? requestPlan as unknown as Record<string, unknown> : undefined,
      )
      void this.reportAgentProgress(String(requestPlan?.request_id || ''), 'review_plan_save', 'running',
        '正在按当前候选事项和表式字段合同保存待确认草稿',
        { record_type: requestPlan?.record_type || '', candidate_count: itemPlan.candidate_count,
          evidence_count: evidence.length, conflicts: requestPlan?.field_conflicts || [],
          clarifications: requestPlan?.clarifications || [] },
      )
      const saved = await createReviewPlan(
        conversationId, plan, requestPlan?.request_id,
        images.map(image => image.remoteUrl).filter((u): u is string => Boolean(u)),
      )
      const issueList = reviewIssues(saved)
      const messageIndex = this.addMessage({
        role: 'assistant',
        text: issueList.length
          ? `已整理 ${evidence.length} 张图片${saved.events.length ? `，形成 ${saved.events.length} 个候选事项` : '，还没有确定记录用途'}。当前已保存为待确认草稿，尚未生成正式工作记录。${this.reviewFollowupText(saved)}`
          : `已整理 ${evidence.length} 张图片${saved.events.length ? `，形成 ${saved.events.length} 个候选事项` : '，还没有确定记录用途'}。信息已准备好，确认后才会写入正式工作记录。`,
        reviewPlan: saved,
        reviewEvents: this.inlineReviewEvents(saved, []),
        reviewUnassignedEvidence: this.inlineReviewUnassignedEvidence(saved),
        reviewMergeSelection: [],
        reviewEventOptions: this.inlineReviewEventOptions(saved),
        reviewIssues: issueList,
        reviewCanConfirm: issueList.length === 0,
        reviewResult: issueList.length ? '尚未生成正式记录：请先处理上方待确认项。' : '',
        reviewProgress: this.reviewProgress(saved),
        reviewPurposeOptions: saved.events.length ? undefined : (purpose.choices || []).map(choice => ({ value: choice.value, label: choice.label })),
        reviewRecommendation: purpose.recommendation ? { recordType: purpose.recommendation.record_type, confidence: purpose.recommendation.confidence, reason: purpose.recommendation.reason } : undefined,
        reviewSummary: [
          notices.join(' '),
      evidence.some(item => item.watermarkStatus === 'unavailable') ? '部分图片的水印识别服务暂不可用；相关信息保持未知，不会用上传时间代替。' :
        evidence.some(item => item.watermarkStatus === 'empty') ? '部分图片未识别到水印字段；日期、地点、施工区域、参建单位及表单信息可在记录草稿中核对补充。' : '已读取照片水印字段，识别结果仅作为候选证据，提交前请核对。',
          observations.some(value => !value) ? '部分图片内容观察不可用；其余用户文字和水印证据仍保留。' : '已完成通用图片内容观察，未调用隐患专用识别。',
        ].filter(Boolean).join(' '),
      })
      if (requestPlan?.request_id) {
        void this.reportAgentProgress(requestPlan.request_id, 'review_plan_save', 'completed',
          `待确认草稿已保存，当前版本 ${saved.serverRevision || saved.revision}`,
          { plan_id: saved.serverId || '', revision: saved.serverRevision || saved.revision,
            event_count: saved.events.length, evidence_count: saved.evidence.length,
            missing_fields: issueList },
        )
      }
      return messageIndex
    } catch (error) {
      const restored = this.restoreAttachments(images)
      this.addMessage({ role: 'assistant', text: `整理计划创建失败：${(error as Error).message}。${restored
        ? `已把 ${restored} 张图片放回输入区，直接回复「继续记录工作」即可重试，不用重新选图。`
        : '已上传的资料不会自动创建业务记录，请补充后重试。'}` })
      if (requestPlan?.request_id) {
        await this.finishAgentProgress(requestPlan.request_id, 'failed', '待确认草稿保存失败', { error: (error as Error).message })
      }
      return null
    } finally {
      if (manageLoading) this.setData({ loading: false })
    }
  },

  /**
   * 草稿保存失败时把本轮图片放回输入区。
   * 服务端没落库、客户端又已清空 attachments 时，用户下一句「继续记录工作」
   * 就会变成既没有上下文也没有图片的空转；这里先保住客户端证据。
   */
  restoreAttachments(items: Attachment[]): number {
    if (!items.length) return 0
    const existingPaths = new Set(this.data.attachments.map(item => item.path))
    const existingMedia = new Set(
      this.data.attachments.map(item => item.mediaId).filter(value => value !== undefined),
    )
    const restored = items.filter(item =>
      !existingPaths.has(item.path)
      && !(item.mediaId !== undefined && existingMedia.has(item.mediaId)))
    const attachments = [...restored, ...this.data.attachments].slice(0, 9)
    this.setData({
      attachments,
      canSend: Boolean(this.data.input.trim()) || attachments.length > 0,
    })
    return Math.min(restored.length, 9)
  },

  watermarkDate(value: string): string {
    const matched = String(value || '').match(/(\d{4})\s*[-/.年]\s*(\d{1,2})\s*[-/.月]\s*(\d{1,2})/)
    if (!matched) return ''
    const year = Number(matched[1]), month = Number(matched[2]), day = Number(matched[3])
    const date = new Date(Date.UTC(year, month - 1, day))
    if (date.getUTCFullYear() !== year || date.getUTCMonth() !== month - 1 || date.getUTCDate() !== day) return ''
    return `${matched[1]}-${matched[2].padStart(2, '0')}-${matched[3].padStart(2, '0')}`
  },

  /** Serialize image facts for the server-side request planner. */
  buildPlanEvidence(
    images: Attachment[],
    watermarks: Array<Record<string, any>>,
    observations: string[] = [],
  ): Array<Record<string, any>> {
    return images.map((_image, index) => {
      const watermark = watermarks[index] || {}
      const watermarkFields = Object.fromEntries(
        Object.entries(watermark).filter(([key, value]) => !key.startsWith('_') && typeof value === 'string') as [string, string][],
      )
      const takenAt = String(watermark.taken_at || '')
      const fieldSources: Record<string, string> = {}
      if (this.watermarkDate(takenAt)) fieldSources.record_date = 'watermark'
      if (watermark.project || watermark.project_name) fieldSources.project = 'watermark'
      if (watermark.location) fieldSources.location = 'watermark'
      if (watermark.section || watermark.work_area) fieldSources.section = 'watermark'
      if (watermark.weather) fieldSources.weather = 'watermark'
      return {
        kind: 'image',
        observed_date: this.watermarkDate(takenAt),
        observed_date_source: takenAt ? 'watermark' : '',
        project: String(watermark.project || watermark.project_name || ''),
        location: String(watermark.location || ''),
        section: String(watermark.section || watermark.work_area || ''),
        activity: [watermark.work_area, watermark.hazard_name].filter(Boolean).join('；'),
        observation: observations[index] || '',
        note: [this.formatWatermark(watermark), observations[index] ? `图片观察：${observations[index]}` : ''].filter(Boolean).join('；'),
        watermark_fields: watermarkFields,
        field_sources: fieldSources,
      }
    })
  },

  async requestAgentPlan(
    note: string,
    evidence: Array<Record<string, any>> = [],
    fileCount = 0,
    requestId = newOperationId('request-plan'),
  ): Promise<AgentRequestPlan> {
    const selected = this.data.models.find(model => model.id === this.data.currentModel)
    const history = this.data.messages
      .filter(message => message.role === 'user' || message.role === 'assistant')
      .slice(-8)
      .map(message => ({ role: message.role, text: message.text }))
    try {
      const plan = await request<AgentRequestPlan>('/agent/request-plan', {
        method: 'POST',
        data: {
          message: note,
          request_id: requestId,
          evidence: evidence.slice(0, 20),
          attachment_count: evidence.filter(item => item.kind !== 'file').length,
          file_count: fileCount,
          history,
          provider: selected?.provider,
          model: selected?.model,
        },
        timeout: 60000,
      })
      plan.request_id = plan.request_id || requestId
      return plan
    } catch {
      return {
        request_id: requestId,
        schema_version: 2,
        intent: 'clarify',
        actions: [],
        record_type: null,
        record_type_confidence: 0,
        facts: [],
        field_status: {},
        field_conflicts: [],
        item_plan: {
          items: [], candidate_count: 0, unassigned_evidence_indexes: [],
          requires_confirmation: false, issues: [], grouping_fields: [], write_allowed: false,
        },
        needs_hazard_analysis: false,
        attachment_purposes: [],
        clarifications: [{
          field: 'purpose',
          question: '这些资料要怎么处理？',
          control: 'select',
          options: ['整理工作记录', '检查安全隐患', '仅归档'],
          required: true,
        }],
        choice: [
          { value: 'record_type:巡视', label: '巡视记录', kind: 'record', record_type: '巡视' },
          { value: 'record_type:旁站', label: '旁站记录', kind: 'record', record_type: '旁站' },
          { value: 'record_type:平行检验', label: '平行检验记录', kind: 'record', record_type: '平行检验' },
          { value: 'record_type:验收', label: '验收记录', kind: 'record', record_type: '验收' },
          { value: 'record_type:见证', label: '见证记录', kind: 'record', record_type: '见证' },
          { value: 'record_type:起重机械安拆检查', label: '起重机械安拆检查记录', kind: 'record', record_type: '起重机械安拆检查' },
          { value: 'archive_only', label: '仅整理归档', kind: 'archive' },
        ],
        selected_tool: null,
        execution_mode: 'clarify',
        write_allowed: false,
        requires_confirmation: false,
        reason: 'planner_unavailable',
        degraded: true,
      }
    }
  },

  purposeFromPlan(plan: AgentRequestPlan): PurposeRecommendation {
    const actionIds = new Set(plan.actions.map(action => action.id))
    const intent: PurposeRecommendation['intent'] = actionIds.has('record_work')
      ? 'create_record'
      : actionIds.has('analyze_hazard')
        ? 'analyze_hazard'
        : actionIds.has('archive_evidence')
          ? 'archive'
          : 'clarify'
    return {
      intent,
      recommendation: plan.record_type ? {
        record_type: plan.record_type,
        confidence: plan.record_type_confidence,
        reason: plan.reason,
      } : null,
      alternatives: [],
      choices: plan.choice.length ? plan.choice : [],
      plan,
      degraded: plan.degraded,
    }
  },

  async requestPurposeRecommendation(note: string, evidence: ReviewPlan['evidence']): Promise<PurposeRecommendation> {
    const selected = this.data.models.find(model => model.id === this.data.currentModel)
    const evidenceSummary = evidence.map((item, index) => `资料${index + 1}：${item.note || item.observation || ''}`).join('\n')
    return request<PurposeRecommendation>('/agent/recommend-purpose', {
      method: 'POST', data: { message: note, evidence_summary: evidenceSummary, attachment_count: evidence.length, provider: selected?.provider, model: selected?.model }, timeout: 60000,
    })
  },

  inlineReviewEvents(plan: ReviewPlan, selectedIds: string[] = []) {
    return plan.events.map(event => ({
      id: event.id, title: event.title, date: event.date, dateConfirmed: event.dateConfirmed,
      dateOptions: [...new Set(plan.evidence.filter(item => event.evidenceIds.includes(item.id)).map(item => item.observedDate).filter(Boolean))],
      dateSource: event.dateSource || (event.date ? 'evidence' : ''),
      recordType: event.recordType || '待定', recordTypeConfidence: event.recordTypeConfidence || 0,
      recordTypeConfidenceText: `${Math.round((event.recordTypeConfidence || 0) * 100)}%`,
      recordTypeReason: event.recordTypeReason || '',
      recordTypeOptions: INLINE_RECORD_TYPES.map(label => ({ label, checked: (event.recordType || '待定') === label })),
      evidenceCount: event.evidenceIds.length,
        evidenceFields: event.evidenceIds.map(id => plan.evidence.find(item => item.id === id)).filter(Boolean).map(item => ({
          id: item!.id, image: item!.image,
          status: item!.watermarkStatus === 'recognized' ? '已识别，核对后可修改' : item!.watermarkStatus === 'empty' ? '没有识别到水印字段，可手动补充' : '水印识别不可用，可手动补充',
          items: (item!.watermarkItems || Object.entries(item!.watermarkFields || {}).map(([label, value]) => ({ label, value }))).map((field, index) => ({ label: field.label, value: field.value, index })),
        })),
      location: event.location || '',
      locationSource: event.fieldSources?.location || '',
      section: event.section || '',
      sectionSource: event.fieldSources?.section || '',
      formCode: event.formCode || '',
      draftStatus: event.recordDraft?.status || 'needs_record_type',
      draftStatusLabel: event.recordDraft?.status === 'ready_for_confirmation'
        ? '字段已齐，待最终确认'
        : event.recordDraft?.status === 'needs_resolution'
          ? '待补充或处理冲突'
          : event.recordDraft?.status === 'needs_user_confirmation'
            ? '待用户确认记录类型'
            : '待确定记录类型',
      contractVersion: event.recordDraft?.contractVersion || '',
      generationAllowed: event.recordDraft?.generationAllowed === true,
      formFields: (event.formFields || []).map(field => ({ ...field, options: field.options || [] })),
      missingFields: (event.missingFields || []).map(field => (
        event.formFields?.find(item => item.canonical === field)?.label
        || ({
          record_date: '业务日期', project: '项目', location: '地点', section: '施工部位', title: '工作名称', content: '工作描述',
        } as Record<string, string>)[field]
        || field
      )),
      issues: event.issues || [],
      entityFieldRows: [
        ['project_name', '项目名称'], ['owner_unit', '建设单位'], ['construction_unit', '施工单位'],
        ['supervision_unit', '监理单位'], ['supervision_org', '项目监理机构'],
        ['inspector', '检查人员'], ['weather', '天气'], ['problems', '问题及处理'],
        ['treatment', '处理措施'],
      ].map(([field, label]) => ({
        field,
        label,
        value: event.entityFields?.[field] || '',
        values: [...new Set([
          event.entityFields?.[field] || '',
          ...(event.fieldCandidates?.[field] || []),
        ].filter(Boolean))],
        conflict: (event.unresolvedConflicts || []).includes(field),
      })).filter(row => row.value || row.values.length),
      hasUnresolvedConflicts: Boolean(event.unresolvedConflicts?.length),
      mergeSelected: selectedIds.includes(event.id),
      evidenceSummary: [...new Set(plan.evidence.filter(item => event.evidenceIds.includes(item.id)).map(item => item.note).filter(Boolean))].join('；'),
      watermarkSummary: [...new Set(plan.evidence.filter(item => event.evidenceIds.includes(item.id)).flatMap(item => Object.entries(item.watermarkFields || {}).map(([key, value]) => `${key}：${value}`)))].join('；'),
      content: event.content || '',
      sourceLabels: [...new Set(event.evidenceIds.map(id => plan.evidence.find(item => item.id === id)?.observedDateSource || '').filter(Boolean).map(source => source === 'watermark' ? '照片水印' : source === 'exif' ? 'EXIF' : source))],
      sourceText: [...new Set(event.evidenceIds.map(id => plan.evidence.find(item => item.id === id)?.observedDateSource || '').filter(Boolean).map(source => source === 'watermark' ? '照片水印' : source === 'exif' ? 'EXIF' : source))].join('、'),
    }))
  },

  reviewFollowupText(plan: ReviewPlan): string {
    const needs: string[] = []
    if (!plan.events.length) needs.push('还没有确定要创建的工作事项，请补充工作内容或选择“记录工作”用途')
    for (const [index, event] of plan.events.entries()) {
      if (!event.recordType || event.recordType === '待定') needs.push(`事项${index + 1}：请先确认记录类型`)
      for (const field of event.formFields || []) {
        if (field.conflict) {
          const choices = [...new Set((field.candidates || []).map(item => item.value).filter(Boolean))]
          needs.push(`${field.label}有冲突${choices.length ? `（${choices.join(' / ')}）` : ''}，请选一个或填写正确值`)
        } else if (field.required && field.missing) {
          needs.push(`${field.label}${field.question ? `：${field.question}` : ''}`)
        }
      }
      for (const conflict of event.unresolvedConflicts || []) {
        if (!(event.formFields || []).some(field => field.canonical === conflict && field.conflict)) {
          needs.push(`${conflict}存在候选冲突，请确认采用哪个值`)
        }
      }
    }
    if (plan.unassignedIds.length) needs.push(`${plan.unassignedIds.length} 张图片还未分配到事项`)
    return needs.length
      ? `目前还不能生成正式记录。请先补充或确认：${[...new Set(needs)].slice(0, 8).join('；')}${needs.length > 8 ? '；其他字段也请在草稿中检查' : ''}。你可以直接回复，也可以在表单字段里修改；我会重新核对。`
      : '当前表单字段已通过检查。请核对事项和字段内容，再点击“确认并生成工作记录”。'
  },

  inlineReviewEventOptions(plan: ReviewPlan) {
    return plan.events.map((event, index) => ({
      id: event.id,
      title: `${index + 1}. ${event.title}`,
    }))
  },

  inlineReviewUnassignedEvidence(plan: ReviewPlan) {
    return plan.unassignedIds.map(id => plan.evidence.find(item => item.id === id)).filter(Boolean).map(item => ({
      id: item!.id, image: item!.image, note: item!.note,
      status: item!.watermarkStatus === 'recognized' ? '水印已识别（候选信息）' : item!.watermarkStatus === 'empty' ? '未读到水印字段' : '水印识别暂不可用',
      items: item!.watermarkItems || Object.entries(item!.watermarkFields || {}).map(([label, value]) => ({ label, value })),
    }))
  },

  async applyReviewPurposeReply(index: number, reply: string, userMessage?: Message) {
    const message = this.data.messages[index]
    const current = message?.reviewPlan
    if (!current) return
    const requestId = newOperationId('agent-turn')
    const userMessageIndex = userMessage ? this.addMessage(userMessage) : -1
    this.setData({ loading: true })
    this.beginAgentProgress(requestId, userMessageIndex,
      `收到你的补充，我核对一下草稿`,
      { plan_id: current.serverId || '', revision: current.serverRevision || current.revision,
        text_excerpt: reply.slice(0, 700) },
    )
    let selectedType = reply.startsWith('record_type:') ? reply.slice('record_type:'.length) : ''
    let archiveOnly = reply === 'archive_only'
    const priorRequest = current.requestText === '用户仅上传图片，尚未说明整理用途' ? '' : current.requestText
    const plannerMessage = selectedType
      ? `请整理为${selectedType}记录。${priorRequest}`.trim()
      : [priorRequest, reply].filter(Boolean).join('\n')
    const plannerEvidence = current.evidence.map(item => ({
      kind: 'image',
      observed_date: item.observedDate,
      observed_date_source: item.observedDateSource || '',
      project: item.project || '',
      location: item.location || '',
      section: item.section || '',
      activity: item.activity || '',
      observation: item.observation || '',
      note: item.note || '',
      watermark_fields: item.watermarkFields || {},
      field_sources: {
        ...(item.observedDate && item.observedDateSource ? { record_date: item.observedDateSource } : {}),
        ...(item.project ? { project: 'watermark' } : {}),
        ...(item.location ? { location: 'watermark' } : {}),
        ...(item.section ? { section: 'watermark' } : {}),
      },
    }))
    void this.reportAgentProgress(requestId, 'planning', 'running',
      '正在结合当前草稿证据和本轮补充重新判断记录类型及字段候选',
      { evidence_count: plannerEvidence.length, previous_event_count: current.events.length },
    )
    const planner = !archiveOnly ? await this.requestAgentPlan(plannerMessage, plannerEvidence, 0, requestId) : null
    if (!selectedType && !archiveOnly && current.events.length === 0) {
      const purpose = planner ? this.purposeFromPlan(planner) : await this.requestPurposeRecommendation(`${current.requestText}\n${reply}`, current.evidence)
      if (purpose.intent === 'archive') archiveOnly = true
      if (purpose.intent === 'create_record' && purpose.recommendation) {
        const messages = [...this.data.messages]
        messages[index] = { ...message,
          reviewRecommendation: { recordType: purpose.recommendation.record_type, confidence: purpose.recommendation.confidence, reason: purpose.recommendation.reason },
          reviewPurposeOptions: purpose.choices.map(choice => ({ value: choice.value, label: choice.label })),
        }
        this.setData({ messages })
        this.addMessage({ role: 'assistant', text: `结合你刚才的补充，我建议整理为「${purpose.recommendation.record_type}」记录。请点“采用建议”确认类型，或选择其他类型；也可以继续补充描述。` })
        await this.finishAgentProgress(requestId, 'waiting', '已给出记录类型建议，等待用户选择或继续补充', {
          proposed_record_type: purpose.recommendation.record_type,
          confidence: purpose.recommendation.confidence, reason: purpose.recommendation.reason,
        })
        this.setData({ loading: false })
        return
      }
      if (purpose.intent !== 'archive') {
        this.addMessage({ role: 'assistant', text: '我还不能可靠判断这批资料的用途。你可以从下面的事项/记录类型中选择，也可以补充说明要完成的工作。' })
        await this.finishAgentProgress(requestId, 'waiting', '当前证据不足以确定用途，等待用户选择或补充', {
          choices: purpose.choices.map(choice => choice.label),
        })
        this.setData({ loading: false })
        return
      }
    }
    const type = selectedType ? { type: selectedType, confidence: 1, reason: '用户已选择记录类型' } : { type: '待定', confidence: 0, reason: '' }
    const messages = [...this.data.messages]
    const revisedPlan: ReviewPlan = { ...current, revision: current.revision + 1, confirmedRevision: null,
      requestText: [priorRequest, reply].filter(Boolean).join('\n'), requestId }
    if (type.type === '待定' && archiveOnly) {
      revisedPlan.notices = [...(current.notices || []), '用户选择仅保存/整理图片，不创建工作记录。']
    } else if (planner?.item_plan?.items?.length) {
      const generated = reviewPlanFromItemPlan(planner.item_plan, current.evidence, revisedPlan.requestText, current.notices || [], planner as unknown as Record<string, unknown>)
      revisedPlan.events = generated.events.map(event => {
        const previous = current.events.find(item => item.evidenceIds.length === event.evidenceIds.length
          && item.evidenceIds.every((id, evidenceIndex) => id === event.evidenceIds[evidenceIndex]))
        const mergedFields: Record<string, Record<string, unknown>> = { ...(previous?.fields || {}) }
        for (const [formCode, fields] of Object.entries(event.fields || {})) {
          mergedFields[formCode] = { ...(mergedFields[formCode] || {}), ...(fields || {}) }
        }
        const mergedEntityFields = { ...(previous?.entityFields || {}), ...(event.entityFields || {}) }
        const mergedFieldSources = { ...(previous?.fieldSources || {}), ...(event.fieldSources || {}) }
        const preserveType = type.type === '待定' && previous?.recordType && previous.recordType !== '待定'
        return {
          ...event,
          location: event.location || previous?.location || '',
          section: event.section || previous?.section || '',
          date: event.date || previous?.date || '',
          dateConfirmed: event.dateConfirmed || previous?.dateConfirmed === true,
          content: event.content || previous?.content || '',
          recordType: type.type !== '待定' ? type.type : (preserveType ? previous!.recordType! : event.recordType),
          recordTypeConfirmed: type.type !== '待定' ? true : (preserveType ? previous!.recordTypeConfirmed === true : event.recordTypeConfirmed === true),
          recordTypeConfidence: type.type !== '待定' ? type.confidence : (preserveType ? previous!.recordTypeConfidence || 0 : event.recordTypeConfidence),
          recordTypeReason: type.type !== '待定' ? '用户已选择记录类型' : (preserveType ? previous!.recordTypeReason || '' : event.recordTypeReason),
          forms: previous?.forms?.length ? previous.forms : event.forms,
          fields: mergedFields,
          fieldSources: mergedFieldSources,
          entityFields: mergedEntityFields,
          fieldCandidates: { ...event.fieldCandidates, ...(previous?.fieldCandidates || {}) },
          unresolvedConflicts: [...new Set([
            ...(event.unresolvedConflicts || []),
            ...(previous?.unresolvedConflicts || []),
          ])].filter(field => !previous?.entityFields?.[field] || previous?.unresolvedConflicts?.includes(field)),
        }
      })
      revisedPlan.unassignedIds = generated.unassignedIds
      revisedPlan.notices = generated.notices
      revisedPlan.requestPlan = generated.requestPlan
    } else if (current.events.length) {
      revisedPlan.notices = [...(current.notices || []), '服务端未能可靠重构这句话对应的事项；当前草稿未被覆盖，请补充日期、地点或工作内容后重试。']
    }
    void this.reportAgentProgress(requestId, 'review_plan_revision', 'running',
      '正在向服务端提交草稿修订，并重新校验当前表式合同',
      { record_type: type.type, previous_revision: current.serverRevision || current.revision,
        event_count: revisedPlan.events.length, unresolved_conflicts: revisedPlan.events.flatMap(event => event.unresolvedConflicts || []) },
    )
    try {
      const saved = await saveReviewPlan(revisedPlan, current.serverRevision || current.revision)
      messages[index] = { ...message,
        reviewPlan: saved, reviewEvents: this.inlineReviewEvents(saved), reviewUnassignedEvidence: this.inlineReviewUnassignedEvidence(saved), reviewEventOptions: this.inlineReviewEventOptions(saved),
        reviewIssues: reviewIssues(saved), reviewProgress: this.reviewProgress(saved), reviewPurposeOptions: saved.events.length ? undefined : message.reviewPurposeOptions }
      messages[index].reviewCanConfirm = reviewIssues(saved).length === 0
      this.setData({ messages })
      this.addMessage({ role: 'assistant', text: archiveOnly
        ? '已保留图片及识别到的证据，不创建工作记录。之后也可以继续补充文字、语音或图片。'
        : `已把“${reply}”并入当前草稿。${saved.events.length ? `由服务端重新核对 ${saved.events.length} 个工作事项。` : ''}${this.reviewFollowupText(saved)}`, reviewFollowupPlanId: saved.serverId })
      await this.finishAgentProgress(requestId, archiveOnly ? 'completed' : 'waiting',
        archiveOnly ? '已更新归档整理结果，不创建工作记录' : '草稿修订及服务端字段合同复核已完成，等待继续补充或确认',
        { plan_id: saved.serverId || '', revision: saved.serverRevision || saved.revision,
          record_type: type.type, event_count: saved.events.length, missing_fields: reviewIssues(saved) },
      )
    } catch (error) {
      this.addMessage({ role: 'assistant', text: `当前草稿更新失败：${(error as Error).message}` })
      await this.finishAgentProgress(requestId, 'failed', '当前草稿修订或服务端校验失败', { error: (error as Error).message })
    }
    this.setData({ loading: false })
  },

  reviewProgress(plan: ReviewPlan) {
    const hasEvidence = plan.evidence.length > 0
    const hasEvents = plan.events.length > 0
    const done = plan.status === 'executed'
    return [
      { label: '收到资料', state: hasEvidence ? 'done' : 'current' },
      { label: '提取证据', state: hasEvidence ? 'done' : 'pending' },
      { label: '归纳事项', state: hasEvents ? 'done' : hasEvidence ? 'current' : 'pending' },
      { label: done ? '已建档' : '待确认', state: done ? 'done' : hasEvents ? 'current' : 'pending' },
    ] as Array<{ label: string; state: 'done' | 'current' | 'pending' }>
  },

  updateInlineReview(index: number, candidate: ReviewPlan) {
    const messages = [...this.data.messages]
    const selected = (messages[index].reviewMergeSelection || []).filter(id => candidate.events.some(event => event.id === id))
    const issues = reviewIssues(candidate)
    const message = { ...messages[index], reviewPlan: candidate, reviewEvents: this.inlineReviewEvents(candidate, selected), reviewUnassignedEvidence: this.inlineReviewUnassignedEvidence(candidate), reviewMergeSelection: selected, reviewEventOptions: this.inlineReviewEventOptions(candidate), reviewIssues: issues, reviewCanConfirm: issues.length === 0, reviewProgress: this.reviewProgress(candidate), reviewResult: '' }
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
      const issueList = reviewIssues(plan)
      const selected = (latest.reviewMergeSelection || []).filter(id => plan.events.some(event => event.id === id))
      latestMessages[index] = { ...latest, reviewPlan: plan, reviewEvents: this.inlineReviewEvents(plan, selected), reviewUnassignedEvidence: this.inlineReviewUnassignedEvidence(plan), reviewMergeSelection: selected, reviewEventOptions: this.inlineReviewEventOptions(plan), reviewIssues: issueList, reviewCanConfirm: issueList.length === 0, reviewProgress: this.reviewProgress(plan) }
      this.setData({ messages: latestMessages })
    })
    this.inlinePlanSaveQueues[key] = task
    return task
  },

  confirmInlineReview(event: WechatMiniprogram.TouchEvent) {
    const index = Number(event.currentTarget.dataset.index)
    void this.confirmAndExecuteInlineReview(index)
  },

  async confirmAndExecuteInlineReview(index: number): Promise<boolean> {
    const message = this.data.messages[index]
    let plan = message?.reviewPlan
    if (!plan || message.reviewBusy) return false
    const issues = reviewIssues(plan)
    if (issues.length) {
      wx.showToast({ title: issues[0], icon: 'none' })
      const blockedMessages = [...this.data.messages]
      blockedMessages[index] = {
        ...blockedMessages[index],
        reviewResult: `尚未生成正式工作记录：${issues[0]}`,
      }
      this.setData({ messages: blockedMessages })
      return false
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
      savedMessages[index] = { ...savedMessages[index], reviewPlan: confirmed, reviewEvents: this.inlineReviewEvents(confirmed), reviewProgress: this.reviewProgress(confirmed) }
      this.setData({ messages: savedMessages })
      const executed = await executeReviewPlan(confirmed, confirmed.serverRevision || confirmed.revision)
      const finalMessages = [...this.data.messages]
      const records = executed.executionResult?.records || []
      const summary = records.map(record => `${record.record_type || '待定'} ${record.record_date || ''}（#${record.record_id}）`).join('、')
      const hasParallel = records.some(record => record.business_type === 'parallel_inspection')
      const hasWorkRecord = records.some(record => record.business_type !== 'parallel_inspection')
      const nextStep = [
        hasWorkRecord ? '工作记录可在工作台查看并生成表单' : '',
        hasParallel ? '平行检验记录可在平行检验页面查看并生成文书' : '',
      ].filter(Boolean).join('；')
      finalMessages[index] = { ...finalMessages[index], reviewPlan: executed, reviewEvents: this.inlineReviewEvents(executed), reviewProgress: this.reviewProgress(executed), reviewResult: `已生成 ${executed.executionResult?.created_count || 0} 条记录${summary ? `：${summary}` : ''}${nextStep ? `。${nextStep}。` : '。'}` }
      this.setData({ messages: finalMessages })
      invalidateByPrefix('/work-record')
      invalidateByPrefix('/parallel')
      invalidateCache(CACHE_KEYS.WORK_RECORD_STATS)
      return true
    } catch (error) {
      const latest = [...this.data.messages]
      latest[index] = { ...latest[index], reviewResult: `暂未生成：${(error as Error).message}` }
      this.setData({ messages: latest })
      return false
    } finally {
      const latest = [...this.data.messages]
      if (latest[index]) latest[index] = { ...latest[index], reviewBusy: false }
      this.setData({ messages: latest })
    }
  },

  /** 识别结果是否命中隐患（与后端 detect 的判定口径保持一致） */
  isHazardHit(raw: any): boolean {
    return !!raw && (!!raw.has_hazard || raw.risk_status === '存在隐患')
  },

  /** 视觉识别只生成待确认候选；这里不写任何正式业务数据。 */
  buildHazardCandidates(
    detections: { path: string; raw: any }[],
    images: Attachment[],
    watermarks: Array<Record<string, any>>,
  ): HazardCandidateView[] {
    return detections.flatMap((item, detectionIndex) => {
      if (!this.isHazardHit(item.raw)) return []
      const imageIndex = images.findIndex(image => image.path === item.path)
      const image = imageIndex >= 0 ? images[imageIndex] : undefined
      const watermark = watermarks[imageIndex >= 0 ? imageIndex : detectionIndex] || {}
      const evidence = Array.isArray(item.raw?.evidence)
        ? item.raw.evidence.map((value: unknown) => String(value || '')).filter(Boolean)
        : []
      return [{
        id: `hazard-${Date.now()}-${detectionIndex}`,
        image: image?.remoteUrl ? absoluteMediaUrl(image.remoteUrl) : item.path,
        localImage: item.path,
        mediaId: image?.mediaId,
        title: String(item.raw?.top1_risk || item.raw?.description || item.raw?.risk_category || '待核实的现场安全隐患'),
        riskCategory: String(item.raw?.risk_category || item.raw?.hazard_type || '未分类'),
        evidence,
        section: String(watermark.section || watermark.work_area || ''),
        location: String(watermark.location || ''),
        weather: String(watermark.weather || ''),
        takenAt: String(watermark.taken_at || ''),
        cameraCapture: image?.cameraCapture === true,
        detection: item.raw,
        selected: true,
        status: 'pending' as const,
        statusText: '待确认',
      }]
    })
  },

  /** Register selected visual results as evidence media without creating a formal hazard. */
  async registerHazardCandidateMedia(candidates: HazardCandidateView[]): Promise<HazardCandidateView[]> {
    const registered: HazardCandidateView[] = []
    for (const candidate of candidates) {
      // 证据准备阶段已上传过的直接复用 mediaId，不再重复上传
      if (candidate.mediaId) {
        registered.push(candidate)
        continue
      }
      try {
        const uploaded = await uploadImage<{ id: number; url: string }>(candidate.localImage, {
          source: 'agent_hazard_candidate',
          trigger_detection: false,
          add_system_watermark: candidate.cameraCapture,
          section: candidate.section,
          location: candidate.location,
          weather: candidate.weather,
        })
        registered.push({ ...candidate, mediaId: uploaded.id, image: absoluteMediaUrl(uploaded.url) })
      } catch (error) {
        registered.push({ ...candidate, error: `证据登记暂未完成：${(error as Error).message}` })
      }
    }
    return registered
  },

  /** Present hazard analysis as a non-writing sub-flow of the same request. */
  async appendHazardCandidateMessage(
    detections: { path: string; raw: any }[],
    images: Attachment[],
    watermarks: Array<Record<string, any>>,
    leadText = '',
  ) {
    let hazardCandidates = this.buildHazardCandidates(detections, images, watermarks)
    if (hazardCandidates.length) {
      hazardCandidates = await this.registerHazardCandidateMedia(hazardCandidates)
    }
    const candidateText = hazardCandidates.length
      ? `已整理 ${hazardCandidates.length} 个隐患候选。这里只做识别，尚未创建正式隐患记录；请在下方核对勾选后确认建档。`
      : '本次未形成需要建档的隐患候选。'
    this.addMessage({
      role: 'assistant',
      text: [leadText, candidateText].filter(Boolean).join('\n\n'),
      hazardCandidates,
      hazardPendingCount: hazardCandidates.length,
      hazardRequestId: newOperationId('hazard-request'),
      hazardExecutionId: newOperationId('hazard-execution'),
    })
  },

  toggleHazardCandidate(event: WechatMiniprogram.TouchEvent) {
    const index = Number(event.currentTarget.dataset.index)
    const id = String(event.currentTarget.dataset.id || '')
    const messages = [...this.data.messages]
    const candidates = messages[index]?.hazardCandidates || []
    const candidate = candidates.find(item => item.id === id)
    if (!candidate || candidate.status !== 'pending' || messages[index].hazardCandidateBusy) return
    messages[index] = {
      ...messages[index],
      hazardCandidates: candidates.map(item => item.id === id ? { ...item, selected: !item.selected } : item),
    }
    this.setData({ messages })
  },

  discardHazardCandidate(event: WechatMiniprogram.TouchEvent) {
    const index = Number(event.currentTarget.dataset.index)
    const id = String(event.currentTarget.dataset.id || '')
    const messages = [...this.data.messages]
    const candidates = messages[index]?.hazardCandidates || []
    if (messages[index]?.hazardCandidateBusy) return
    messages[index] = {
      ...messages[index],
      hazardCandidates: candidates.map(item => item.id === id && item.status === 'pending'
        ? { ...item, selected: false, status: 'discarded', statusText: '已排除' }
        : item),
    }
    messages[index].hazardPendingCount = (messages[index].hazardCandidates || []).filter(item => item.status === 'pending').length
    this.setData({ messages })
  },

  async confirmHazardCandidates(event: WechatMiniprogram.TouchEvent) {
    const index = Number(event.currentTarget.dataset.index)
    const message = this.data.messages[index]
    if (!message?.hazardCandidates?.length || message.hazardCandidateBusy) return
    const selected = message.hazardCandidates.filter(item => item.status === 'pending' && item.selected)
    if (!selected.length) {
      wx.showToast({ title: '请至少勾选一个隐患候选', icon: 'none' })
      return
    }

    let working = message.hazardCandidates.map(item => selected.some(chosen => chosen.id === item.id)
      ? { ...item, status: 'committing' as const, statusText: '正在建档', error: '' }
      : item)
    const update = (busy: boolean, result?: string) => {
      const messages = [...this.data.messages]
      messages[index] = {
        ...messages[index], hazardCandidates: working, hazardCandidateBusy: busy,
        hazardPendingCount: working.filter(item => item.status === 'pending').length,
        ...(result === undefined ? {} : { hazardCandidateResult: result }),
      }
      this.setData({ messages })
    }
    update(true)
    try {
      for (const chosen of selected) {
        const current = working.find(item => item.id === chosen.id)!
        let mediaId = current.mediaId
        if (!mediaId) {
          const uploaded = await uploadImage<{ id: number; url: string }>(current.localImage, {
            source: 'agent_hazard_candidate',
            trigger_detection: false,
            add_system_watermark: current.cameraCapture,
            section: current.section,
            location: current.location,
            weather: current.weather,
          })
          mediaId = uploaded.id
        }
        working = working.map(item => item.id === current.id ? { ...item, mediaId } : item)
        update(true)
      }

      type HazardCommitResponse = { created_count: number; hazards: Array<{ media_id: number; hazard_id: number }> }
      const applyCommitted = (response: HazardCommitResponse, recovered = false) => {
        const idsByMedia = new Map((response.hazards || []).map(item => [Number(item.media_id), Number(item.hazard_id)]))
        working = working.map(item => {
          if (item.status !== 'committing') return item
          const hazardId = idsByMedia.get(Number(item.mediaId))
          return hazardId
            ? { ...item, hazardId, status: 'committed' as const, statusText: `已建档 #${hazardId}` }
            : item
        })
        invalidateCache(CACHE_KEYS.HAZARD_LIST)
        update(false, `${recovered ? '已恢复中断的提交结果：' : ''}已生成 ${response.created_count} 条隐患记录。可在隐患台账继续派单、生成整改文书或复查。`)
      }
      const executionId = message.hazardExecutionId || newOperationId('hazard-execution')
      try {
        const response = await request<HazardCommitResponse>('/hazard/commit-candidates', {
          method: 'POST',
          data: {
            request_id: message.hazardRequestId || newOperationId('hazard-request'),
            execution_id: executionId,
            candidates: selected.map(item => {
              const current = working.find(candidate => candidate.id === item.id)!
              return {
                media_id: current.mediaId,
                detection: current.detection,
                section: current.section,
                location: current.location,
                weather: current.weather,
                taken_at: current.takenAt,
                remark: current.detection?.top1_risk || current.title,
              }
            }),
          },
          timeout: 120000,
        })
        applyCommitted(response)
      } catch (error) {
        if (!isExecutionRecoverable(error)) throw error
        const execution = await waitForAgentExecution(executionId)
        if (execution?.status === 'COMMITTED') {
          applyCommitted(execution.result as HazardCommitResponse, true)
        } else if (execution?.status === 'FAILED') {
          throw new Error(execution.error || '服务端已回滚本次建档')
        } else if (execution?.status === 'RUNNING') {
          throw new Error('服务端仍在确认建档结果，请稍后重新进入查看，不会重复建档')
        } else {
          throw error
        }
      }
    } catch (error) {
      const detail = (error as Error).message
      working = working.map(item => item.status === 'committing'
        ? { ...item, status: 'pending' as const, statusText: '待确认', error: detail }
        : item)
      update(false, `暂未建档：${detail}`)
    }
  },

  /** 单张图片视觉识别，返回可读文本 + 原始识别结果（原始结果供落库复用） */
  async detectImageOnce(imagePath: string): Promise<{ text: string; raw: any }> {
    const token = session.getToken()
    const selectedModel = this.data.models.find(m => m.id === this.data.currentModel)
    const result = await new Promise<any>((resolve, reject) => {
      wx.uploadFile({
        url: `${config.apiBaseUrl}/hazard/detect`,
        filePath: imagePath,
        name: 'image',
        formData: {
          prompt: '请识别这张图片中的安全隐患与现场情况',
          ...(selectedModel?.model ? { model: selectedModel.model } : {}),
        },
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
      if (result.rectify_plan) text += `\n整改单：${result.rectify_plan}`
      if (result.matched_regulations?.length) {
        const regs = (result.matched_regulations as Array<{ title?: string; content_snippet?: string }>)
          .slice(0, 3)
          .map((r: { title?: string; content_snippet?: string }) => `${r.title || '规范条款'}：${r.content_snippet || ''}`)
          .join('；')
        if (regs) text += `\n适用规范：${regs}`
      }
      return { text, raw: result }
    }
    return { text: '未识别到明显安全隐患。', raw: result }
  },

  /**
   * 证据准备阶段的唯一上传口：图片一次上传（/media/images），服务端随上传完成
   * 水印 OCR + EXIF 解析并在响应中返回；结果缓存到 Attachment 上，下游
   * （ReviewPlan / 隐患候选 / 挂记录）直接复用 mediaId，不再二次上传或二次 OCR。
   * 失败返回空壳（_available=false 语义），不阻塞主流程。
   */
  async uploadEvidence(image: Attachment, source: string): Promise<UploadedImageResult | null> {
    if (image.mediaId !== undefined) {
      return {
        id: image.mediaId,
        url: image.remoteUrl || '',
        watermark: image.serverWatermark,
        capture_time: image.captureTime,
        capture_time_source: image.captureTimeSource,
      }
    }
    // 上传故障多为 wx.uploadFile 客户端/网络层瞬时抖动（请求根本没到后端，
    // 同域名 observe 却成功即此特征）。UploadError.retryable 已区分可重试，
    // 这里最多重试 3 次、按 0.8s*轮次退避；仍失败则记下真实错误便于定位。
    let lastErr: any = null
    const maxAttempts = 3
    for (let attempt = 1; attempt <= maxAttempts; attempt++) {
      try {
        const uploaded = await uploadImage<UploadedImageResult>(image.path, {
          source,
          trigger_detection: false,
          add_system_watermark: image.cameraCapture === true,
        })
        image.mediaId = uploaded.id
        image.remoteUrl = uploaded.url
        image.serverWatermark = uploaded.watermark
        image.captureTime = uploaded.capture_time
        image.captureTimeSource = uploaded.capture_time_source
        return uploaded
      } catch (err: any) {
        lastErr = err
        const retryable = err && err.retryable === true
        if (!retryable || attempt === maxAttempts) break
        await new Promise<void>((resolve) => setTimeout(resolve, 800 * attempt))
      }
    }
    const msg = lastErr?.message || (typeof lastErr === 'string' ? lastErr : '图片上传失败')
    ;(this as any).lastUploadErrorMsg = msg
    console.error('[uploadEvidence] 图片上传失败：', msg)
    return null
  },

  /** 服务端水印识别结果 → 前端扁平字段记录（原 readWatermark 的输出格式） */
  normalizeWatermark(uploaded: UploadedImageResult | null): Record<string, any> {
    if (!uploaded || !uploaded.watermark) return { _available: 'false' }
    const result = uploaded.watermark
    const fields = result.fields || {}
    const out: Record<string, any> = {
      _available: result.recognized ? 'true' : result.available ? 'empty' : 'false',
      _lines: Array.isArray(result.lines) ? result.lines : [],
      labeled_items: Array.isArray(fields.labeled_items) && fields.labeled_items.length
        ? fields.labeled_items
        : (Array.isArray(result.labeled_items) ? result.labeled_items : []),
    }
    for (const key of Object.keys(fields)) {
      if (key === 'labeled_items') continue
      const value = String(fields[key] || '').trim()
      if (value) out[key] = value
    }
    return out
  },

  /** Standalone preview endpoint; the workbench upload path reuses uploadImage's OCR result. */
  async readWatermarkWithStatus(imagePath: string): Promise<{
    fields: Record<string, any>
    state: 'recognized' | 'empty' | 'unavailable' | 'failed'
    error?: string
  }> {
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
      const out: Record<string, any> = {
        _available: result?.recognized ? 'true' : result?.available ? 'empty' : 'false',
        _lines: Array.isArray(result?.lines) ? result.lines : [],
        labeled_items: Array.isArray(fields.labeled_items) ? fields.labeled_items : [],
      }
      for (const key of Object.keys(fields)) {
        if (key === 'labeled_items') continue
        const value = String(fields[key] || '').trim()
        if (value) out[key] = value
      }
      const state = result?.recognized ? 'recognized' : result?.available ? 'empty' : 'unavailable'
      return { fields: out, state }
    } catch (error) {
      return { fields: { _available: 'false' }, state: 'failed', error: (error as Error).message }
    }
  },

  /** Compatibility wrapper for flows that only need the extracted fields. */
  async readWatermark(imagePath: string): Promise<Record<string, any>> {
    return (await this.readWatermarkWithStatus(imagePath)).fields
  },

  /** Generic image observation, distinct from dedicated hazard detection. */
  async observeImageWithStatus(imagePath: string): Promise<{
    observation: string
    state: 'completed' | 'empty' | 'unavailable' | 'failed'
    error?: string
  }> {
    const token = session.getToken()
    try {
      const result = await new Promise<any>((resolve, reject) => {
        wx.uploadFile({
          url: `${config.apiBaseUrl}/media/images/observe`, filePath: imagePath, name: 'file',
          header: { Authorization: `Bearer ${token}` },
          success: (res) => {
            if (res.statusCode >= 200 && res.statusCode < 300) {
              try { resolve(JSON.parse(res.data)) } catch { reject(new Error('图片观察返回格式异常')) }
            } else reject(new Error(`图片观察失败（HTTP ${res.statusCode}）`))
          },
          fail: (err) => reject(new Error(err.errMsg || '图片观察上传失败')),
        })
      })
      const observation = result?.available ? String(result.observation || '').trim() : ''
      return {
        observation,
        state: result?.available ? (observation ? 'completed' : 'empty') : 'unavailable',
        ...(!result?.available && result?.detail ? { error: String(result.detail) } : {}),
      }
    } catch (error) {
      return { observation: '', state: 'failed', error: (error as Error).message }
    }
  },

  async observeImageOnce(imagePath: string): Promise<string> {
    return (await this.observeImageWithStatus(imagePath)).observation
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
    // 🔴 2026-11-04 用户明令：图片里的「单位工程」「施工区域」属于施工部位（section）。
    // 服务端 OCR 已把这两个标签归入 section；此处兜底兼容旧数据里的 work_area。
    out.section = String(fields.section || fields.work_area || fields.unit_project || '').trim()
    if (fields.weather) out.weather = fields.weather
    return out
  },

  /** 带素材走 Agent 工具路由：办事类消息只有走这里才会真的落库 */
  async runAgentWithMaterial(
    text: string,
    material: string,
    prefill: Record<string, string>,
    messages: Message[],
    wmFields: Record<string, string>,
    hasImage = false,
    images: Attachment[] = [],
    requestPlan?: AgentRequestPlan,
  ) {
    const requestId = requestPlan?.request_id || newOperationId('agent-chat')
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
          request_id: requestId,
          conversation_id: conversationIdFor(this.currentSessionId),
          material: material || undefined,
          prefill,
          history,
          images: images.map(i => i.remoteUrl).filter(Boolean),
          mode: this.data.mode,
          request_plan: requestPlan,
          provider: selModel?.provider || undefined,
          model: selModel?.model || undefined,
        },
        timeout: 120000,
      })
      // 🔴 2026-11-16 用户口径「识别的内容只保留到思考里，不需要写出来」：
      // 不再把 formatWatermark(wmFields) 逐项拼进对话。水印值仍由 wmFields /
      // prefill 用于回填与分类，这里只用 wmText 判断是否「读到了水印」。
      const wmText = this.formatWatermark(wmFields)
      const reply = String(result.reply || '')
      if (wmText) {
        // 服务端没给自动填入回执时补一句概括（只说栏目名，不写具体值），
        // 避免用户以为系统没看图；已有「自动填入/已读到」就不重复。
        if (!reply.includes('自动填入') && !reply.includes('已读到')) {
          result.reply = [
            '已读到这批照片的水印信息，拍摄时间、地点、部位等已自动带入记录栏位。',
            reply,
          ].filter(Boolean).join('\n')
        }
      } else if (hasImage) {
        // 普通记录场景只做水印读取；没有水印不等于用户没有提供事实，
        // 也不能把未调用隐患模型说成“图片识别结果”。
        result.reply = [
          '【水印】这张照片没读到水印文字（水印要清晰、别被裁掉）。图片已作为附件保留，不影响你在文字中提供的日期、地点和工作内容。',
          reply,
        ].filter(Boolean).join('\n')
      }
      this.applyAgentReply(result, this.data.messages)
      // 照片补挂统一走 applyAgentReply → attachPendingPhotosFromReply，覆盖
      // 「带图首轮只追问、后续纯文字轮才建档」的场景；本轮不再单独挂一次，
      // 否则同一批照片会挂两遍并冒出两条互相矛盾的提示。
      await this.finishAgentProgress(
        requestId,
        result.need_more?.length || result.form ? 'waiting' : 'completed',
        result.need_more?.length || result.form
          ? 'Agent 已整理出当前草稿，正在等待用户补充或确认'
          : 'Agent 本轮处理已完成，结果已返回工作台',
        {
          tool: result.tool || '', model: result.model || '',
          missing_fields: result.need_more || [],
          response_excerpt: String(result.reply || '').slice(0, 700),
          card_ids: (result.cards || (result.card ? [result.card] : [])).map(card => card.id).filter(Boolean),
        },
      )
    } catch (error) {
      const errMsg = (error as Error).message || ''
      const aborted = /abort/i.test(errMsg)
      // 用户已切走/切后台时请求被微信中止属预期，不打刺眼「调用失败」；
      // 仍在页内却被中断（偶发抖动）给柔和提示，真错误仍原样上报。
      if (!(aborted && this.pageUnloading)) {
        this.addMessage({
          role: 'assistant',
          text: aborted ? '请求被中断，请重新发送消息重试。' : `调用失败：${errMsg}`,
        })
      }
      await this.finishAgentProgress(requestId, 'failed', 'Agent 请求失败，未返回成功结果', { error: errMsg })
    } finally {
      this.setData({ loading: false })
      void this.loadPendingDrafts()
    }
  },

  /** 把服务端回执还原成可持久化的助手消息。 */
  agentReplyToMessage(result: AgentChatResponse): Message {
    const cards: AgentCard[] = result.cards && result.cards.length
      ? result.cards
      : (result.card ? [result.card] : [])
    const actions: { label: string; command: string }[] = (result.actions || [])
      .filter(a => a.url)
      .map(a => ({ label: a.label, command: `navigate:${a.url}` }))
    if (result.undo) {
      actions.push({ label: '撤销', command: `agent-undo:${result.undo.entity}:${result.undo.id}` })
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
    // 服务端显式下发的动作 chips（如最终确认态「确认生成」快捷键，2026-11-10：
    // 用户要求确认生成免打字）。与 options 生成的候选 chips 区分：这是单一动作
    // 按钮，点击即以该词发送 → 命中确认白名单直接建档。
    for (const chip of result.chips || []) {
      if (!chips.includes(chip)) chips.push(chip)
    }
    // 主气泡已含追问/接话文案时不再重复「还需要补充：」小字标签（2026-11-04）
    const missing = result.need_more && result.need_more.length && !(result.reply && result.reply.trim())
      ? `还需要补充：${result.need_more.join('、')}`
      : undefined
    const recommendations = result.recommendations || []
    const inputComponents = (result.input_components || []).map(input => {
      const recommendation = recommendations.find(item => item.field === input.field || item.field === input.label)
      return { ...input, value: '', recommendation: recommendation?.value || '', reason: recommendation?.reason || '' }
    })
    const legacyFields: AgentFormField[] = inputComponents.map(input => ({
      name: input.field,
      key: input.field,
      canonical: input.field,
      definition_source: 'legacy_input_components',
      label: input.label,
      control: input.control,
      required: input.required === true,
      value: input.value || '',
      options: input.options || [],
      recommendation: input.recommendation,
      reason: input.reason,
    }))
    const sourceForm: AgentForm | undefined = result.form || (legacyFields.length ? {
      id: 'legacy-input-form',
      kind: 'form',
      schema_version: 1,
      field_schema: 'legacy_input_components.v1',
      title: '补齐信息',
      fields: legacyFields,
      submit_label: '提交',
      skippable: false,
    } : undefined)
    const agentForm: AgentForm | undefined = sourceForm ? {
      ...sourceForm,
      // 🔴 新追问默认收起（2026-11-04 用户反馈「怎么还要填表」）：表格是「当前这一问」的快捷填写，
      // 不是要用户逐项填的整张表。展开态只保留用户主动点开的。
      expanded: false,
      fields: (sourceForm.fields || []).map((field, index, fields) => {
        const recommendation = recommendations.find(item => (
          item.field === field.name || item.field === field.label || item.field === field.canonical
        ))
        return {
          ...field,
          value: field.value || '',
          options: field.options || [],
          recommendation: recommendation?.value || '',
          reason: recommendation?.reason || '',
          showSection: Boolean(field.section) && (index === 0 || field.section !== fields[index - 1]?.section),
        }
      }),
      attachments: sourceForm.attachments ? { ...sourceForm.attachments, picked: sourceForm.attachments.picked || [] } : undefined,
      actions: [...(sourceForm.actions || [])],
      busy: false,
    } : undefined
    if (agentForm && result.undo) {
      agentForm.actions = [...(agentForm.actions || []), {
        type: 'undo', label: '撤销刚才创建的记录', command: `agent-undo:${result.undo.entity}:${result.undo.id}`,
      }]
      actions.splice(0, actions.length, ...actions.filter(action => action.label !== '撤销'))
    }
    return {
      role: 'assistant',
      text: result.reply || '智能体未返回内容',
      timeText: formatMessageTime(),
      reviewDetailsExpanded: false,
      cards,
      actions,
      chips: chips.length ? chips : undefined,
      hint: missing,
      agentForm,
      preferenceSuggestion: result.preference_suggestion
        ? { ...result.preference_suggestion }
        : undefined,
      docMissing: (result.missing_fields || []).length
        ? (result.missing_fields || []).map(field => ({ label: field.label, required: field.required }))
        : undefined,
      docReview: result.review && (result.review.items || []).length
        ? {
            items: (result.review.items || []).map((item, index) => ({
              id: String(index), level: item.level, text: item.text,
            })),
            status: result.review.status || 'ok',
          }
        : undefined,
      docReviewExpanded: false,
    }
  },

  /** 把 /agent/chat 回执渲染成一条助手消息（卡片/动作/chips/缺项提示） */
  applyAgentReply(result: AgentChatResponse, messages: Message[]) {
    // 所有 /agent/chat 回执的统一入口：带图首轮往往只追问类型，真正建档在后续纯文字轮。
    // 只要这一轮回了「工作记录」卡，就把本会话待挂的现场照片补关联上（异步，不阻塞气泡）。
    void this.attachPendingPhotosFromReply(result)
    const reply = this.agentReplyToMessage(result)
    const progressIndex = messages.length - 1
    const progress = messages[progressIndex]
    if (progress?.role === 'assistant' && progress.progressRequestId && !progress.text) {
      const next = [...messages]
      next[progressIndex] = mergeAgentProgress(reply, progress)
      this.setData({ messages: next }, () => this.scrollChatToBottom())
      return
    }
    this.setData({ messages: [...messages, reply] }, () => this.scrollChatToBottom())
  },

  /** 新回执追加后跟到底部：scroll-into-view 值不变不会二次触发，
   *  长回执（分组建档回执常一屏放不下）会停在开头，用户误以为后半段没输出。 */
  scrollChatToBottom() {
    this.setData({ scrollAnchor: '' }, () => {
      this.setData({ scrollAnchor: 'bottom' })
    })
  },

  /** 局部更新某条消息的偏好建议（busy/saved/清除） */
  patchPreferenceSuggestion(
    messageIndex: number,
    patch: Partial<NonNullable<Message['preferenceSuggestion']>> | null,
  ) {
    const messages = [...this.data.messages]
    const message = messages[messageIndex]
    if (!message?.preferenceSuggestion) return
    messages[messageIndex] = {
      ...message,
      preferenceSuggestion: patch ? { ...message.preferenceSuggestion, ...patch } : undefined,
    }
    this.setData({ messages })
  },

  /** 点「记住」才真正落库：偏好会进以后每一轮的系统上下文，绝不自动保存 */
  async savePreferenceSuggestion(event: WechatMiniprogram.TouchEvent) {
    const messageIndex = Number(event.currentTarget.dataset.index)
    const suggestion = this.data.messages[messageIndex]?.preferenceSuggestion
    if (!suggestion || suggestion.saved || suggestion.busy) return
    this.patchPreferenceSuggestion(messageIndex, { busy: true })
    try {
      await request('/agent/preferences', {
        method: 'POST',
        data: {
          rule: suggestion.rule,
          category: suggestion.category,
          source: 'assistant_suggested',
        },
      })
      this.patchPreferenceSuggestion(messageIndex, { saved: true, busy: false })
      wx.showToast({ title: '已记住，以后每轮都生效', icon: 'none' })
    } catch (error) {
      this.patchPreferenceSuggestion(messageIndex, { busy: false })
      wx.showToast({ title: (error as Error).message || '保存失败', icon: 'none' })
    }
  },

  dismissPreferenceSuggestion(event: WechatMiniprogram.TouchEvent) {
    this.patchPreferenceSuggestion(Number(event.currentTarget.dataset.index), null)
  },

  updateAgentForm(messageIndex: number, patch: (form: AgentForm) => AgentForm) {
    const messages = [...this.data.messages]
    const message = messages[messageIndex]
    if (!message?.agentForm) return
    messages[messageIndex] = { ...message, agentForm: patch(message.agentForm) }
    this.setData({ messages })
  },

  onAgentFormInput(event: WechatMiniprogram.Input) {
    const messageIndex = Number(event.currentTarget.dataset.message)
    const name = String(event.currentTarget.dataset.field || '')
    this.updateAgentForm(messageIndex, form => ({
      ...form,
      fields: form.fields.map(field => field.name === name ? { ...field, value: event.detail.value } : field),
    }))
  },

  onAgentFormPicker(event: WechatMiniprogram.PickerChange) {
    const messageIndex = Number(event.currentTarget.dataset.message)
    const name = String(event.currentTarget.dataset.field || '')
    const value = String(event.detail.value)
    this.updateAgentForm(messageIndex, form => ({
      ...form,
      fields: form.fields.map(field => {
        if (field.name !== name) return field
        const selected = field.control === 'select'
          ? (field.options[Number(value)] || value)
          : value
        return { ...field, value: selected }
      }),
    }))
  },

  onAgentFormDateTimeChange(event: WechatMiniprogram.CustomEvent<{ value: string }>) {
    const messageIndex = Number(event.currentTarget.dataset.message)
    const name = String(event.currentTarget.dataset.field || '')
    const value = String(event.detail.value || '')
    this.updateAgentForm(messageIndex, form => ({
      ...form,
      fields: form.fields.map(field => field.name === name ? { ...field, value } : field),
    }))
  },

  chooseAgentFormOption(event: WechatMiniprogram.TouchEvent) {
    const messageIndex = Number(event.currentTarget.dataset.message)
    const name = String(event.currentTarget.dataset.field || '')
    let value = String(event.currentTarget.dataset.value || '')
    const fieldState = this.data.messages[messageIndex]?.agentForm?.fields.find(field => field.name === name)
    if (fieldState?.control === 'datetime' || fieldState?.control === 'time') {
      const normalized = normalizeDateTimeInput(value, fieldState.control === 'time' ? 'time' : 'datetime')
      if (!normalized) {
        wx.showToast({ title: '这个时间建议无法识别，请用时间滚轮选择', icon: 'none' })
        return
      }
      value = normalized
    }
    this.updateAgentForm(messageIndex, form => ({
      ...form,
      fields: form.fields.map(field => field.name === name ? { ...field, value } : field),
    }))
  },

  chooseAgentFormAttachments(event: WechatMiniprogram.TouchEvent) {
    const messageIndex = Number(event.currentTarget.dataset.message)
    const form = this.data.messages[messageIndex]?.agentForm
    if (!form?.attachments?.enabled || form.busy) return
    wx.showActionSheet({
      itemList: ['拍照', '从相册选择'],
      success: result => {
        const isCamera = result.tapIndex === 0
        wx.chooseMedia({
          count: Math.max(1, form.attachments!.max - form.attachments!.picked.length),
          mediaType: ['image'],
          sourceType: [isCamera ? 'camera' : 'album'],
          camera: 'back',
          success: picked => {
            const attachments: Attachment[] = (picked.tempFiles || []).map((file, index) => ({
              id: `form-${Date.now()}-${index}-${Math.random()}`,
              kind: 'image', path: file.tempFilePath,
              name: isCamera ? `拍照 ${index + 1}` : `相册图片 ${index + 1}`,
              size: (file as unknown as { size?: number }).size,
              cameraCapture: isCamera,
            }))
            this.updateAgentForm(messageIndex, current => ({
              ...current,
              attachments: current.attachments ? {
                ...current.attachments,
                picked: [...current.attachments.picked, ...attachments].slice(0, current.attachments.max),
              } : current.attachments,
            }))
          },
        })
      },
    })
  },

  removeAgentFormAttachment(event: WechatMiniprogram.TouchEvent) {
    const messageIndex = Number(event.currentTarget.dataset.message)
    const id = String(event.currentTarget.dataset.id || '')
    this.updateAgentForm(messageIndex, form => ({
      ...form,
      attachments: form.attachments ? { ...form.attachments, picked: form.attachments.picked.filter(item => item.id !== id) } : undefined,
    }))
  },

  async submitAgentForm(event: WechatMiniprogram.TouchEvent) {
    const messageIndex = Number(event.currentTarget.dataset.message)
    const form = this.data.messages[messageIndex]?.agentForm
    if (!form || form.busy) return
    const missing = form.fields.filter(field => field.required && !String(field.value || '').trim())
    if (missing.length) {
      wx.showToast({ title: `请补充${missing[0].label}`, icon: 'none' })
      return
    }
    const picked = form.attachments?.picked || []
    if (form.kind === 'attachments' && !picked.length) {
      wx.showToast({ title: '请先选择照片，或跳过这一步', icon: 'none' })
      return
    }
    this.updateAgentForm(messageIndex, current => ({ ...current, busy: true }))
    try {
      const values: Record<string, string> = {}
      for (const field of form.fields) {
        const value = String(field.value || '').trim()
        if (value) values[field.name] = value
      }
      if (form.kind === 'attachments' && form.record_id) {
        // 一次上传即拿到服务端水印识别结果（mediaId 缓存到 Attachment，下游 link/复核复用）
        const uploadedList = await Promise.all(picked.map(item => this.uploadEvidence(item, 'agent_workbench')))
        const watermarks = uploadedList.map(item => this.normalizeWatermark(item))
        const previewEvidence: ReviewPlan['evidence'] = picked.map((item, index) => {
          const watermark = watermarks[index] || {}
          return {
            id: item.id, image: item.remoteUrl ? absoluteMediaUrl(item.remoteUrl) : item.path,
            observedDate: this.watermarkDate(String(watermark.taken_at || '')),
            observedDateSource: watermark.taken_at ? 'watermark' : '',
            project: String(watermark.project || watermark.project_name || ''),
            section: String(watermark.section || watermark.work_area || ''),
            location: String(watermark.location || ''),
            note: this.formatWatermark(watermark as Record<string, string>),
            watermarkFields: Object.fromEntries(Object.entries(watermark).filter(([key, value]) => !key.startsWith('_') && typeof value === 'string') as [string, string][]),
          }
        })
        const conflicts = evidenceTargetConflicts(previewEvidence, form.target || {})
        if (conflicts.length) {
          const observations = await Promise.all(picked.map(item => this.observeImageOnce(item.path)))
          const note = `为记录 #${form.record_id} 补充照片时发现证据不一致；请按照片水印重新整理候选事项，不修改原记录。`
          const requestPlan = await this.requestAgentPlan(
            note,
            this.buildPlanEvidence(picked, watermarks, observations),
          )
          this.updateAgentForm(messageIndex, current => ({ ...current, busy: false }))
          this.addMessage({
            role: 'assistant',
            text: `这些照片水印中的${conflicts.join('、')}与记录 #${form.record_id} 不一致，系统没有直接挂到原记录。已保留照片并按证据重新整理候选事项，请核对后再决定是否新建或改挂。`,
          })
          await this.createReviewPlanFromAttachments(
            note,
            picked,
            observations,
            requestPlan,
          )
          return
        }
        const result = await this.attachPhotosToRecord(form.record_id, picked)
        this.updateAgentForm(messageIndex, current => ({
          ...current, busy: false, kind: 'attachments_done', attachments: current.attachments ? { ...current.attachments, picked: [] } : undefined,
        }))
        this.addMessage({ role: 'assistant', text: result.failed
          ? `已上传 ${result.ok} 张照片，${result.failed} 张失败：${result.lastError || '请稍后重试'}`
          : `已将 ${result.ok} 张现场照片补充到这条工作记录。` })
        return
      }

      const summary = form.fields
        .filter(field => String(field.value || '').trim())
        .map(field => `${field.label}：${String(field.value).trim()}`)
        .join('\n')
      if (picked.length) {
        await this.submitAgentFormEvidence(messageIndex, form, summary, picked)
        return
      }

      this.setData({ loading: true })
      const messages = [...this.data.messages, { role: 'user', text: summary || form.title, timeText: formatMessageTime() }] as Message[]
      const history = messages.slice(-7, -1)
        .filter(message => message.role === 'user' || message.role === 'assistant')
        .map(message => ({ role: message.role, text: message.text }))
      const selectedModel = this.data.models.find(model => model.id === this.data.currentModel)
      const result = await request<AgentChatResponse>('/agent/chat', {
        method: 'POST',
        data: {
          message: summary || '按表单补充信息',
          request_id: newOperationId('agent-form-submit'),
          form_values: values,
          conversation_id: conversationIdFor(this.currentSessionId),
          history,
          mode: this.data.mode,
          provider: selectedModel?.provider,
          model: selectedModel?.model,
        },
        timeout: 120000,
      })
      this.updateAgentForm(messageIndex, current => ({ ...current, busy: false }))
      this.applyAgentReply(result, this.data.messages)
    } catch (error) {
      this.updateAgentForm(messageIndex, current => ({ ...current, busy: false }))
      this.addMessage({ role: 'assistant', text: `表单提交失败：${(error as Error).message}` })
    } finally {
      this.setData({ loading: false })
    }
  },

  async submitAgentFormEvidence(messageIndex: number, form: AgentForm, summary: string, images: Attachment[]) {
    const watermarks: Array<Record<string, any>> = []
    const observations: string[] = []
    const results = await Promise.all(images.map(async image => ({
      image,
      watermark: this.normalizeWatermark(await this.uploadEvidence(image, 'agent_workbench')),
      observation: await this.observeImageOnce(image.path),
    })))
    for (const result of results) {
      watermarks.push(result.watermark)
      observations.push(result.observation)
    }
    this.updateAgentForm(messageIndex, current => ({ ...current, busy: false }))
    const note = [form.title, summary].filter(Boolean).join('\n')
    const requestPlan = await this.requestAgentPlan(
      note,
      this.buildPlanEvidence(images, watermarks, observations),
    )
    const reviewMessageIndex = await this.createReviewPlanFromAttachments(note, images, observations, requestPlan)
    if (reviewMessageIndex === null || form.kind !== 'work_record') return
    const plan = this.data.messages[reviewMessageIndex]?.reviewPlan
    if (plan && reviewIssues(plan).length === 0) {
      await this.confirmAndExecuteInlineReview(reviewMessageIndex)
    }
  },

  skipAgentForm(event: WechatMiniprogram.TouchEvent) {
    const messageIndex = Number(event.currentTarget.dataset.message)
    this.updateAgentForm(messageIndex, form => ({ ...form, skippable: false, attachments: form.attachments ? { ...form.attachments, picked: [], enabled: false } : undefined }))
    this.addMessage({ role: 'assistant', text: '这一步已跳过，可以继续补充文字、语音或照片。' })
  },

  handleAgentFormAction(event: WechatMiniprogram.TouchEvent) {
    const type = String(event.currentTarget.dataset.type || '')
    const recordId = Number(event.currentTarget.dataset.recordId || 0)
    const url = String(event.currentTarget.dataset.url || '')
    const command = String(event.currentTarget.dataset.command || '')
    if (type === 'generate_document' && recordId) {
      void this.generateDocFromRecord(recordId)
      return
    }
    if (type === 'navigate' && url) {
      this.openAgentPage(url)
      return
    }
    if (command) this.handleActionTap({ currentTarget: { dataset: { command } } } as unknown as WechatMiniprogram.TouchEvent)
  },

  /**
   * 确定性本地动作：精确文本「返回/返回主页/主页」→ 重置会话回空态。
   * 这是纯 UI 动作、无语义歧义，保留在前端；其余自然语言一律交后端智能体。
   */
  tryLocalReset(text: string, userMsg: Message): boolean {
    if (!['返回', '返回主页', '主页'].includes(text.trim())) return false
    this.setData({
      breadcrumb: [],
      hazardMode: false,
      messages: [...this.data.messages, userMsg, { role: 'assistant', text: '已返回主页', timeText: formatMessageTime() }],
    })
    this.currentSessionId = 0
    this.initMessages()
    return true
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
        messages: [...this.data.messages, { role: 'assistant', text: answer || '未找到相关知识', timeText: formatMessageTime() }],
      })
    } catch (error) {
      this.setData({
        messages: [...this.data.messages, { role: 'assistant', text: `查询失败：${(error as Error).message}`, timeText: formatMessageTime() }],
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
    const requestId = newOperationId('agent-chat')
    const messages = [...this.data.messages, userMsg] as Message[]
    this.setData({ messages, loading: true })
    this.beginAgentProgress(requestId, messages.length - 1, '收到，我先理解你的需求', {
      text_excerpt: text.slice(0, 700), conversation_id: conversationIdFor(this.currentSessionId),
    })

    const selModel = this.data.models.find(m => m.id === this.data.currentModel)
    // 最近 6 轮纯文本作为上下文（用于「把这条生成日志」这类指代消解）
    const history = messages
      .filter(m => m.role === 'user' || m.role === 'assistant')
      .slice(-7, -1)
      .map(m => ({ role: m.role, text: m.text }))

    // 2026-11-19「停止生成」：请求可中止，abort 后 catch 走「已停止」分支
    const { promise, abort } = abortableRequest<AgentChatResponse>('/agent/chat', {
      method: 'POST',
      data: {
        message: text,
        request_id: requestId,
        conversation_id: conversationIdFor(this.currentSessionId),
        history,
        mode: this.data.mode,
        provider: selModel?.provider || undefined,
        model: selModel?.model || undefined,
      },
      timeout: 120000,
    })
    this.activeChatAbort = abort
    try {
      const result = await promise
      this.applyAgentReply(result, this.data.messages)
      await this.finishAgentProgress(
        requestId,
        result.need_more?.length || result.form ? 'waiting' : 'completed',
        result.need_more?.length || result.form
          ? 'Agent 已给出当前判断，等待用户补充或确认'
          : 'Agent 本轮回复已完成',
        { tool: result.tool || '', model: result.model || '', missing_fields: result.need_more || [],
          response_excerpt: String(result.reply || '').slice(0, 900) },
      )
    } catch (error) {
      const errMsg = (error as Error).message || ''
      const aborted = /abort/i.test(errMsg)
      if (aborted && this.chatAborted) {
        // 用户主动停止：不打「失败」，把话收干净即可。
        // 进度状态上报用 skipped（后端白名单无 cancelled），文案仍是「用户停止」。
        this.addMessage({ role: 'assistant', text: '已停止生成。' })
        await this.finishAgentProgress(requestId, 'skipped', '用户停止了本轮生成', {})
      } else {
        // 用户已切走/切后台时请求被微信中止属预期，不打刺眼「调用失败」；
        // 仍在页内却被中断（偶发抖动）给柔和提示，真错误仍原样上报。
        if (!(aborted && this.pageUnloading)) {
          this.addMessage({
            role: 'assistant',
            text: aborted ? '请求被中断，请重新发送消息重试。' : `调用失败：${errMsg}`,
          })
          // 失败的用户消息标红「点击重发」，文字不丢（2026-11-19）
          this.markLastUserMessageFailed()
        }
        await this.finishAgentProgress(requestId, 'failed', 'Agent 请求失败，未返回成功结果', { error: errMsg })
      }
    } finally {
      this.chatAborted = false
      this.activeChatAbort = null
      this.setData({ loading: false })
      void this.loadPendingDrafts()
    }
  },

  /** 把最近一条用户消息标记为失败（供「点击重发」交互） */
  markLastUserMessageFailed() {
    const messages = [...this.data.messages]
    for (let i = messages.length - 1; i >= 0; i -= 1) {
      if (messages[i].role === 'user') {
        messages[i] = { ...messages[i], failed: true }
        break
      }
    }
    this.setData({ messages })
  },

  /** 失败消息上的「重新发送」：取原文重新走 send 路径 */
  resendMessage(event: WechatMiniprogram.TouchEvent) {
    const idx = Number(event.currentTarget.dataset.index)
    const msg = this.data.messages[idx]
    if (!msg || msg.role !== 'user') return
    // 清掉失败标记后重发（直接走 send 的完整分流逻辑）
    const messages = [...this.data.messages]
    messages[idx] = { ...msg, failed: false }
    this.setData({ messages, input: msg.text, canSend: true }, () => { void this.send() })
  },

  /** 停止生成（2026-11-19）：中止在飞的 /agent/chat 请求 */
  stopGeneration() {
    if (!this.data.loading) return
    this.chatAborted = true
    if (this.activeChatAbort) {
      this.activeChatAbort()
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
    } else if (kind === 'parallel_inspection') {
      wx.navigateTo({ url: '/pages/parallel/index' })
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

  /**
   * 多轮建单补挂照片。
   * 现场照片在「带图那一轮」就已上传并拿到 mediaId，但那一轮常常只是追问记录类型；
   * 真正建出 work_record 的可能是几轮之后的纯文字消息（handleLlmChat），那条路径既
   * 拿不到附件也不会主动挂图。这里在统一的 applyAgentReply 里拦「工作记录」回执卡，
   * 把本会话攒下的媒体一次性 /media/link 过去，避免记录详情「影像资料」整块空白。
   */
  async attachPendingPhotosFromReply(result: AgentChatResponse) {
    const pending = this.data.pendingRecordPhotos
    if (!pending.length) return
    // 多图分组建单：后端 _create_split_records 已按组把照片分别写进每条记录的
    // inspection_photos / witness_photos。这里不能再把本会话攒下的全部照片补挂到
    // 第一张记录卡，否则别组照片会串进第一条记录（现场实测：巡视应 1 张 → 详情 8 张）。
    const workCards = (result.cards || []).filter(c => c.kind === 'work_record' && c.id)
    if (result.data?.split === true) {
      // 这批照片已由后端分完，清空待挂队列，避免污染后续会话或单条建单
      this.setData({ pendingRecordPhotos: [], pendingRecordPhotosSession: 0 })
      return
    }
    if (workCards.length > 1) {
      // 兜底：后端未下发 split 标志但一次回了多条记录卡，宁可不补挂也不挂错
      return
    }
    const session = this.data.pendingRecordPhotosSession
    const current = this.currentSessionId || 0
    if (session && current && session !== current) {
      // 换了会话就别把旧照片挂到新会话建出的记录上
      this.setData({ pendingRecordPhotos: [], pendingRecordPhotosSession: 0 })
      return
    }
    const recCard =
      (result.cards && result.cards.find(c => c.kind === 'work_record' && c.id)) ||
      (result.card && result.card.kind === 'work_record' ? result.card : undefined)
    if (!recCard || !recCard.id) return
    const recordId = Number(recCard.id)
    if (!recordId) return
    // 先出队：同一条回执只挂一次，也防止这批图被挂到会话里后续建出的第二条记录。
    this.setData({ pendingRecordPhotos: [] })
    try {
      const { ok, failed, lastError } = await this.attachPhotosToRecord(recordId, pending)
      if (ok) {
        this.addMessage({
          role: 'assistant',
          text: `📎 已自动把本次会话的 ${ok} 张现场照片挂到记录 #${recordId}` +
            (failed ? `（另有 ${failed} 张失败${lastError ? `：${lastError}` : ''}）` : '') + '。',
        })
      } else {
        this.setData({ pendingRecordPhotos: pending, pendingRecordPhotosSession: current })
        this.addMessage({
          role: 'assistant',
          text: `⚠️ 现场照片挂记录失败${lastError ? `：${lastError}` : ''}，可点记录卡「补充照片」手动添加。`,
        })
      }
    } catch (error) {
      this.setData({ pendingRecordPhotos: pending, pendingRecordPhotosSession: current })
      this.addMessage({ role: 'assistant', text: `⚠️ 现场照片挂记录失败：${(error as Error).message}` })
    }
  },

  /**
   * 把 Attachment[] 挂到工作记录：证据准备阶段已上传的直接 /media/link 关联（零重复上传），
   * 未上传的退回 uploadPhotosToRecord 逐张上传。返回与 uploadPhotosToRecord 相同的统计。
   */
  async attachPhotosToRecord(recordId: number, images: Attachment[]): Promise<{ ok: number; failed: number; lastError: string }> {
    const uploadedIds = images.filter(img => img.mediaId !== undefined).map(img => img.mediaId as number)
    const pending = images.filter(img => img.mediaId === undefined)
    let ok = 0
    let failed = 0
    let lastError = ''
    if (uploadedIds.length) {
      try {
        await request('/media/link', {
          method: 'POST',
          data: { media_ids: uploadedIds, business_type: 'work_record', business_id: recordId },
          timeout: 30000,
        })
        ok += uploadedIds.length
      } catch (error) {
        failed += uploadedIds.length
        lastError = (error as Error).message
      }
    }
    if (pending.length) {
      const result = await this.uploadPhotosToRecord(recordId, pending.map(img => img.path))
      ok += result.ok
      failed += result.failed
      lastError = result.lastError || lastError
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
        this.addMessage({ role: 'user', images: paths, text: `为记录 #${recordId} 补充 ${paths.length} 张照片`, timeText: formatMessageTime() })
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

  /** 工作记录卡「生成文书」：/form-docs/generate → 同步到我的资料库对应目录 */
  async generateDocFromRecord(recordId: number) {
    if (this.data.loading) return
    wx.showLoading({ title: '生成文书...', mask: true })
    try {
      const res = await request<{ doc_id: number; title: string }>('/form-docs/generate', {
        method: 'POST',
        data: { record_id: recordId },
        timeout: 60000,
      })
      let archiveKbId = 0
      try {
        const library = await request<{ id: number }>('/kb/mine')
        archiveKbId = library.id || 0
      } catch {
        archiveKbId = 0
      }
      this.addMessage({
        role: 'assistant',
        text: `✅ 已生成文书《${res.title}》，并归档到「我的资料库 / 项目文件夹 / 工作记录与表单」；可在资料库中查看、签认并导出 PDF / Word。`,
        actions: [
          { label: '查看文书', command: `navigate:/pages/documents/detail?id=${res.doc_id}` },
          ...(archiveKbId ? [{ label: '打开我的资料库', command: `navigate:/pages/kb/detail?id=${archiveKbId}` }] : []),
        ],
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
      this.guideJoinProject('导入文档会生成工作记录')
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
    const userMsg: Message = { role: 'user', text: `导入文件《${filename}》，请根据文件内容生成对应的工作记录`, timeText: formatMessageTime() }
    const messages = [...this.data.messages, userMsg] as Message[]
    this.setData({ messages, loading: true })
    try {
      // 走智能体：让服务端真正落库一条工作记录（而不是只回一段文字）
      const result = await request<AgentChatResponse>('/agent/chat', {
        method: 'POST',
        data: {
          message: `请根据下面这份导入文件的内容，创建一条规范的工作记录（自动判断巡视/旁站/验收/见证）并回执给我。\n\n文件《${filename}》内容：\n${brief}`,
          request_id: newOperationId('agent-chat'),
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
      // 工作记录卡：附「生成文书」（渲染省统表并同步到我的资料库）
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
          // 主气泡已含追问/接话文案时不再重复「还需要补充：」小字标签（2026-11-04）
          hint: result.need_more && result.need_more.length && !(result.reply && result.reply.trim())
            ? `还需要补充：${result.need_more.join('、')}`
            : undefined,
        }],
      })
    } catch (error) {
      this.setData({
        messages: [...messages, { role: 'assistant', text: `生成失败：${(error as Error).message}`, timeText: formatMessageTime() }],
      })
    } finally {
      this.setData({ loading: false })
      void this.loadPendingDrafts()
    }
  },

  /** 空态建议按钮：一律在当前聊天框内引导用户完成，不跳转到其它页面 */
  handleActionTap(event: WechatMiniprogram.TouchEvent) {
    const command = event.currentTarget.dataset.command as string
    if (command === 'show-form') {
      this.setData({ input: '' })
      return
    }
    // 🔴 无项目用户的「加入项目」快捷入口：直接去 join-project 页填邀请码
    if (command === 'join-project') {
      wx.navigateTo({ url: '/pages/join-project/index' })
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
      this.handleLlmChat(value, { role: 'user', text: value, timeText: formatMessageTime() })
      return
    }
    // 工作记录卡「补充照片」：选图 → /media/images 挂到该记录
    if (command.startsWith('attach-photo:')) {
      const recordId = Number(command.slice('attach-photo:'.length))
      if (recordId) void this.attachPhotoToRecord(recordId)
      return
    }
    // 工作记录卡「生成文书」：渲染省统表文书并同步到我的资料库
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
    // 空态引导卡的分步选项：继续在本页对话里带用户往下走，不跳页
    if (command.startsWith('work-guide:')) {
      this.continueWorkGuide(command.slice('work-guide:'.length))
      return
    }
    switch (command) {
      case '工作记录':
        this.startVoiceWorkFromChat()
        return
      case '知识库':
        this.askScopeFromChat()
        return
      // 「拍照识别安全隐患」「生成今日监理日志」两张卡已下架（2026-11-16）；
      // 对应函数保留给其它入口复用，不再从这里分发。
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

  /** 空状态① 语音记录现场工作：先在当前聊天里给引导和分步选项（不跳页、不直接开录）
   *  2026-11-16：卡片是「带领用户」的对话入口，不是功能跳转；用户选了「开始说话」
   *  才进入录音模式，之后的缺项提示、确认归档仍由智能体逐轮带着走。 */
  startVoiceWorkFromChat() {
    if (!this.data.projectCreated) {
      this.guideJoinProject('语音记录会生成现场工作记录')
      return
    }
    this.addMessage({
      role: 'assistant',
      text: '我来带你把今天的现场情况记成一条正式工作记录，跟着三步走：\n① 说一段现场情况，或先发现场照片，我会自动读出时间、天气、部位和验收要点；\n② 缺的内容我一次列给你，点选或补一句就行；\n③ 你说「写入正式记录」，我再归档。\n先从哪一步开始？',
      actions: [
        { label: '开始说话', command: 'work-guide:voice' },
        { label: '先发照片', command: 'work-guide:photo' },
        { label: '用文字描述', command: 'work-guide:text' },
      ],
    })
  },

  /**
   * 「我的」页派单入口注入的引导对话（2026-11-18 方案②）。
   * 不弹表单：像「开启语音，记录现场工作」一样，由 AI 在当前聊天里带用户
   * 口述「派给谁 + 做什么」，后端 dispatch_task 工具 + 关键词兜底负责落地。
   */
  beginDispatchConversation() {
    this.addMessage({
      role: 'assistant',
      text: '我来帮你把任务派给项目成员，一句话就行。例如：\n「让张三去检查3号楼二层的临边防护」\n「给李四派个单：复查昨天那条隐患的整改情况」\n派单后对方会在眼镜端和微信收到通知。要派给谁、做什么？',
    })
  },

  /** 引导卡的下一步：全部留在当前对话里推进，不跳转到任何页面 */
  continueWorkGuide(step: string) {
    if (step === 'voice') {
      this.beginVoiceWorkSession()
      return
    }
    if (step === 'photo') {
      this.pickImages('album')
      this.addMessage({ role: 'assistant', text: '选好照片后点下方发送，我会先归类，再逐项带你补齐记录内容。' })
      return
    }
    if (step === 'text') {
      // 文字描述同样进建档会话（输入框文字会作为字段补充，走 autoCreateWorkSession），
      // 而不是开一轮普通问答，保证「带领用户」的链路一致。
      this.beginVoiceWorkSession('直接在下方输入现场情况就行，例如「3号楼二层钢筋绑扎，天气晴，一切正常」，我会接着带你补缺项、确认归档。')
      return
    }
  },

  /** 用户选「开始说话」/「用文字描述」后才真正进入记录会话（原 startVoiceWorkFromChat 的动作） */
  beginVoiceWorkSession(hint?: string) {
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
      input: '',
      canSend: false,
    })
    this.addMessage({
      role: 'assistant',
      text: hint || '请点击下方麦克风说出工作内容，例如「3号楼二层钢筋绑扎，天气晴，一切正常」。',
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
      this.guideJoinProject('拍照识别会登记安全隐患')
      return
    }
    this.setData({ hazardMode: true, mode: 'hazard' })
    wx.showActionSheet({
      itemList: ['拍照', '从相册选择'],
      success: (res) => this.pickImages(res.tapIndex === 0 ? 'camera' : 'album'),
    })
  },

  /** 空状态③ 查规范资料库：不跳到资料库页面，由智能体在对话里带用户问（2026-11-16）
   *  chips 点一下即作为用户问题发给 /agent/chat，后续追问、找条款都在同一会话里继续带。 */
  askScopeFromChat() {
    this.addMessage({
      role: 'assistant',
      text: '查规范不用翻页面，我带你问：先选一个方向，或者直接把问题打在下方，我会检索资料库给出条款依据，再按你的追问往下查。',
      chips: ['旁站记录怎么填', '钢筋隐蔽验收要点', '危大工程专项方案', '安全检查要点'],
    })
  },

  /** 空状态④ 生成今日监理日志：结果直接回到聊天框 */
  async generateTodayLogInChat() {
    if (!this.data.projectCreated) {
      this.guideJoinProject('生成监理日志要写入所属项目')
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
      this.guideJoinProject('拍照会生成现场工作记录')
      return
    }
    const isCamera = source === 'camera'
    this.setData({ showPlusPanel: false })
    wx.chooseMedia({
      count: isCamera ? 1 : 9,
      mediaType: ['image'],
      sourceType: isCamera ? ['camera'] : ['album'],
      camera: 'back',
      success: (res) => {
        const picked: Attachment[] = (res.tempFiles || []).map((f, i) => ({
          id: `img-${Date.now()}-${i}`,
          kind: 'image',
          path: f.tempFilePath,
          name: isCamera ? `拍照 ${i + 1}` : `图片 ${i + 1}`,
          size: (f as unknown as { size?: number }).size,
          cameraCapture: isCamera,
        }))
        this.appendAttachments(picked)
      },
      fail: () => { /* 用户取消 */ },
    })
  },

  /** 「文件」：从微信聊天记录中选文档（pdf/doc/docx/xls/xlsx/txt/md） */
  pickLocalFile() {
    if (!this.data.projectCreated) {
      this.guideJoinProject('导入文件会生成工作记录')
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
      this.guideJoinProject('导入文件会生成工作记录')
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
    this.openImageViewer(urls, item.path)
  },

  /** 聊天记录里的图片点开预览：同一消息的多张图传给 urls，支持左右滑动/上一张下一张 */
  previewImageUrl(event: WechatMiniprogram.TouchEvent) {
    const url = String(event.currentTarget.dataset.url || '')
    if (!url) return
    const mi = Number(event.currentTarget.dataset.mi)
    const msg = this.data.messages[mi]
    // 该消息的图片全集：优先多图数组，其次单图。
    // 🔴 不用原生 wx.previewImage：它没有「上一张/下一张」按钮、多图只能滑动，
    // 且只传 1 张时根本切不了（用户反馈「点不了下一张，只能退出」）。统一走
    // 带按钮的 image-viewer 组件。这里把 本地路径(images/image) 与 服务端兜底
    // (remoteImages) 做并集去重，避免「本地照片已失效、remoteImages 却没进 urls」
    // 导致预览里只有一张。
    const localUrls = msg?.images && msg.images.length
      ? [...msg.images]
      : (msg?.image ? [msg.image] : [])
    const remoteUrls = msg?.remoteImages && msg.remoteImages.length
      ? [...msg.remoteImages]
      : []
    const urls = Array.from(new Set([...localUrls, ...remoteUrls, url].filter(Boolean)))
    this.openImageViewer(urls.length ? urls : [url], url)
  },

  /** 聊天内其它缩略图（水印照片卡/事件证据/隐患候选）统一预览：组件带上一张/下一张 */
  previewInlineImages(event: WechatMiniprogram.TouchEvent) {
    const url = String(event.currentTarget.dataset.url || '')
    if (!url) return
    const mi = Number(event.currentTarget.dataset.mi)
    const msg = this.data.messages[mi]
    // data-group：该卡片自己的照片数组（水印照片卡/事件证据卡），
    // 让同一张卡里的多张照片能互相切换，而不是只看到点开的这一张。
    const group = event.currentTarget.dataset.group as { image?: string }[] | undefined
    const groupUrls = Array.isArray(group)
      ? group.map(item => String(item?.image || '')).filter(Boolean)
      : []
    // 集合 = 卡片内照片 + 消息本地图 + 服务端兜底图 + 当前点击图（去重）
    const localUrls = msg?.images && msg.images.length ? [...msg.images] : (msg?.image ? [msg.image] : [])
    const remoteUrls = msg?.remoteImages && msg.remoteImages.length ? [...msg.remoteImages] : []
    const urls = Array.from(new Set([...groupUrls, ...localUrls, ...remoteUrls, url].filter(Boolean)))
    const list = urls.length ? urls : [url]
    this.setData({
      imageViewerVisible: true,
      imageViewerUrls: list,
      imageViewerCurrent: Math.max(0, list.indexOf(url)),
    })
  },

  /** 聊天内图片统一走带「上一张/下一张」的组件（替代无按钮的原生预览） */
  openImageViewer(urls: string[], currentUrl: string) {
    const list = Array.from(new Set((urls || []).filter(Boolean)))
    if (!list.length) return
    this.setData({
      imageViewerVisible: true,
      imageViewerUrls: list,
      imageViewerCurrent: Math.max(0, list.indexOf(currentUrl)),
    })
  },

  onImageViewerClose() {
    this.setData({ imageViewerVisible: false, imageViewerUrls: [], imageViewerCurrent: 0 })
  },

  /** 消息内图片加载失败兜底：本地临时路径失效时切换到服务端远程地址（remoteImages）。
   * 仅当远程地址与当前不同才替换，避免 binderror 重复触发死循环。 */
  onMsgImgError(event: WechatMiniprogram.TouchEvent) {
    const mi = Number(event.currentTarget.dataset.mi)
    const imgIdx = Number(event.currentTarget.dataset.imgidx)
    const msg = this.data.messages[mi]
    if (!msg) return
    const remote = msg.remoteImages?.[imgIdx]
    const current = msg.images?.[imgIdx] ?? (imgIdx === 0 ? msg.image : undefined)
    if (remote && remote !== current) {
      const updated = [...this.data.messages]
      const m = { ...updated[mi] }
      if (imgIdx === 0 && !m.images) {
        m.image = remote
      } else {
        const imgs = [...(m.images || [])]
        imgs[imgIdx] = remote
        m.images = imgs
      }
      updated[mi] = m
      this.setData({ messages: updated })
    }
  },

  /** 一键复制某条对话气泡的文字内容到剪贴板 */
  /** 长按消息气泡：复制 / 一句话改归类 / 补现场照片（搬自 8090 交互原型）。
   * 「改归类」不直接改后端记录，只拼一句话发进对话，并在本条消息挂一张
   * 偏好卡——用户点「记住」才会真正落库，守住「偏好绝不自动保存」的约定。 */
  onMessageLongPress(event: WechatMiniprogram.TouchEvent) {
    const index = Number(event.currentTarget.dataset.index)
    const msg = this.data.messages[index]
    if (!msg) return
    wx.showActionSheet({
      itemList: ['复制内容', '改归类 / 纠错', '补现场照片'],
      success: (res) => {
        if (res.tapIndex === 0) {
          const text = (msg.text ? String(msg.text) : '').trim()
          if (!text) {
            wx.showToast({ title: '没有可复制的文字', icon: 'none' })
            return
          }
          wx.setClipboardData({ data: text })
        } else if (res.tapIndex === 1) {
          this.openQuickCorrection(index)
        } else if (res.tapIndex === 2) {
          this.selectImage()
        }
      },
      fail: () => {},
    })
  },

  /** 一句话纠错改归类：选目标类型 → 自动发一句话 → 给「以后都按这个」偏好卡 */
  openQuickCorrection(messageIndex: number) {
    const types = ['巡视', '旁站', '验收', '见证']
    wx.showActionSheet({
      itemList: types.map((t) => `改成 ${t}`),
      success: (res) => {
        const target = types[res.tapIndex]
        if (!target) return
        if (this.data.loading) {
          wx.showToast({ title: '上一轮还没结束，稍后再试', icon: 'none' })
          return
        }
        const rule = `以后这类现场记录按「${target}」归类`
        const messages = [...this.data.messages]
        const msg = messages[messageIndex]
        if (msg) {
          messages[messageIndex] = {
            ...msg,
            preferenceSuggestion: {
              rule,
              category: 'record_type',
              reason: 'assistant' as const,
            },
          }
        }
        const text = `这条记录归类不对，请改成「${target}」。${rule}。`
        this.setData({
          messages,
          input: text,
          canSend: true,
        }, () => { void this.send() })
      },
      fail: () => {},
    })
  },

  copyMessage(event: WechatMiniprogram.TouchEvent) {
    const idx = Number(event.currentTarget.dataset.index)
    const msg = this.data.messages[idx]
    const text = (msg && msg.text ? String(msg.text) : '').trim()
    if (!text) {
      wx.showToast({ title: '没有可复制的文字', icon: 'none' })
      return
    }
    wx.setClipboardData({ data: text })
  },

  async sendImage() {
    if (!this.data.pendingImage) return
    const imagePath = this.data.pendingImage
    const userMsg: Message = { role: 'user', text: '请识别这张图片中的隐患', image: imagePath, timeText: formatMessageTime() }
    this.setData({ pendingImage: '', messages: [...this.data.messages, userMsg], loading: true })

    try {
      const token = session.getToken()
      const selectedModel = this.data.models.find(m => m.id === this.data.currentModel)
      const result = await new Promise<any>((resolve, reject) => {
        wx.uploadFile({
          url: `${config.apiBaseUrl}/hazard/detect`,
          filePath: imagePath,
          name: 'image',
          formData: {
            prompt: '请识别这张图片中的安全隐患',
            ...(selectedModel?.model ? { model: selectedModel.model } : {}),
          },
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
        if (result.recheck_suggestion?.length) answer += `复查建议：${result.recheck_suggestion.join('、')}\n`
        if (result.rectify_plan) answer += `整改单：${result.rectify_plan}\n`
        if (result.matched_regulations?.length) {
          const regs = (result.matched_regulations as Array<{ title?: string; content_snippet?: string }>)
            .slice(0, 3)
            .map((r: { title?: string; content_snippet?: string }) => `${r.title || '规范条款'}：${r.content_snippet || ''}`)
            .join('；')
          if (regs) answer += `适用规范：${regs}`
        }
      } else {
        answer = '未识别到明显安全隐患。'
      }
      this.setData({
        messages: [...this.data.messages, { role: 'assistant', text: answer, timeText: formatMessageTime() }],
      })
    } catch (error) {
      this.setData({
        messages: [...this.data.messages, { role: 'assistant', text: `识别失败：${(error as Error).message}`, timeText: formatMessageTime() }],
      })
    } finally {
      this.setData({ loading: false })
    }
  },

  /** 语音拍照自动创建工作记录 — 进入语音会话 */
  startVoiceWorkSession(photoPath: string) {
    const userMsg: Message = photoPath
      ? { role: 'user', text: '用这张照片记录现场工作', image: photoPath, timeText: formatMessageTime() }
      : { role: 'user', text: '语音记录现场工作', timeText: formatMessageTime() }
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
        start_time: fields.start_time || fields.station_start_time || '',
        end_time: fields.end_time || fields.station_end_time || '',
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

  addMessage(msg: Message): number {
    if (msg.role === 'assistant' && msg.text) {
      msg = { ...msg, text: cleanMarkdown(msg.text) }
    }
    if (!msg.timeText) {
      msg = { ...msg, timeText: formatMessageTime() }
    }
    const messages = [...this.data.messages]
    const progressIndex = messages.length - 1
    const progress = messages[progressIndex]
    if (msg.role === 'assistant' && progress?.role === 'assistant' && progress.progressRequestId && !progress.text) {
      messages[progressIndex] = mergeAgentProgress(msg, progress)
      this.setData({ messages }, () => this.scrollChatToBottom())
      return progressIndex
    }
    messages.push(msg)
    this.setData({ messages }, () => this.scrollChatToBottom())
    return messages.length - 1
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

  onWorkFormDateChange(event: WechatMiniprogram.PickerChange) {
    const field = String(event.currentTarget.dataset.field || '')
    if (field !== 'record_date') return
    this.setData({ [`workForm.${field}`]: String(event.detail.value) })
  },

  onWorkFormDateTimeChange(event: WechatMiniprogram.CustomEvent<{ value: string }>) {
    const field = String(event.currentTarget.dataset.field || '')
    if (!['inspection_start_time', 'inspection_end_time', 'start_time', 'end_time'].includes(field)) return
    this.setData({ [`workForm.${field}`]: event.detail.value })
  },

  async submitWorkForm() {
    const type = this.data.workFormType
    const form: Record<string, any> = { ...this.data.workForm }
    for (const key of ['start_time', 'end_time', 'inspection_start_time', 'inspection_end_time', 'record_date']) { if (!form[key]) delete form[key] }
    if ('worker_count' in form) form.worker_count = form.worker_count === '' ? null : Number(form.worker_count)
    // schema 必填字段校验（provform draft-2026.09）
    const required: Record<string, Array<[string, string]>> = {
      inspection: [['section', '巡视部位'], ['work_content', '施工情况'], ['inspection_start_time', '巡视开始时间']],
      acceptance: [['acceptance_type', '验收类型'], ['section', '施工区域'], ['acceptance_content', '验收内容'], ['acceptance_result', '验收结果']],
      station: [['station_location', '旁站部位'], ['station_process', '关键工序'], ['start_time', '旁站开始时间'], ['end_time', '旁站结束时间']],
      witness: [['witness_material_name', '材料名称'], ['witness_quantity', '取样数量']],
    }
    for (const [field, label] of required[type] || []) {
      if (!String(form[field] || '').trim()) {
        wx.showToast({ title: `请填写${label}`, icon: 'none' })
        return
      }
    }

    if (type === 'inspection' || type === 'station') {
      const startField = type === 'station' ? 'start_time' : 'inspection_start_time'
      const endField = type === 'station' ? 'end_time' : 'inspection_end_time'
      const range = resolveDateTimeRange(String(form[startField] || ''), String(form[endField] || ''), String(form.record_date || ''))
      if (range.invalidField) {
        const field = range.invalidField === 'start' ? startField : endField
        wx.showToast({ title: `请重新选择${WORK_FIELD_LABELS[field] || '时间'}`, icon: 'none' })
        return
      }
      if (range.start) form[startField] = range.start
      if (range.end) form[endField] = range.end
    }
    await this.postWorkForm(type, form)
  },

  async postWorkForm(type: string, payload: Record<string, any>) {
    // 🔴 防重入：请求在途中再点一次会重复建单（巡视/旁站 30s+ 很容易连点）
    if (this.data.workFormSubmitting) return
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
          timeText: formatMessageTime(),
        }],
      })
    } catch (error) {
      wx.showToast({ title: (error as Error).message, icon: 'none' })
    } finally {
      this.setData({ workFormSubmitting: false })
    }
  },

  async submitDailyLog() {
    // 🔴 防重入：日志生成 30~60s，连点会生成两份当日监理日志
    if (this.data.logGenerating) return
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
          timeText: formatMessageTime(),
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
    if (this.data.taskSubmitting) return // 🔴 防重入：连点会重复派单
    this.setData({ taskSubmitting: true })
    try {
      await request('/task', {
        method: 'POST',
        data: { ...form, assignee_id: Number(form.assignee_id) || undefined },
      })
      wx.showToast({ title: '派单成功', icon: 'success' })
      this.setData({ showTaskForm: false })
      this.setData({
        messages: [...this.data.messages, { role: 'assistant', text: `任务「${form.title}」已派发`, timeText: formatMessageTime() }],
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
    if (this.data.docSubmitting) return // 🔴 防重入：连点会重复建文书
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
          timeText: formatMessageTime(),
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

  /**
   * 🔴 无项目用户的统一引导出口（2026-10-21）。
   *
   * 取代原先到处散落的 `wx.showToast('请先创建项目')` —— 那句话既指错了动作
   * （建项目对小程序用户必然 403），又不给出路。这里改成弹窗说明 + 一键直达
   * 「加入项目」页填邀请码。
   *
   * @param reason 为什么这个操作需要项目（会显示给用户，说人话）
   */
  guideJoinProject(reason?: string) {
    wx.showModal({
      title: '需要先加入项目',
      content: (reason ? reason + '。' : '') + '向项目负责人索取项目邀请码，填写后即可使用现场记录等功能。',
      confirmText: '去加入项目',
      cancelText: '稍后再说',
      success: (res) => {
        if (res.confirm) wx.navigateTo({ url: '/pages/join-project/index' })
      },
    })
  },

  /** 引导条/快捷按钮「去加入项目」：直接跳，不再弹窗（用户已经明确要去了） */
  goJoinProject() {
    wx.navigateTo({ url: '/pages/join-project/index' })
  },

  /**
   * 按「有没有项目」重算 Hero 区展示的快捷动作。
   *
   * 无项目时：滤掉会产生业务数据的（语音记录），只留「查询规范资料库」
   * 这类真能用的，并补一个「加入项目」入口 ——
   * 让用户第一眼就知道现在能干什么、下一步该干什么，而不是点一个被拦一个。
   */
  refreshHeroActions() {
    const hasProject = this.data.projectCreated
    const visible = this.data.heroActions
      .filter((a) => hasProject ? !a.onlyNoProject : (!a.needProject || a.onlyNoProject))
      .map(({ icon, text, command }) => ({ icon, text, command }))
    this.setData({ visibleHeroActions: visible })
  },

  /** 侧边栏「项目概况」→ 点进修改当前项目（名称/地点/参建单位/描述） */
  async openProjectEdit() {
    if (!this.data.currentProjectId) {
      this.guideJoinProject('你还没有所属项目')
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
    if (this.data.loading) return // 🔴 防重入：建项目请求慢，连点会创建两个同名项目
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
          { role: 'user', text: `创建项目：${name}`, timeText: formatMessageTime() },
          { role: 'assistant', text: `项目「${result.name}」已创建成功！现在可以开始工作了。`, timeText: formatMessageTime() },
        ],
      })
    } catch (error) {
      const message = (error as Error).message
      // 🔴 建项目要 project_create 权限（后端仅超管持有），而超管不允许登录小程序
      // ⇒ 小程序里任何人都建不了项目。原来直接把后端 403 原文 toast 出来
      //   （"您的角色（监理员）无权访问此功能，需要权限: project_create"）对用户毫无意义。
      // 改成说人话并给出路：要么加入已有项目，要么让管理员在 Web 后台立项。
      if (!isEdit && /无权|权限|403/.test(message)) {
        this.setData({ loading: false, showProjectForm: false })
        wx.showModal({
          title: '无法新建项目',
          content: '新建项目由管理员在 Web 后台完成。如果你是要加入已有项目，请向项目负责人索取邀请码。',
          confirmText: '去加入项目',
          cancelText: '知道了',
          success: (res) => {
            if (res.confirm) wx.navigateTo({ url: '/pages/join-project/index' })
          },
        })
        return
      }
      wx.showToast({ title: message, icon: 'none' })
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
    const storageKey = historyStorageKey(session.getUser()?.user_id)
    const list = (wx.getStorageSync(storageKey) || []) as ChatHistory[]
    // 复用当前会话 id：同一会话多次保存只覆盖同一条，不产生重复记录
    const id = this.currentSessionId || Date.now()
    this.currentSessionId = id
    const entry: ChatHistory = {
      id,
      conversationId: conversationIdFor(id),
      title,
      modelId: this.data.currentModel,
      messages,
      updatedAt: now,
      projectId: this.data.currentProjectId || undefined,
    }
    wx.setStorageSync(storageKey, [entry, ...list.filter(h => h.id !== id)].slice(0, 30))
  },

  remoteSummaryToHistory(item: RemoteConversationSummary): ChatHistory {
    return {
      id: sessionIdFromConversationId(item.conversation_id),
      conversationId: item.conversation_id,
      title: item.title || '新对话',
      modelId: this.data.currentModel,
      messages: [],
      updatedAt: item.updatedAt || '',
      messageCount: item.message_count,
      remote: true,
      projectId: this.data.currentProjectId || undefined,
    }
  },

  async openHistory() {
    this.saveCurrentSession()
    const storageKey = historyStorageKey(session.getUser()?.user_id)
    const activeProjectId = this.data.currentProjectId || 0
    const cached = (wx.getStorageSync(storageKey) || []) as ChatHistory[]
    // 🔴 项目隔离：本地缓存必须按当前项目过滤。老缓存没有 projectId 的记录
    // 无法判定归属，进入项目后不再展示（服务端历史仍是真源，可正常恢复）。
    const local = cached.filter(item =>
      activeProjectId ? item.projectId === activeProjectId : !item.projectId,
    )
    this.setData({
      historyList: local,
      historyListAll: local,
      historyQuery: '',
      showHistory: true,
      drawerHistoryExpanded: true,
    })
    void this.loadProjectSummary()
    void this.loadTodoPendingCount()
    try {
      const result = await request<{ conversations: RemoteConversationSummary[] }>('/agent/conversations', {
        data: { limit: 30 },
      })
      const remote = (result.conversations || []).map(item => {
        const summary = this.remoteSummaryToHistory(item)
        const cachedEntry = local.find(history => history.conversationId === summary.conversationId)
        return cachedEntry ? { ...summary, modelId: cachedEntry.modelId, messages: cachedEntry.messages } : summary
      })
      const remoteIds = new Set(remote.map(item => item.conversationId))
      const localOnly = local.filter(item => !item.conversationId || !remoteIds.has(item.conversationId))
      const merged = [...remote, ...localOnly].slice(0, 30)
      this.setData({ historyList: this.filterHistoryByQuery(merged), historyListAll: merged })
    } catch {
      // 服务端暂时不可用时保留本地历史，不阻塞侧边栏。
    }
  },

  /** 历史搜索过滤：标题或消息内容命中即保留（大小写不敏感） */
  filterHistoryByQuery(list: ChatHistory[]): ChatHistory[] {
    const q = (this.data.historyQuery || '').trim().toLowerCase()
    if (!q) return list
    return list.filter(item => {
      if ((item.title || '').toLowerCase().includes(q)) return true
      return (item.messages || []).some(m => (m.text || '').toLowerCase().includes(q))
    })
  },

  onHistorySearch(event: WechatMiniprogram.Input) {
    // 先回显，过滤防抖 200ms（历史含全部消息文本，全量扫有成本）
    this.setData({ historyQuery: event.detail.value })
    if (this.historySearchTimer !== null) {
      clearTimeout(this.historySearchTimer)
    }
    this.historySearchTimer = setTimeout(() => {
      this.historySearchTimer = null
      this.setData({ historyList: this.filterHistoryByQuery(this.data.historyListAll) })
    }, 200)
  },

  clearHistorySearch() {
    if (this.historySearchTimer !== null) {
      clearTimeout(this.historySearchTimer)
      this.historySearchTimer = null
    }
    this.setData({ historyQuery: '', historyList: this.data.historyListAll })
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

  async loadTodoPendingCount() {
    if (!this.data.currentProjectId) {
      this.setData({ todoPendingCount: 0 })
      return
    }
    try {
      const result = await request<{ pending_count: number }>('/collaboration/inbox?view=action')
      this.setData({ todoPendingCount: result.pending_count || 0 })
    } catch {
      // 请求失败时清零，避免切换项目后显示上一个项目的红点。
      this.setData({ todoPendingCount: 0 })
    }
  },

  /** 侧边栏分组折叠：当前仅「历史对话」。
   *  工作文书组已移除，文书管理统一收敛到资料库；资料库为底栏 tab。 */
  toggleDrawerGroup(event: WechatMiniprogram.TouchEvent) {
    const group = String((event.currentTarget.dataset as { group?: string }).group || '')
    if (group === 'history') {
      this.setData({ drawerHistoryExpanded: !this.data.drawerHistoryExpanded })
    }
  },

  async switchProject() {
    // 🔴 无项目用户点导航栏标题：别弹「切换项目」——那里列表必然是空的「暂无项目」，
    //    唯一可点的「新建项目」又必然 403（见 openProjectCreate 注释），是个死路。
    //    直接引导去填邀请码加入项目。
    if (!this.data.projectCreated) {
      this.guideJoinProject('你还没有所属项目，无法切换')
      return
    }
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
      // 401：全局 request 已清会话并跳登录页，这里不再弹英文原文
      if ((error as { statusCode?: number }).statusCode !== 401) {
        wx.showToast({ title: (error as Error).message, icon: 'none' })
      }
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
      void this.loadTodoPendingCount()
    } catch (error) {
      // 401 = 登录态失效：全局 request 已清会话并跳登录页（含 pendingRoute 回跳），
      // 这里不重复弹错误原文（否则用户会看到后端英文 `Not authenticated` 盖在页面上）。
      if ((error as { statusCode?: number }).statusCode !== 401) {
        wx.showModal({
          title: '切换失败',
          content: (error as Error).message || '请稍后重试',
          showCancel: false,
        })
      }
    } finally {
      this.setData({ switchingProject: false })
    }
  },

  async loadHistoryItem(event: WechatMiniprogram.TouchEvent) {
    const id = Number(event.currentTarget.dataset.id)
    const item = this.data.historyList.find(h => h.id === id)
    if (!item) return
    if (item.conversationId) {
      try {
        const detail = await request<RemoteConversationDetail>(
          `/agent/conversations/${encodeURIComponent(item.conversationId)}`,
        )
        this.applyConversationDetail(detail)
        return
      } catch {
        if (!item.messages.length) {
          wx.showToast({ title: '历史会话加载失败，请稍后重试', icon: 'none' })
          return
        }
      }
    }
    // 接续该历史会话：继续对话时覆盖同一条历史，而不是新建
    this.currentSessionId = item.id
    // 历史回看时本地临时路径已失效，改用服务端远程地址（remoteImages）显示，
    // 否则重进会话后用户发的照片变空白（旧数据无 remoteImages 时沿用原 images，向后兼容）。
    const restoredMessages = (item.messages || []).map(m =>
      (m.role === 'user' && m.remoteImages && m.remoteImages.length)
        ? { ...m, images: m.remoteImages, image: m.remoteImages[0] }
        : m)
    this.setData({
      showHistory: false,
      mode: 'chat',
      hazardMode: false,
      messages: restoredMessages,
    })
    void this.loadPendingDrafts()
    void this.resumeAgentProgress()
  },

  async deleteHistoryItem(event: WechatMiniprogram.TouchEvent) {
    const id = Number(event.currentTarget.dataset.id)
    const item = this.data.historyList.find(history => history.id === id)
    if (!item) return
    if (item.conversationId) {
      try {
        await request(`/agent/conversations/${encodeURIComponent(item.conversationId)}`, { method: 'DELETE' })
      } catch (error) {
        wx.showToast({ title: (error as Error).message || '删除失败', icon: 'none' })
        return
      }
    }
    const storageKey = historyStorageKey(session.getUser()?.user_id)
    const local = ((wx.getStorageSync(storageKey) || []) as ChatHistory[]).filter(h => h.id !== id)
    wx.setStorageSync(storageKey, local)
    const historyList = this.data.historyList.filter(h => h.id !== id)
    // 全量列表同步删除（2026-11-19：搜索态下删除，清空搜索后不复活）
    const historyListAll = this.data.historyListAll.filter(h => h.id !== id)
    this.setData({ historyList, historyListAll })
    if (this.currentSessionId === id) {
      this.currentSessionId = 0
      this.initMessages()
    }
  },

  newChat() {
    this.saveCurrentSession()
    this.currentSessionId = 0
    this.setData({ showHistory: false })
    this.initMessages()
    void this.loadPendingDrafts()
    wx.showToast({ title: '已开启新对话', icon: 'none' })
  },

  /** 双击底部「工作台」tab 新建对话（tab-bar 组件检测到 300ms 内二次点击已选中的 tab 后抛出）。 */
  onTabDoubleTap() {
    this.newChat()
  },

  navigateTo(event: WechatMiniprogram.TouchEvent) {
    const url = event.currentTarget.dataset.url as string
    this.setData({ showHistory: false, showPlusPanel: false })
    wx.navigateTo({ url })
  },

  // 输入卡左侧的模型下拉（千问 / 华工）；与 @ 面板互斥
  toggleModelMenu() {
    this.setData({ showModelMenu: !this.data.showModelMenu, showAtPanel: false })
  },

  // 页面根 bindtap：点气泡外任意处关闭模型菜单 / @ 面板（无条件关闭而非 toggle，
  // 否则冒泡到菜单项/按钮时会把刚关掉的面板又翻转打开；滚动是 touch 事件不受影响）
  onPageTap() {
    if (this.data.showModelMenu || this.data.showAtPanel) {
      this.setData({ showModelMenu: false, showAtPanel: false })
      this.atOpenedByTyping = false
    }
  },

  selectModel(event: WechatMiniprogram.TouchEvent | WechatMiniprogram.CustomEvent) {
    const detail = event.detail as { id?: string }
    const id = (detail && detail.id) || (event.currentTarget && (event.currentTarget.dataset.model as string))
    const model = this.data.models.find(m => m.id === id)
    if (!model) return
    wx.setStorageSync(`gdjl_${config.storageScope}_agent_model`, model.id)
    this.setData({ currentModel: model.id, modelName: model.name, modelShortName: model.shortName, showModelMenu: false })
    this.addMessage({ role: 'assistant', text: `已切换为「${model.name}」模型。` })
  },

  // 模型下拉菜单里的「隐患识别」入口：底部 tab 第 1 格改为待办事项后，这里是隐患识别的固定入口
  openHazardCapture() {
    this.setData({ showModelMenu: false })
    wx.navigateTo({ url: '/pages/capture/index?mode=hazard' })
  },

  // ============ @ 提及面板 ============
  // 空操作：@ 面板内部 catchtap 阻断冒泡，避免点面板被 onPageTap 关掉
  noop() {},

  // 两个气泡互斥：开 @ 面板时关模型菜单，反之亦然
  toggleAtPanel() {
    const opening = !this.data.showAtPanel
    this.setData({
      showAtPanel: opening, showModelMenu: false, atQuery: '',
      atVisibleItems: this.data.atBiz,
      // 每次打开都回到根层级，避免上次下钻的文件夹残留
      atDrillStack: [], atDrillItems: [], atDrillPath: '',
    })
    // 按钮唤起：清掉打字标记（此路径点选走追加插入，不替换触发词）
    this.atOpenedByTyping = false
    if (opening) void this.loadAtData()
  },

  onAtSearch(event: WechatMiniprogram.Input) {
    // 防抖（2026-11-19）：@ 面板数据量可达数百条（记录/文件），
    // 每敲一个字符都全量过滤 + setData 会掉帧。先回显输入，过滤延迟合并。
    const raw = event.detail.value
    this.setData({ atQuery: raw })
    if (this.atSearchTimer !== null) {
      clearTimeout(this.atSearchTimer)
    }
    this.atSearchTimer = setTimeout(() => {
      this.atSearchTimer = null
      this.runAtFilter(raw)
    }, 120)
  },

  // 打开面板时按需加载；失败静默（记录/隐患/规范/资料库各自独立降级）
  async loadAtData() {
    if (this.data.atBiz.length) return
    this.setData({ atLoading: true })
    const results = await Promise.allSettled([
      request<{ records?: Array<Record<string, unknown>> }>('/work-record/list'),
      request<Array<Record<string, unknown>>>('/hazard/list'),
      request<{ results?: Array<Record<string, unknown>> }>('/knowledge/regulations/list'),
      // 资料库文件夹：两步（/kb/mine 拿 kbId → /kb/{id}/folders 拿树），失败降级为空
      (async () => {
        const kb = await request<{ id: number }>('/kb/mine')
        if (!kb || !kb.id) return []
        // 下钻文件列表要用到 kbId，先记下来
        this.setData({ atKbId: kb.id })
        return await request<{ items: Array<Record<string, unknown>> }>(`/kb/${kb.id}/folders`)
      })(),
    ])
    const items: AtBizItem[] = []
    const ok = (i: number) => results[i].status === 'fulfilled'
      ? (results[i] as PromiseFulfilledResult<unknown>).value : []
    // 后端返回结构不统一：work-record/list={records:[]}、regulations/list={results:[]}、folders={items:[]}、hazard/list=[]，这里统一摊平成数组
    const asArray = (v: unknown): Array<Record<string, unknown>> => {
      if (Array.isArray(v)) return v as Array<Record<string, unknown>>
      if (v && typeof v === 'object') {
        const o = v as Record<string, unknown>
        if (Array.isArray(o.records)) return o.records as Array<Record<string, unknown>>
        if (Array.isArray(o.results)) return o.results as Array<Record<string, unknown>>
        if (Array.isArray(o.items)) return o.items as Array<Record<string, unknown>>
      }
      return []
    }
    const label = (r: Record<string, unknown>, keys: string[]) => {
      for (const k of keys) if (r[k]) return String(r[k])
      return ''
    }
    // 工作记录
    for (const r of asArray(ok(0))) {
      const id = r.id as number | string
      const title = label(r, ['content', 'title', 'work_content', 'business_content']).slice(0, 24) || '工作记录'
      items.push({ key: `record-${id}`, icon: '📝', label: label(r, ['record_type_name', 'record_type']) || '工作记录',
        desc: `${title}${r.project_name ? ' · ' + String(r.project_name) : ''}`, ref: `@[记录#${id} ${title}]` })
    }
    // 隐患
    for (const r of asArray(ok(1))) {
      const id = r.id as number | string
      const title = label(r, ['hazard_name', 'risk_category', 'description', 'title']).slice(0, 24) || '隐患'
      items.push({ key: `hazard-${id}`, icon: '⚠️', label: '隐患', desc: title, ref: `@[隐患#${id} ${title}]` })
    }
    // 规范资料
    for (const r of asArray(ok(2))) {
      const name = label(r, ['name', 'title', 'filename', 'file_name']).slice(0, 30)
      if (!name) continue
      const id = r.id ?? r.filename ?? r.file_name ?? name
      items.push({ key: `reg-${id}`, icon: '📚', label: '规范', desc: name, ref: `@[规范 ${name}]` })
    }
    // 资料库文件夹：树形拍平，带层级路径 + 文档数，插到业务引用最前（用户最常按文件夹找资料）
    // 点文件夹不再直接插入「整个文件夹」引用，而是下钻到里面选具体文件（2026-11-19）。
    const folderItems: AtBizItem[] = []
    const flattenFolder = (nodes: Array<Record<string, unknown>>, prefix: string) => {
      for (const f of nodes) {
        const name = String(f.name || '').trim()
        if (!name) continue
        const path = prefix ? `${prefix}/${name}` : name
        const count = Number(f.count || 0)
        const children = Array.isArray(f.children) ? f.children as Array<Record<string, unknown>> : []
        folderItems.push({ key: `kb-${f.id}`, icon: '📁', label: path,
          desc: count ? `${count} 份资料` : (children.length ? `${children.length} 个子文件夹` : '空文件夹'),
          ref: `@[资料库 ${path}]`, kind: 'folder', folderId: Number(f.id), path, children })
        if (children.length) flattenFolder(children, path)
      }
    }
    flattenFolder(asArray(ok(3)), '')
    items.unshift(...folderItems)
    const finalItems = items.slice(0, 60)
    this.setData({ atBiz: finalItems, atVisibleItems: finalItems, atLoading: false })
    // 打字唤起时先到了空列表，加载完成后补一次过滤
    if (this.data.atQuery) this.runAtFilter(this.data.atQuery)
  },

  // 点选：把引用插进输入框，关闭面板并聚焦
  pickAtBiz(event: WechatMiniprogram.TouchEvent) {
    const key = event.currentTarget.dataset.key as string
    // 下钻后列表来自 atDrillItems，未下钻时来自 atBiz
    const pool = this.data.atDrillStack.length ? this.data.atDrillItems : this.data.atBiz
    const item = pool.find(i => i.key === key)
    if (!item) return
    // 文件夹：进入下一层选具体文件，而不是直接引用整层
    if (item.kind === 'folder') {
      void this.openAtFolder(item)
      return
    }
    if (this.atOpenedByTyping) {
      this.replaceAtTrigger(`${item.ref} `)
      return
    }
    this.insertAtText(`${item.ref} `, false)
  },

  /** 进入资料库文件夹：列出子文件夹 + 该层文件（文件可单独 @ 引用） */
  async openAtFolder(item: AtBizItem) {
    const kbId = this.data.atKbId
    if (!kbId || !item.folderId) return
    const path = item.path || item.label
    const subItems: AtBizItem[] = []
    for (const child of item.children || []) {
      const name = String(child.name || '').trim()
      if (!name) continue
      const childPath = `${path}/${name}`
      const childChildren = Array.isArray(child.children) ? child.children as Array<Record<string, unknown>> : []
      const count = Number(child.count || 0)
      subItems.push({
        key: `kb-${child.id}`, icon: '📁', label: name,
        desc: count ? `${count} 份资料` : (childChildren.length ? `${childChildren.length} 个子文件夹` : '空文件夹'),
        ref: `@[资料库 ${childPath}]`, kind: 'folder', folderId: Number(child.id),
        path: childPath, children: childChildren,
      })
    }

    this.setData({ atDrillLoading: true })
    let docItems: AtBizItem[] = []
    try {
      const res = await request<{ items?: Array<Record<string, unknown>> }>(
        `/kb/${kbId}/documents?folder_id=${item.folderId}`,
      )
      const docs = Array.isArray(res?.items) ? res.items.slice(0, 100) : []
      docItems = docs.map(d => {
        const name = String(d.name || d.filename || '未命名文件')
        return {
          key: `doc-${d.id}`, icon: '📄', label: name,
          desc: String(d.filename || ''), ref: `@[文件#${d.id} ${name}]`, kind: 'doc',
          docId: Number(d.id),
        } as AtBizItem
      })
    } catch {
      docItems = []
    }

    // 第一行固定给「引用整个文件夹」，其余是子文件夹 + 文件
    const items: AtBizItem[] = [
      {
        key: `kb-ref-${item.folderId}`, icon: '📎', label: '引用整个文件夹',
        desc: `${path}（读取其中全部资料）`, ref: `@[资料库 ${path}]`, kind: 'folder-ref',
      },
      ...subItems, ...docItems,
    ]
    const stack = this.data.atDrillStack.concat([{ path, items }])
    this.setData({
      atDrillStack: stack, atDrillItems: items, atVisibleItems: items,
      atDrillPath: path, atQuery: '', atDrillLoading: false,
    })
  },

  /** 下钻返回：回到上一层，栈空时回到全部引用 */
  atDrillBack() {
    const stack = this.data.atDrillStack.slice(0, -1)
    if (!stack.length) {
      this.setData({
        atDrillStack: [], atDrillItems: [], atDrillPath: '',
        atVisibleItems: this.data.atBiz, atQuery: '',
      })
      return
    }
    this.setData({
      atDrillStack: stack, atVisibleItems: stack[stack.length - 1].items,
      atDrillPath: stack[stack.length - 1].path, atQuery: '',
    })
  },

  /** 打字唤起面板后的点选：把输入串末尾的「@关键词」整段替换为选中项 */
  replaceAtTrigger(text: string) {
    const current = this.data.input
    const next = current.replace(/@([^\s@]*)$/, text)
    this.setData({ input: next, canSend: true, showAtPanel: false, atQuery: '', draftFocus: false })
    this.atOpenedByTyping = false
    setTimeout(() => this.setData({ draftFocus: true }), 60)
  },

  // 统一插入：追加到现有输入末尾（已有的话留一个空格），重算 canSend
  insertAtText(text: string, _closePanel: boolean) {
    const current = this.data.input
    const next = current ? `${current.replace(/\s+$/, '')} ${text}` : text
    // focus 先 false 再 true：若本来就是 true，直接 setData true 不会触发 textarea 重新聚焦
    this.setData({ input: next, canSend: true, showAtPanel: false, draftFocus: false })
    setTimeout(() => this.setData({ draftFocus: true }), 60)
  },

  onInput(event: WechatMiniprogram.Input) {
    const input = event.detail.value
    this.setData({ input, canSend: Boolean(input.trim()) || this.data.attachments.length > 0 })
    this.detectAtTrigger(input)
  },

  /**
   * 打字打出 @ 自动唤起引用面板（2026-11-19）。
   * 触发口径：输入串以「@」结尾，或末段存在「空白+@关键词」且光标（=输入串末尾）就在
   * 关键词后 —— 即用户正在打一个新的 @。已通过面板点选插入的引用（@张三 ）后面
   * 跟了空格，不满足「无空格的 @词在末尾」，不会重复弹。
   * 面板已开时，把 @ 后的关键词同步进面板搜索；删掉 @ 则收起。
   */
  detectAtTrigger(input: string) {
    // 光标位置 = 输入串末尾（textarea 无 selectionStart 事件详情时按此近似；
    // 我们只关心「正在末尾打字」的场景，中间编辑不触发也合理）
    const m = /(^|\s)@([^\s@]*)$/.exec(input)
    if (!m) {
      // 没有 @ 在打：已开的面板收起（用户删掉了触发词）
      if (this.data.showAtPanel && this.atOpenedByTyping) {
        this.setData({ showAtPanel: false, atQuery: '' })
        this.atOpenedByTyping = false
      }
      return
    }
    const keyword = m[2]
    const opening = !this.data.showAtPanel
    if (opening) {
      // 打字唤起：直接进资料/记录引用列表
      this.atOpenedByTyping = true
      this.setData({
        showAtPanel: true,
        showModelMenu: false,
        atQuery: keyword,
        atVisibleItems: this.data.atBiz,
      })
      void this.loadAtData()
      if (keyword) this.runAtFilter(keyword)
    } else if (this.atOpenedByTyping) {
      // 面板已开（打字开的）：关键词实时同步进搜索并过滤
      this.setData({ atQuery: keyword })
      if (this.atSearchTimer !== null) {
        clearTimeout(this.atSearchTimer)
      }
      this.atSearchTimer = setTimeout(() => {
        this.atSearchTimer = null
        this.runAtFilter(this.data.atQuery)
      }, 120)
    }
  },

  /** @ 触发词过滤：与 onAtSearch 同一套匹配逻辑（打字唤起路径复用） */
  runAtFilter(rawQuery: string) {
    const q = (rawQuery || '').trim().toLowerCase()
    const source = this.data.atDrillStack.length ? this.data.atDrillItems : this.data.atBiz
    const match = (s: string) => !q || s.toLowerCase().includes(q)
    const visible = source.filter(i => match(i.label) || match(i.desc))
    this.setData({ atVisibleItems: visible })
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
    let handedOffToSend = false
    try {
      const result = await uploadQueue.retryOne(pending[0].id)
      const text = result?.text || ''
      if (!text) throw new Error('没有识别到有效语音')
      if (this.data.voiceSessionActive) {
        this.setData({ voiceSessionTexts: [...this.data.voiceSessionTexts, text] })
        wx.showToast({ title: '语音片段已记录', icon: 'success' })
      } else {
        this.setData({ input: text, loading: false })
        // 🔴 同 onStop：不清 loading 会让 send() 防重入直接 return（自锁）；
        // 交接后 loading 由 send 下游自管
        handedOffToSend = true
        await this.send()
      }
    } catch (error) {
      if (handedOffToSend) this.setData({ loading: false })
      wx.showToast({ title: (error as Error).message, icon: 'none' })
    } finally {
      this.setData({ pendingVoiceRetrying: false })
      if (!handedOffToSend) this.setData({ loading: false })
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
