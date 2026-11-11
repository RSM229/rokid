/**
 * 监理小助手（/api/agent/*）接口封装。
 *
 * 走 http 实例，自带 token / 权限版本 / 全部项目视角等既有拦截器，
 * 与其它页面同一套登录态。
 */
import http from '@/api'

export interface AgentCard {
  kind: string
  id: number
  title: string
  sub?: string
  status?: string
  time?: string
}

export interface AgentAction {
  type: string
  label: string
  url?: string
}

export interface Citation {
  index: number
  kind: string
  title: string
  sub?: string
  snippet?: string
  target?: string
  ref_id?: string | number | null
}

export interface AgentFormOption {
  value: string
  label: string
}

export interface AgentFormField {
  name: string
  key?: string
  canonical?: string
  label: string
  control: 'text' | 'textarea' | 'select' | 'date' | 'time' | 'number' | 'attachments' | string
  required?: boolean
  value?: string
  options?: Array<string | AgentFormOption>
  placeholder?: string
  hint?: string
}

export interface AgentForm {
  id?: string
  kind?: string
  title?: string
  hint?: string
  fields?: AgentFormField[]
  record_id?: number
  form_code?: string
}

export interface AgentUndo {
  tool?: string
  entity: string
  id: number
}

/** 图表卡：由后端白名单生成，前端只负责渲染，不执行任意配置 */
export interface ChartSpec {
  chartType: 'bar' | 'line' | 'pie'
  title: string
  xAxis?: string[]
  series: Array<{ name: string; data: Array<number | string> }>
  meta?: string
}

/** /agent/chat 回执；字段与小程序端 AgentChatResponse 对齐 */
export interface AgentChatResponse {
  kind: 'action' | 'chat'
  conversation_id?: string
  history_saved?: boolean
  tool?: string | null
  reply: string
  card?: AgentCard | null
  cards?: AgentCard[]
  actions?: AgentAction[]
  need_more?: string[]
  options?: Record<string, string[]>
  chips?: string[]
  citations?: Citation[]
  form?: AgentForm | null
  undo?: AgentUndo | null
  model?: string | null
  degraded?: boolean
  chart?: ChartSpec | null
}

export interface AgentHistoryTurn {
  role: 'user' | 'assistant'
  text: string
}

export interface AgentChatPayload {
  message: string
  request_id?: string
  conversation_id?: string
  history?: AgentHistoryTurn[]
  project_id?: number | null
  mode?: string
  images?: string[]
  material?: string
  prefill?: Record<string, unknown>
  form_values?: Record<string, unknown>
}

export interface ConversationSummary {
  conversation_id: string
  title: string
  message_count: number
  updated_at: string
  updatedAt?: string
}

export interface ConversationDetail {
  conversation_id: string
  title: string
  message_count: number
  updated_at: string
  messages: Array<{
    role: string
    text: string
    at?: string
    images?: string[]
    /** 完整回执（含 reasoning / steps / thinking_ms / citations），用于还原历史 */
    response?: Record<string, any>
  }>
}

export interface AgentStreamHandlers {
  onMeta?: (payload: { conversation_id?: string; request_id?: string }) => void
  onProgress?: (payload: { stage?: string; status?: string; title?: string }) => void
  onReasoning?: (text: string) => void
  onAnswer?: (text: string) => void
  onDone?: (result: AgentChatResponse) => void
  onError?: (message: string) => void
}

/**
 * 流式对话（SSE over fetch）。
 *
 * 不用 EventSource：它不支持 POST + Authorization。这里按 `\n\n` 切事件块，
 * 分发 meta/progress/reasoning/answer/done/error；传输层失败才抛错，
 * 业务 error 事件通过 onError 回调，调用方据此决定是否降级到 /agent/chat。
 */
export async function chatStream(
  payload: AgentChatPayload,
  handlers: AgentStreamHandlers,
  signal?: AbortSignal,
): Promise<void> {
  const token = localStorage.getItem('token') || ''
  const response = await fetch('/api/agent/chat/stream', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify(payload),
    signal,
  })
  if (!response.ok || !response.body) {
    throw new Error(`stream_http_${response.status}`)
  }
  const reader = response.body.getReader()
  const decoder = new TextDecoder('utf-8')
  let buffer = ''

  const dispatch = (block: string) => {
    let event = ''
    const dataLines: string[] = []
    for (const line of block.split('\n')) {
      if (line.startsWith('event:')) event = line.slice(6).trim()
      else if (line.startsWith('data:')) dataLines.push(line.slice(5).trim())
    }
    if (!event) return
    let data: Record<string, any> = {}
    if (dataLines.length) {
      try {
        data = JSON.parse(dataLines.join('\n'))
      } catch {
        return
      }
    }
    if (event === 'meta') handlers.onMeta?.(data)
    else if (event === 'progress') handlers.onProgress?.(data)
    else if (event === 'reasoning') {
      if (data.text) handlers.onReasoning?.(String(data.text))
    } else if (event === 'answer') {
      if (data.text) handlers.onAnswer?.(String(data.text))
    } else if (event === 'done') handlers.onDone?.(data as AgentChatResponse)
    else if (event === 'error') handlers.onError?.(String(data.message || '服务异常'))
  }

  while (true) {
    const { value, done } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    let index = buffer.indexOf('\n\n')
    while (index >= 0) {
      dispatch(buffer.slice(0, index))
      buffer = buffer.slice(index + 2)
      index = buffer.indexOf('\n\n')
    }
  }
  if (buffer.trim()) dispatch(buffer)
}

export const assistantApi = {
  // 工具轮（建记录/生成文书）可能跑 30~60s，默认 30s 不够
  chat: (payload: AgentChatPayload) => http.post<AgentChatResponse>('/agent/chat', payload, { timeout: 180000 }),
  /** 当前文本模型（只读；切换在「系统设置 → 模型调用设置」） */
  modelStatus: () =>
    http.get<{
      configured?: boolean
      enabled?: boolean
      provider?: string
      current_model?: string
      model_available?: boolean
    }>('/local/llm/status'),
  conversations: (limit = 30) =>
    http.get<{ conversations: ConversationSummary[]; project_id?: number | null }>(
      '/agent/conversations',
      { params: { limit } },
    ),
  conversation: (id: string) =>
    http.get<ConversationDetail>(`/agent/conversations/${encodeURIComponent(id)}`),
  removeConversation: (id: string) =>
    http.delete<{ ok: boolean }>(`/agent/conversations/${encodeURIComponent(id)}`),
  undo: (entity: string, id: number) =>
    http.post<Record<string, unknown>>('/agent/undo', { entity, id }),
}
