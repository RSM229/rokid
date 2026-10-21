// Review-plan transport and editing rules shared by the work-review page.
import { request } from './request'
import { absoluteMediaUrl } from '../config'
export const FORM_TYPES = [
  '巡视记录', '危大工程巡视记录', '旁站记录', '平行检验记录', '验收记录',
  '材料台账', '监理通知单', '安全隐患整改通知单', '监理日志', '监理月报', '安全监理日志',
]
export const SCENARIOS = ['混合日期与缺失水印', '九张图片 / 九个日期', '同一历史日期 / 多项工作', '资料不足 / 用途不明', '纯文字 / 无图片']
export const FORM_CODE_BY_LABEL: Record<string, string> = {
  '旁站记录': 'gd_b_212_pangzhan', '平行检验记录': 'gd_b_213_pingxing',
  '巡视记录': 'gd_b_214_xunshi', '监理通知单': 'gd_b_215_tongzhi',
  '安全隐患整改通知单': 'gdaq_30308_yinhuan', '验收记录': 'acceptance_record',
  '监理日志': 'gd_b_220_rizhi', '监理月报': 'gd_b_221_yuebao',
  '材料台账': 'gd_b_219_cailiao', '危大工程巡视记录': 'gdaq_30306_weida',
  '安全监理日志': 'anquan_jianli_rizhi',
}
const FORM_LABEL_BY_CODE = Object.fromEntries(Object.entries(FORM_CODE_BY_LABEL).map(([label, code]) => [code, label]))
export interface Evidence {
  id: string
  mediaId?: number
  image: string
  observedDate: string
  note: string
}
export interface WorkEvent {
  id: string
  title: string
  location: string
  date: string
  dateConfirmed: boolean
  dateSource?: string
  evidenceIds: string[]
  /** Legacy form selections are retained for old saved plans, but are not required. */
  forms: string[]
  recordType?: '巡视' | '验收' | '见证' | '旁站' | '待定' | string
  recordTypeConfidence?: number
  recordTypeReason?: string
  fieldSources?: Record<string, string>
  content: string
  fields?: Record<string, Record<string, unknown>>
}
export interface ReviewPlan {
  schemaVersion: 1
  scenario: number
  revision: number
  requestText: string
  evidence: Evidence[]
  events: WorkEvent[]
  unassignedIds: string[]
  confirmedRevision: number | null
  serverId?: number
  serverRevision?: number
  conversationId?: string
  status?: 'draft' | 'confirmed' | 'executed'
  executedRevision?: number | null
  executionResult?: ReviewExecutionResult | null
}

export interface ReviewExecutionRecord {
  event_id: string
  record_id: number
  record_type?: string
  record_date?: string
  media_ids: number[]
  /** Legacy response fields kept for the standalone review page. */
  form_code?: string
  form_name?: string
  document_id?: number
  document_title?: string
  pending_fields?: string[]
}

export interface ReviewExecutionResult {
  plan_id: number
  revision: number
  created_count: number
  records: ReviewExecutionRecord[]
}

export interface ReviewPlanResponse {
  id: number
  conversation_id: string
  revision: number
  status: 'draft' | 'confirmed' | 'executed'
  confirmed_revision: number | null
  executed_revision?: number | null
  execution_result?: ReviewExecutionResult | null
  plan: Record<string, unknown>
  issues: string[]
}

