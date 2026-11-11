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
  messages: Array<{ role: string; text: string; at?: string; images?: string[] }>
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
