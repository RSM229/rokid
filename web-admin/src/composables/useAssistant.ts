/**
 * 监理小助手：会话状态与动作分发。
 *
 * 状态放在模块级（单例）：MainLayout 卸载重挂、路由切换都不会丢对话。
 * 会话 id 落 localStorage，后端按 user + project 存展示历史，
 * 打开面板时可以恢复上一次的对话。
 */
import { computed, nextTick, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { hazardApi } from '@/api'
import {
  assistantApi,
  chatStream,
  type AgentCard,
  type AgentAction,
  type AgentChatResponse,
  type ChartSpec,
  type Citation,
  type ConversationSummary,
} from '@/api/assistant'
import { resolveAgentUrl, resolveCardTarget } from '@/constants/agentPages'

export interface AssistantMessage {
  id: string
  role: 'user' | 'assistant'
  text: string
  hint: string
  image: string
  /** 思考过程（纯文本，不渲染 Markdown） */
  reasoning: string
  /** 进度步骤（"正在查询工作记录…"这类，来自后端 progress 事件） */
  steps: string[]
  /** 开始出答案前的思考耗时（ms） */
  thinkingMs: number
  answerStarted: boolean
  streaming: boolean
  cards: AgentCard[]
  actions: AgentAction[]
  chips: string[]
  /** 行内 [[n]] 角标与文末「引用」列表的数据源 */
  citations: Citation[]
  undo: { entity: string; id: number } | null
  chart: ChartSpec | null
  retryText: string
  error: boolean
  at: string
}

const CONVERSATION_KEY = 'agentConversationId'

const messages = ref<AssistantMessage[]>([])
const open = ref(false)
const busy = ref(false)
const historyOpen = ref(false)
const conversations = ref<ConversationSummary[]>([])
const conversationsLoading = ref(false)
const conversationId = ref<string>(localStorage.getItem(CONVERSATION_KEY) || '')
const hydrated = ref(false)
const modelLabel = ref('')

let seq = 0
function nextId(): string {
  seq += 1
  return `m${Date.now().toString(36)}${seq}`
}

function uuid(): string {
  try {
    if (typeof crypto !== 'undefined' && crypto.randomUUID) return crypto.randomUUID()
  } catch {
    /* ignore */
  }
  return `web-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`
}

function nowLabel(): string {
  return new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
}

/** 隐患识别结果 → 对话里的一段文字（与旧工作台的展示口径一致） */
function formatHazardResult(data: any): string {
  const NA = new Set(['不适用', '无', '暂无', '不存在隐患'])
  const clean = (value: unknown) => String(value ?? '').trim()
  const riskStatus = clean(data?.risk_status)
  const negative = /不存在隐患|未发现|无明显隐患/.test(riskStatus)
  const lines: string[] = []
  if (data?.has_hazard && !negative) lines.push('发现隐患')
  const type = clean(data?.hazard_type) || clean(data?.risk_category) || clean(data?.top1_risk)
  if (type && !NA.has(type)) lines.push(`风险：${type}`)
  const description = clean(data?.description).replace(/证据：[\s\S]*$/, '').trim()
  if (description && !NA.has(description) && !/^不适用/.test(description)) lines.push(description)
  if (data?.suggestion) lines.push(`建议：${clean(data.suggestion)}`)
  if (Array.isArray(data?.evidence) && data.evidence.length) {
    lines.push(`证据：${data.evidence.join('、')}`)
  }
  if (Array.isArray(data?.recheck_suggestion) && data.recheck_suggestion.length) {
    lines.push(`复查：${data.recheck_suggestion.join('、')}`)
  }
  if (!lines.length) lines.push('未识别到明显隐患。')
  return lines.join('\n')
}

function persistConversationId() {
  if (conversationId.value) localStorage.setItem(CONVERSATION_KEY, conversationId.value)
}

function blankMessage(role: 'user' | 'assistant', text: string): AssistantMessage {
  return {
    id: nextId(),
    role,
    text,
    hint: '',
    image: '',
    reasoning: '',
    steps: [],
    thinkingMs: 0,
    answerStarted: false,
    streaming: false,
    cards: [],
    actions: [],
    chips: [],
    citations: [],
    undo: null,
    chart: null,
    retryText: '',
    error: false,
    at: nowLabel(),
  }
}

/** 把 /agent/chat 的回执（含流式 done）落到一条消息上 */
function applyResponse(message: AssistantMessage, resp: AgentChatResponse) {
  const cards = resp.cards?.length ? resp.cards : resp.card ? [resp.card] : []
  message.cards = cards
  message.actions = resp.actions || []
  message.chips = resp.chips || []
  message.citations = resp.citations || []
  message.undo = resp.undo ? { entity: resp.undo.entity, id: resp.undo.id } : null
  message.chart = resp.chart || null
  if (resp.need_more?.length) message.hint = `还缺：${resp.need_more.join('、')}`
  if (resp.reply) message.text = resp.reply
  if (!message.text && !message.cards.length) {
    message.text = resp.kind === 'action' ? '已完成。' : '没有找到相关内容。'
  }
  if (resp.degraded) {
    message.hint = message.hint || '模型路由降级，本轮按问答处理。'
  }
  if (resp.conversation_id) {
    conversationId.value = resp.conversation_id
    persistConversationId()
  }
}

function mapResponse(resp: AgentChatResponse): AssistantMessage {
  const message = blankMessage('assistant', '')
  applyResponse(message, resp)
  return message
}

interface StoredMessage {
  role: string
  text: string
  response?: Record<string, any>
}

/** 历史会话 → 消息：把落库的 reasoning/steps/citations/耗时一并还原 */
function messageFromStored(item: StoredMessage): AssistantMessage {
  const role = item.role === 'assistant' ? 'assistant' : 'user'
  const message = blankMessage(role, item.text || '')
  const response = item.response
  if (role === 'assistant' && response && typeof response === 'object') {
    applyResponse(message, response as AgentChatResponse)
    const reasoning = String(response.reasoning || '')
    if (reasoning.trim()) message.reasoning = reasoning
    if (Array.isArray(response.steps)) message.steps = response.steps.map(String).slice(0, 12)
    const thinkingMs = Number(response.thinking_ms || 0)
    if (thinkingMs > 0) message.thinkingMs = thinkingMs
    message.answerStarted = true
  }
  message.streaming = false
  return message
}

export function useAssistant() {
  const route = useRoute()
  const router = useRouter()
  const auth = useAuthStore()

  const contextLabel = computed(() => {
    const pageTitle = String((route.meta as Record<string, unknown>)?.title || '')
    const project = auth.currentProjectName || ''
    return [pageTitle, project].filter(Boolean).join(' · ') || '当前项目'
  })

  const unread = computed(() => {
    if (open.value) return false
    const last = messages.value[messages.value.length - 1]
    return Boolean(last && last.role === 'assistant')
  })

  function setConversation(id: string) {
    conversationId.value = id
    persistConversationId()
  }

  /**
   * 后端只有拿到 conversation_id 才会落展示历史（缺省为 None 时历史不落库），
   * 所以每次请求都必须带一个客户端生成的 id。
   */
  function ensureConversation(): string {
    if (!conversationId.value) setConversation(uuid())
    return conversationId.value
  }

  function newConversation() {
    messages.value = []
    setConversation(uuid())
    historyOpen.value = false
    hydrated.value = true
  }

  function toggle() {
    open.value = !open.value
    if (open.value) {
      hydrated.value = true
      void hydrate()
    }
  }

  function close() {
    open.value = false
  }

  /** 首次打开时恢复上次会话（后端按 user + project 隔离，切换项目会取不到 → 新会话） */
  async function hydrate() {
    if (!conversationId.value || messages.value.length) return
    try {
      const detail = await assistantApi.conversation(conversationId.value)
      if (!detail.messages?.length) return
      messages.value = detail.messages
        .filter((m) => m.text || m.response?.reply)
        .map(messageFromStored)
    } catch {
      // 会话不存在或已切项目：静默开新会话，不打扰用户
      setConversation(uuid())
    }
  }

  async function send(raw: string, options: { echo?: boolean; image?: File } = {}) {
    const text = String(raw || '').trim()
    const image = options.image
    if ((!text && !image) || busy.value) return
    if (options.echo !== false) {
      const userMessage = blankMessage('user', text)
      if (image) userMessage.image = URL.createObjectURL(image)
      messages.value.push(userMessage)
    }
    busy.value = true
    await nextTick()

    // 图片先走 VLM 识别，识别文本作为 material 交给 Agent（模型看不到图，只能读素材）
    let material = ''
    let visionFailed = false
    if (image) {
      try {
        const result = await hazardApi.detect(image)
        material = formatHazardResult(result)
        const recognition = blankMessage('assistant', material)
        recognition.hint = '图片识别结果'
        messages.value.push(recognition)
      } catch (error: any) {
        visionFailed = true
        const detail =
          error?.response?.data?.detail ||
          error?.message ||
          '识别服务暂时不可用。'
        const message = blankMessage('assistant', '图片识别失败。')
        message.hint = String(detail)
        messages.value.push(message)
      }
    }
    const materialText =
      material ||
      (visionFailed
        ? '（用户已上传一张现场图片，但自动隐患识别服务暂时不可用，没有拿到识别结果。）'
        : undefined)

    // 必须是 reactive 代理：流式期间要在 push 之后持续改这条消息（文本/思考/步骤），
    // 直接改 push 进去的原始对象不会触发 Vue 重渲染。
    const message = reactive(blankMessage('assistant', ''))
    message.streaming = true
    messages.value.push(message)
    const startedAt = Date.now()
    let receivedAny = false
    let doneReceived = false

    const payload = {
      message: text || '根据这张图片的识别结果，告诉我下一步该做什么。',
      conversation_id: ensureConversation(),
      project_id: auth.currentProjectId ?? undefined,
      mode: 'web',
      material: materialText,
    }

    const fallback = async () => {
      const resp = await assistantApi.chat(payload)
      applyResponse(message, resp)
      doneReceived = true
    }

    try {
      await chatStream(payload, {
        onMeta: (meta) => {
          if (meta?.conversation_id) setConversation(meta.conversation_id)
        },
        onProgress: (progress) => {
          const title = String(progress?.title || '').trim()
          if (!title) return
          if (message.steps[message.steps.length - 1] !== title) message.steps.push(title)
        },
        onReasoning: (chunk) => {
          receivedAny = true
          message.reasoning += chunk
        },
        onAnswer: (chunk) => {
          receivedAny = true
          if (!message.answerStarted) {
            message.answerStarted = true
            message.thinkingMs = Date.now() - startedAt
          }
          message.text += chunk
        },
        onDone: (result) => {
          doneReceived = true
          applyResponse(message, result)
        },
        onError: (detail) => {
          message.error = true
          message.text = detail || '服务异常'
          message.retryText = text
        },
      })
      if (!doneReceived && !message.error && !message.text) {
        await fallback()
      }
    } catch {
      // 传输层失败（老后端 / 网络 / 401）：没收到任何增量就回退普通接口
      if (!receivedAny && !message.text && !message.answerStarted) {
        try {
          await fallback()
        } catch {
          message.error = true
          message.text = '请求失败。'
          message.retryText = text
          message.hint = '网络或服务异常，可直接重试。'
        }
      } else {
        message.error = true
        message.retryText = text
        if (!message.text) message.text = '连接中断，请重试。'
      }
    } finally {
      message.streaming = false
      if (!message.answerStarted) message.thinkingMs = Date.now() - startedAt
      busy.value = false
    }
  }

  /** 面板头部展示当前文本模型（改模型在「系统设置 → 模型调用设置」） */
  async function loadModelLabel() {
    try {
      const status: any = await assistantApi.modelStatus()
      modelLabel.value = [status?.provider, status?.current_model].filter(Boolean).join(' / ')
    } catch {
      modelLabel.value = ''
    }
  }

  function retry(message: AssistantMessage) {
    if (!message.retryText) return
    const text = message.retryText
    messages.value = messages.value.filter((item) => item.id !== message.id)
    void send(text, { echo: false })
  }

  function runAction(action: AgentAction) {
    if (action.type !== 'navigate') return
    const target = resolveAgentUrl(action.url)
    if (target) {
      close()
      void router.push(target.path)
    }
  }

  function openCard(card: AgentCard) {
    const target = resolveCardTarget(card.kind)
    if (target) {
      close()
      void router.push(target.path)
    }
  }

  async function undoMessage(message: AssistantMessage) {
    if (!message.undo) return
    const target = message.undo
    message.undo = null
    try {
      await assistantApi.undo(target.entity, target.id)
      messages.value.push(blankMessage('assistant', '已撤销。'))
      busy.value = false
    } catch {
      messages.value.push(blankMessage('assistant', '撤销失败，可能已超过 24 小时。'))
    }
  }

  async function loadConversations() {
    conversationsLoading.value = true
    try {
      const resp = await assistantApi.conversations(30)
      conversations.value = resp.conversations || []
    } catch {
      conversations.value = []
    } finally {
      conversationsLoading.value = false
    }
  }

  function toggleHistory() {
    historyOpen.value = !historyOpen.value
    if (historyOpen.value) void loadConversations()
  }

  async function openConversation(id: string) {
    if (!id) return
    try {
      const detail = await assistantApi.conversation(id)
      messages.value = detail.messages
        .filter((m) => m.text || m.response?.reply)
        .map(messageFromStored)
      setConversation(id)
      historyOpen.value = false
    } catch {
      // 历史会话属于旧项目/已删除：留在列表里，不做破坏性处理
    }
  }

  // 切换项目后，旧会话属于旧项目，服务端会拒绝续写；直接开新会话
  watch(
    () => auth.currentProjectId,
    (next, prev) => {
      if (next === prev) return
      messages.value = []
      conversations.value = []
      setConversation(uuid())
    },
  )

  return {
    messages,
    open,
    busy,
    unread,
    historyOpen,
    conversations,
    conversationsLoading,
    conversationId,
    contextLabel,
    modelLabel,
    send,
    loadModelLabel,
    retry,
    toggle,
    close,
    newConversation,
    runAction,
    openCard,
    undoMessage,
    toggleHistory,
    openConversation,
  }
}