function fromServerPlan(result: ReviewPlanResponse): ReviewPlan {
  const raw = result.plan as Record<string, unknown>
  const evidence = (raw.evidence || []) as Array<Record<string, unknown>>
  const events = (raw.events || []) as Array<Record<string, unknown>>
  const plan: ReviewPlan = {
    schemaVersion: 1,
    scenario: 0,
    revision: result.revision,
    requestText: String(raw.request_text || ''),
    evidence: evidence.map(item => ({
      id: String(item.id || ''), mediaId: item.media_id == null ? undefined : Number(item.media_id), image: absoluteMediaUrl(String(item.image || '')),
      observedDate: String(item.observed_date || ''), note: String(item.note || ''),
    })),
    events: events.map(item => ({
      id: String(item.id || ''), title: String(item.title || ''),
      location: String(item.location || ''), content: String(item.content || ''),
      date: String(item.date || ''), dateConfirmed: item.date_confirmed === true,
      dateSource: String(item.date_source || ''),
      evidenceIds: Array.isArray(item.evidence_ids) ? item.evidence_ids.map(String) : [],
      forms: Array.isArray(item.forms) ? item.forms.map(value => FORM_LABEL_BY_CODE[String(value)] || String(value)) : [],
      recordType: String(item.record_type || '待定'),
      recordTypeConfidence: typeof item.record_type_confidence === 'number' ? item.record_type_confidence : 0,
      recordTypeReason: String(item.record_type_reason || ''),
      fieldSources: item.field_sources && typeof item.field_sources === 'object' ? item.field_sources as Record<string, string> : {},
      fields: item.fields && typeof item.fields === 'object' ? item.fields as Record<string, Record<string, unknown>> : {},
    })),
    unassignedIds: Array.isArray(raw.unassigned_ids) ? raw.unassigned_ids.map(String) : [],
    confirmedRevision: result.confirmed_revision,
    serverId: result.id,
    serverRevision: result.revision,
    conversationId: result.conversation_id,
    status: result.status,
    executedRevision: result.executed_revision == null ? null : Number(result.executed_revision),
    executionResult: result.execution_result || null,
  }
  validatePlan(plan)
  return plan
}

function toServerPlan(plan: ReviewPlan): Record<string, unknown> {
  return {
    schema_version: 1, request_text: plan.requestText,
    evidence: plan.evidence.map(item => ({
      id: item.id, media_id: item.mediaId, image: item.image, observed_date: item.observedDate, note: item.note,
    })),
    events: plan.events.map(item => ({
      id: item.id, title: item.title, location: item.location, content: item.content,
      date: item.date, date_confirmed: item.dateConfirmed,
      date_source: item.dateSource || '',
      evidence_ids: item.evidenceIds, forms: item.forms.map(value => FORM_CODE_BY_LABEL[value] || value),
      record_type: item.recordType || '待定',
      record_type_confidence: item.recordTypeConfidence || 0,
      record_type_reason: item.recordTypeReason || '',
      field_sources: item.fieldSources || {},
      fields: item.fields || {},
    })),
    unassigned_ids: plan.unassignedIds,
  }
}

export async function fetchReviewPlan(id: number): Promise<ReviewPlan> {
  return fromServerPlan(await request<ReviewPlanResponse>(`/agent/review-plans/${id}`))
}

export interface ReviewFormField {
  key: string
  label: string
  type: string
  required?: boolean
  system?: boolean
  options?: string[]
}
export interface ReviewFormSchema { form_code: string; name: string; sections: Array<{ name: string; fields: ReviewFormField[] }> }
export async function fetchReviewFormSchema(formCode: string): Promise<ReviewFormSchema> {
  return request<ReviewFormSchema>(`/provform/schemas/${formCode}`)
}

export async function createReviewPlan(conversationId: string, plan: ReviewPlan): Promise<ReviewPlan> {
  try {
    const result = await request<ReviewPlanResponse>('/agent/review-plans', {
      method: 'POST', data: { conversation_id: conversationId, plan: toServerPlan(plan) },
    })
    return fromServerPlan(result)
  } catch (error) {
    if (!(error instanceof Error) || !error.message.includes('已有整理计划')) throw error
    const existing = (await listReviewPlans(conversationId))[0]
    if (!existing || existing.status !== 'draft' || !existing.serverId) throw error
    const prefix = `turn-${Date.now()}-`
    const idMap = new Map(plan.evidence.map(item => [item.id, prefix + item.id]))
    const incomingEvents = plan.events.map(event => ({
      ...event, id: prefix + event.id,
      evidenceIds: event.evidenceIds.map(id => idMap.get(id) || id),
    }))
    const merged: ReviewPlan = {
      ...existing,
      revision: existing.revision + 1,
      requestText: [existing.requestText, plan.requestText].filter(Boolean).join('\n'),
      evidence: [...existing.evidence, ...plan.evidence.map(item => ({ ...item, id: idMap.get(item.id)! }))],
      events: [...existing.events, ...incomingEvents],
      unassignedIds: [...existing.unassignedIds, ...plan.unassignedIds.map(id => idMap.get(id) || id)],
      confirmedRevision: null,
    }
    validatePlan(merged)
    return saveReviewPlan(merged, existing.serverRevision)
  }
}

