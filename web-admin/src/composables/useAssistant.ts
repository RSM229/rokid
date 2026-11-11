/**
 * 监理小助手：会话状态与动作分发。
 *
 * 状态放在模块级（单例）：MainLayout 卸载重挂、路由切换都不会丢对话。
 * 会话 id 落 localStorage，后端按 user + project 存展示历史，
 * 打开面板时可以恢复上一次的对话。
 */
import { computed, nextTick, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import {
  assistantApi,
  type AgentCard,
  type AgentAction,
  type AgentChatResponse,
  type ChartSpec,
  type ConversationSummary,
} from '@/api/assistant'
import { resolveAgentUrl, resolveCardTarget } from '@/constants/agentPages'

export interface AssistantMessage {
  id: string
  role: 'user' | 'assistant'
  text: string
  hint: string
  cards: AgentCard[]
  actions: AgentAction[]
  chips: string[]
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

function persistConversationId() {
  if (conversationId.value) localStorage.setItem(CONVERSATION_KEY, conversationId.value)
}

function blankMessage(role: 'user' | 'assistant', text: string): AssistantMessage {
  return {
    id: nextId(),
    role,
    text,
    hint: '',
    cards: [],
    actions: [],
    chips: [],
    undo: null,
    chart: null,
    retryText: '',
    error: false,
    at: nowLabel(),
  }
}

function mapResponse(resp: AgentChatResponse): AssistantMessage {
  const message = blankMessage('assistant', resp.reply || '')
  const cards = resp.cards?.length ? resp.cards : resp.card ? [resp.card] : []
  message.cards = cards
  message.actions = resp.actions || []
  message.chips = resp.chips || []
  message.undo = resp.undo ? { entity: resp.undo.entity, id: resp.undo.id } : null
  message.chart = resp.chart || null
  if (resp.need_more?.length) {
    message.hint = `还缺：${resp.need_more.join('、')}`
  }
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
        .filter((m) => m.text)
        .map((m) => {
          const message = blankMessage(m.role === 'assistant' ? 'assistant' : 'user', m.text)
          return message
        })
    } catch {
      // 会话不存在或已切项目：静默开新会话，不打扰用户
      setConversation(uuid())
    }
  }

  async function send(raw: string, options: { echo?: boolean } = {}) {
    const text = String(raw || '').trim()
    if (!text || busy.value) return
    if (options.echo !== false) {
      messages.value.push(blankMessage('user', text))
    }
    busy.value = true
    await nextTick()
    try {
      const resp = await assistantApi.chat({
        message: text,
        conversation_id: ensureConversation(),
        project_id: auth.currentProjectId ?? undefined,
        mode: 'web',
      })
      messages.value.push(mapResponse(resp))
    } catch (error) {
      const message = blankMessage('assistant', '请求失败。')
      message.error = true
      message.retryText = text
      message.hint = '网络或服务异常，可直接重试。'
      messages.value.push(message)
    } finally {
      busy.value = false
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
        .filter((m) => m.text)
        .map((m) => blankMessage(m.role === 'assistant' ? 'assistant' : 'user', m.text))
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
    send,
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