export async function saveReviewPlan(plan: ReviewPlan, expectedRevision = plan.serverRevision ?? plan.revision - 1): Promise<ReviewPlan> {
  if (!plan.serverId) throw new Error('整理计划尚未保存到服务端')
  const result = await request<ReviewPlanResponse>(`/agent/review-plans/${plan.serverId}`, {
    method: 'PUT', data: { revision: expectedRevision, plan: toServerPlan(plan) },
  })
  return fromServerPlan(result)
}

export async function listReviewPlans(conversationId?: string): Promise<ReviewPlan[]> {
  const query = conversationId ? `?conversation_id=${encodeURIComponent(conversationId)}` : ''
  const result = await request<{ plans: ReviewPlanResponse[] }>(`/agent/review-plans${query}`)
  return (result.plans || []).map(fromServerPlan)
}

export async function confirmReviewPlan(plan: ReviewPlan, revision = plan.serverRevision ?? plan.revision): Promise<ReviewPlan> {
  if (!plan.serverId) throw new Error('整理计划尚未保存到服务端')
  const result = await request<ReviewPlanResponse>(`/agent/review-plans/${plan.serverId}/confirm`, {
    method: 'POST', data: { revision },
  })
  return fromServerPlan(result)
}

export async function executeReviewPlan(plan: ReviewPlan, revision = plan.serverRevision ?? plan.revision): Promise<ReviewPlan> {
  if (!plan.serverId) throw new Error('整理计划尚未保存到服务端')
  const result = await request<ReviewPlanResponse>(`/agent/review-plans/${plan.serverId}/execute`, {
    method: 'POST', data: { revision },
  })
  return fromServerPlan(result)
}

export function newEvent(id: string, evidenceIds: string[] = [], date = ''): WorkEvent {
  return { id, title: '现场事项（待核对）', location: '', date, dateConfirmed: Boolean(date), dateSource: date ? 'watermark' : '', evidenceIds, forms: [], recordType: '待定', recordTypeConfidence: 0, recordTypeReason: '', fieldSources: {}, content: '' }
}

export function createExample(scenario = 0): ReviewPlan {
  const evidence = Array.from({ length: scenario === 4 ? 0 : 9 }, (_, i) => {
    const observedDate = scenario === 1 ? `2026-10-${String(12 + i).padStart(2, '0')}`
      : scenario === 2 ? '2026-10-18' : scenario === 3 ? '' : i < 3 ? '2026-10-18' : i < 6 ? '2026-10-19' : ''
    return { id: `photo-${i + 1}`, image: `/assets/work-review/photo-${i + 1}.jpg`, observedDate, note: observedDate ? `模拟提取日期：${observedDate}` : '示例：无可确认日期' }
  })
  let events: WorkEvent[]
  if (scenario === 1) {
    events = evidence.map((photo, i) => newEvent(`event-${i + 1}`, [photo.id], photo.observedDate))
  } else if (scenario === 3) {
    events = []
  } else if (scenario === 4) {
    events = [
      { ...newEvent('event-1'), title: '钢筋验收', forms: ['验收记录'], content: '用户描述：上午进行了钢筋验收，参验人员和结论待补充。' },
      { ...newEvent('event-2'), title: '混凝土浇筑旁站', forms: ['旁站记录'], content: '用户描述：下午进行了混凝土浇筑旁站，时间、部位和现场情况待补充。' },
    ]
  } else {
    events = [0, 1, 2].map((group) => newEvent(`event-${group + 1}`, evidence.slice(group * 3, group * 3 + 3).map(photo => photo.id), evidence[group * 3].observedDate))
  }
  return {
    schemaVersion: 1, scenario, revision: 1,
    requestText: scenario === 4 ? '记录今天的工作：上午钢筋验收，下午混凝土浇筑旁站。' : '记录今天的工作',
    evidence, events, unassignedIds: scenario === 3 ? evidence.map(photo => photo.id) : [], confirmedRevision: null,
  }
}

export function candidateDates(plan: ReviewPlan, event: WorkEvent): string[] {
  return [...new Set(plan.evidence.filter(photo => event.evidenceIds.includes(photo.id)).map(photo => photo.observedDate).filter(Boolean))].sort()
}

// Every material must belong to exactly one event or the unassigned pool in this prototype.
export function validatePlan(plan: ReviewPlan): void {
  if (plan.schemaVersion !== 1 || !Number.isInteger(plan.revision) || plan.revision < 1) throw new Error('草稿版本无效')
  if (!Number.isInteger(plan.scenario) || plan.scenario < 0 || plan.scenario >= SCENARIOS.length || typeof plan.requestText !== 'string') throw new Error('草稿内容无效')
  if (new Set(plan.events.map(event => event.id)).size !== plan.events.length) throw new Error('事项标识重复')
  const ids = plan.evidence.map(photo => photo.id)
  if (new Set(ids).size !== ids.length) throw new Error('资料标识重复')
  for (const photo of plan.evidence) {
    if (!photo.id || typeof photo.image !== 'string' || !validDateOrEmpty(photo.observedDate) || typeof photo.note !== 'string') throw new Error('资料信息无效')
  }
  const assigned = [...plan.events.flatMap(event => event.evidenceIds), ...plan.unassignedIds]
  if (assigned.length !== ids.length || new Set(assigned).size !== ids.length || assigned.some(id => !ids.includes(id))) throw new Error('资料归属不完整或重复')
  for (const event of plan.events) {
    if (typeof event.id !== 'string' || typeof event.title !== 'string' || typeof event.location !== 'string' || typeof event.content !== 'string' || typeof event.dateConfirmed !== 'boolean') throw new Error('事项数据无效')
    if (!validDateOrEmpty(event.date) || (event.dateConfirmed && !event.date)) throw new Error('日期无效')
    if (event.recordType && !['巡视', '验收', '见证', '旁站', '待定'].includes(event.recordType)) throw new Error('记录类型无效')
    if (event.recordTypeConfidence != null && (typeof event.recordTypeConfidence !== 'number' || event.recordTypeConfidence < 0 || event.recordTypeConfidence > 1)) throw new Error('记录类型置信度无效')
    if (new Set(event.forms).size !== event.forms.length || event.forms.some(form => !FORM_TYPES.includes(form))) throw new Error('表式无效')
  }
  if (plan.confirmedRevision !== null && plan.confirmedRevision !== plan.revision) throw new Error('确认版本已过期')
}

function validDateOrEmpty(value: string): boolean {
  if (value === '') return true
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return false
  const parsed = new Date(`${value}T00:00:00Z`)
  return Number.isFinite(parsed.getTime()) && parsed.toISOString().slice(0, 10) === value
}

function revised(plan: ReviewPlan, patch: Partial<ReviewPlan>): ReviewPlan {
  const next = { ...plan, ...patch, revision: plan.revision + 1, confirmedRevision: null }
  validatePlan(next)
  return next
}

export function editRequest(plan: ReviewPlan, requestText: string): ReviewPlan {
  return revised(plan, { requestText })
}
export function editEvent(plan: ReviewPlan, id: string, patch: Partial<Pick<WorkEvent, 'title' | 'location' | 'date' | 'dateConfirmed' | 'dateSource' | 'forms' | 'content' | 'recordType' | 'recordTypeConfidence' | 'recordTypeReason'>>): ReviewPlan {
  if (!plan.events.some(event => event.id === id)) throw new Error('事项不存在')
  return revised(plan, { events: plan.events.map(event => event.id === id ? { ...event, ...patch } : event) })
}
export function editFormField(plan: ReviewPlan, id: string, formCode: string, key: string, value: unknown): ReviewPlan {
  const event = plan.events.find(item => item.id === id)
  if (!event) throw new Error('事项不存在')
  const fields = { ...(event.fields || {}), [formCode]: { ...(event.fields?.[formCode] || {}), [key]: value } }
  return revised(plan, { events: plan.events.map(item => item.id === id ? { ...item, fields } : item) })
}
export function addEvent(plan: ReviewPlan, id: string): ReviewPlan {
  return revised(plan, { events: [...plan.events, newEvent(id)] })
}
export function removeEvent(plan: ReviewPlan, id: string): ReviewPlan {
  const source = plan.events.find(event => event.id === id)
  if (!source) throw new Error('事项不存在')
  return revised(plan, { events: plan.events.filter(event => event.id !== id), unassignedIds: [...plan.unassignedIds, ...source.evidenceIds] })
}
export function releaseEvidence(plan: ReviewPlan, id: string): ReviewPlan {
  if (!plan.events.some(event => event.evidenceIds.includes(id))) throw new Error('资料未分配')
  return revised(plan, { events: plan.events.map(event => ({ ...event, evidenceIds: event.evidenceIds.filter(value => value !== id) })), unassignedIds: [...plan.unassignedIds, id] })
}
export function assignEvidence(plan: ReviewPlan, id: string, targetId: string): ReviewPlan {
  if (!plan.unassignedIds.includes(id) || !plan.events.some(event => event.id === targetId)) throw new Error('资料或事项不存在')
  return revised(plan, { events: plan.events.map(event => event.id === targetId ? { ...event, evidenceIds: [...event.evidenceIds, id] } : event), unassignedIds: plan.unassignedIds.filter(value => value !== id) })
}
export function splitEvidence(plan: ReviewPlan, id: string, newId: string): ReviewPlan {
  const source = plan.events.find(event => event.evidenceIds.includes(id))
  if (!source || source.evidenceIds.length < 2) throw new Error('这张图片已单独归属一个事项')
  const photo = plan.evidence.find(item => item.id === id)!
  return revised(plan, { events: [
    ...plan.events.map(event => event.id === source.id ? { ...event, evidenceIds: event.evidenceIds.filter(value => value !== id) } : event),
    newEvent(newId, [id], photo.observedDate),
  ] })
}
export function mergeEvents(plan: ReviewPlan, selectedIds: string[], newId: string): ReviewPlan {
  const selected = plan.events.filter(event => selectedIds.includes(event.id))
  if (selected.length < 2) throw new Error('请先选中至少两个事项')
  const fields: Record<string, Record<string, unknown>> = {}
  for (const event of selected) {
    for (const [form, values] of Object.entries(event.fields || {})) {
      fields[form] ||= {}
      for (const [key, value] of Object.entries(values)) {
        const previous = fields[form][key]
        if (previous != null && previous !== '' && value != null && value !== '' && previous !== value) {
          throw new Error(`事项合并时「${form}」字段「${key}」内容冲突，请先分别核对`)
        }
        if (previous == null || previous === '') fields[form][key] = value
      }
    }
  }
  const dates = [...new Set(selected.map(event => event.date))]
  const merged: WorkEvent = {
    ...newEvent(newId, selected.flatMap(event => event.evidenceIds), dates.length === 1 ? dates[0] : ''),
    title: '合并事项（待核对）', forms: [...new Set(selected.flatMap(event => event.forms))],
    recordType: selected.every(event => event.recordType === selected[0].recordType) ? (selected[0].recordType || '待定') : '待定',
    recordTypeConfidence: selected.every(event => event.recordType === selected[0].recordType) ? Math.min(...selected.map(event => event.recordTypeConfidence || 0)) : 0,
    recordTypeReason: '合并事项后需要重新核对记录类型',
    content: selected.map(event => event.content).filter(Boolean).join('\n'),
    location: [...new Set(selected.map(event => event.location).filter(Boolean))].join('；'),
    fields,
  }
  const first = plan.events.findIndex(event => selectedIds.includes(event.id))
  const events = plan.events.filter(event => !selectedIds.includes(event.id))
  events.splice(first, 0, merged)
  return revised(plan, { events })
}

export function reviewIssues(plan: ReviewPlan): string[] {
  const issues: string[] = []
  if (!plan.requestText.trim()) issues.push('请补充本次工作要求')
  if (!plan.events.length) issues.push('尚未确定现场事项')
  if (plan.unassignedIds.length) issues.push(`${plan.unassignedIds.length} 张资料尚未分配`)
  plan.events.forEach((event, i) => {
    if (!event.title.trim()) issues.push(`事项 ${i + 1} 缺少名称`)
    if (!event.evidenceIds.length && !event.content.trim()) issues.push(`事项 ${i + 1} 缺少工作描述或资料`)
  })
  return issues
}
export function confirmPlan(plan: ReviewPlan): ReviewPlan {
  validatePlan(plan)
  const issues = reviewIssues(plan)
  if (issues.length) throw new Error(issues[0])
  return { ...plan, confirmedRevision: plan.revision }
}

export function restorePlan(value: unknown): ReviewPlan | null {
  try {
    const plan = value as ReviewPlan
    validatePlan(plan)
    return plan
  } catch { return null }
}
