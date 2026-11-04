// Review-plan transport and editing rules shared by the work-review page.
import { ApiRequestError, request } from './request'
import { absoluteMediaUrl } from '../config'
import { isExecutionRecoverable, waitForAgentExecution } from './agent-execution'
export const FORM_TYPES = [
  '巡视记录', '危大工程巡视记录', '旁站记录', '平行检验记录', '验收记录', '见证取样记录',
  '起重机械安拆检查',
  '材料台账', '监理通知单', '安全隐患整改通知单', '监理日志', '监理月报', '安全监理日志',
]
export const SCENARIOS = ['混合日期与缺失水印', '九张图片 / 九个日期', '同一历史日期 / 多项工作', '资料不足 / 用途不明', '纯文字 / 无图片']
export const FORM_CODE_BY_LABEL: Record<string, string> = {
  '旁站记录': 'gd_b_212_pangzhan', '平行检验记录': 'gd_b_213_pingxing',
  '巡视记录': 'gd_b_214_xunshi', '监理通知单': 'gd_b_215_tongzhi',
  '安全隐患整改通知单': 'gdaq_30308_yinhuan', '验收记录': 'acceptance_record',
  '监理日志': 'gd_b_220_rizhi', '监理月报': 'gd_b_221_yuebao',
  '材料台账': 'gd_b_219_cailiao', '危大工程巡视记录': 'gdaq_30306_weida',
  '见证取样记录': 'witness_record',
  '起重机械安拆检查': 'gdaq_30305_qizhong',
  '安全监理日志': 'anquan_jianli_rizhi',
}
const FORM_LABEL_BY_CODE = Object.fromEntries(Object.entries(FORM_CODE_BY_LABEL).map(([label, code]) => [code, label]))
export interface Evidence {
  id: string
  mediaId?: number
  image: string
  observedDate: string
  observedDateSource?: 'watermark' | 'exif' | 'user_message' | string
  project?: string
  section?: string
  location?: string
  activity?: string
  observation?: string
  watermarkAvailable?: boolean
  watermarkStatus?: 'recognized' | 'empty' | 'unavailable'
  watermarkFields?: Record<string, string>
  watermarkItems?: Array<{ label: string; value: string }>
  note: string
}
export interface WorkEvent {
  id: string
  title: string
  location: string
  section?: string
  date: string
  dateConfirmed: boolean
  dateSource?: string
  evidenceIds: string[]
  /** Legacy form selections are retained for old saved plans, but are not required. */
  forms: string[]
  recordType?: '巡视' | '验收' | '见证' | '旁站' | '平行检验' | '起重机械安拆检查' | '待定' | string
  recordTypeConfirmed?: boolean
  recordTypeConfidence?: number
  recordTypeReason?: string
  fieldSources?: Record<string, string>
  content: string
  fields?: Record<string, Record<string, unknown>>
  /** Fields the planner could not establish from user text, watermark/EXIF or vision. */
  missingFields?: string[]
  /** Item-level conflicts and caveats retained for review in the conversation. */
  issues?: string[]
  itemStatus?: string
  /** Form-relevant scalar facts resolved from the item plan (project/units/person). */
  entityFields?: Record<string, string>
  /** Candidate values that require an explicit user decision. */
  fieldCandidates?: Record<string, string[]>
  /** Candidate fields with more than one known value that are not yet confirmed. */
  unresolvedConflicts?: string[]
  /** Canonical form fields resolved by the backend. */
  formCode?: string
  formFields?: ReviewFormFieldState[]
  formMissingFields?: string[]
  recordDraft?: RecordDraftPreview
}

export interface ReviewFormFieldState {
  key: string
  canonical: string
  label: string
  section?: string
  control: 'text' | 'textarea' | 'number' | 'date' | 'datetime-local' | 'select' | string
  options?: string[]
  question?: string
  required: boolean
  readonly: boolean
  value: string
  source?: string
  status?: string
  missing: boolean
  conflict?: boolean
  validationError?: string
  candidates?: Array<{ value: string; label?: string; evidence_id?: string }>
}
export interface RecordDraftPreview {
  status: string
  recordType: string
  recordTypeConfirmed: boolean
  formCode: string
  formName: string
  contractVersion: string
  recordDate: string
  section: string
  location: string
  content: string
  fields: Record<string, string>
  blockers: Array<{ kind: string; canonical: string; label: string; message?: string }>
  generationAllowed: boolean
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
  /** 对话内解释证据冲突或需要确认的原因。 */
  notices?: string[]
  /** Immutable request-plan snapshot used to create this draft. */
  requestPlan?: Record<string, unknown>
  /** Form-schema fingerprints retained across edits to detect contract drift. */
  formContracts?: Record<string, string>
  /** Trace/operation id propagated through planning, confirmation and execution. */
  requestId?: string
}

export interface RequestPlanFact {
  field: string
  value: unknown
  source: string
  status: string
  confidence?: number
}

export interface RequestPlanField {
  value: unknown
  source: string
  status: string
  candidates?: unknown[]
}

export interface RequestPlanItem {
  id: string
  record_type?: string | null
  record_type_confidence?: number
  fields: Record<string, RequestPlanField>
  evidence_indexes: number[]
  notes?: string[]
  missing_fields?: string[]
  issues?: string[]
  confidence?: number
  status?: string
}

export interface RequestPlanItemPlan {
  items: RequestPlanItem[]
  candidate_count?: number
  unassigned_evidence_indexes: number[]
  requires_confirmation?: boolean
  issues?: string[]
  grouping_fields?: string[]
  write_allowed?: boolean
}

export interface ReviewEvidenceGroup {
  date: string
  project: string
  location: string
  section: string
  evidenceIds: string[]
}

export interface ReviewEvidenceTarget {
  date?: string
  project?: string
  location?: string
  section?: string
}

export interface ReviewUserIdentityFacts {
  date?: string
  location?: string
}

export interface ReviewIdentitySelection {
  date: string
  location: string
  dateSource: string
  locationSource: string
  userDateConflict: boolean
  userLocationConflict: boolean
}

export interface ReviewExecutionRecord {
  event_id: string
  record_id: number
  record_type?: string
  record_date?: string
  media_ids: number[]
  business_type?: 'work_record' | 'parallel_inspection' | string
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
  request_id?: string
  execution_id?: string
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
      observedDate: String(item.observed_date || ''), observedDateSource: String(item.observed_date_source || ''), note: String(item.note || ''),
      project: String(item.project || ''), section: String(item.section || ''),
      location: String(item.location || ''), activity: String(item.activity || ''), observation: String(item.observation || ''),
      watermarkAvailable: item.watermark_available === true,
      watermarkStatus: item.watermark_status === 'recognized' || item.watermark_status === 'empty' ? item.watermark_status : 'unavailable',
      watermarkFields: item.watermark_fields && typeof item.watermark_fields === 'object' ? item.watermark_fields as Record<string, string> : {},
      watermarkItems: Array.isArray(item.watermark_items) ? item.watermark_items.map((entry: any) => ({ label: String(entry?.label || ''), value: String(entry?.value || '') })) : [],
    })),
    events: events.map(item => ({
      id: String(item.id || ''), title: String(item.title || ''),
      location: String(item.location || ''), section: String(item.section || ''), content: String(item.content || ''),
      date: String(item.date || ''), dateConfirmed: item.date_confirmed === true,
      dateSource: String(item.date_source || ''),
      evidenceIds: Array.isArray(item.evidence_ids) ? item.evidence_ids.map(String) : [],
      forms: Array.isArray(item.forms) ? item.forms.map(value => FORM_LABEL_BY_CODE[String(value)] || String(value)) : [],
      recordType: String(item.record_type || '待定'),
      recordTypeConfirmed: item.record_type_confirmed === true,
      recordTypeConfidence: typeof item.record_type_confidence === 'number' ? item.record_type_confidence : 0,
      recordTypeReason: String(item.record_type_reason || ''),
      fieldSources: item.field_sources && typeof item.field_sources === 'object' ? item.field_sources as Record<string, string> : {},
      fields: item.fields && typeof item.fields === 'object' ? item.fields as Record<string, Record<string, unknown>> : {},
      missingFields: Array.isArray(item.missing_fields) ? item.missing_fields.map(String) : [],
      issues: Array.isArray(item.issues) ? item.issues.map(String) : [],
      itemStatus: String(item.item_status || ''),
      entityFields: item.entity_fields && typeof item.entity_fields === 'object'
        ? Object.fromEntries(Object.entries(item.entity_fields as Record<string, unknown>).map(([key, value]) => [key, String(value ?? '')]))
        : {},
      fieldCandidates: item.field_candidates && typeof item.field_candidates === 'object'
        ? Object.fromEntries(Object.entries(item.field_candidates as Record<string, unknown>).map(([key, value]) => [
          key, Array.isArray(value) ? value.map(String) : [],
        ]))
        : {},
      unresolvedConflicts: Array.isArray(item.unresolved_conflicts) ? item.unresolved_conflicts.map(String) : [],
      formCode: String(item.form_code || ''),
      formFields: Array.isArray(item.form_fields)
        ? item.form_fields.map((field: any) => ({
          key: String(field?.key || ''),
          canonical: String(field?.canonical || ''),
          label: String(field?.label || ''),
          section: String(field?.section || ''),
          control: String(field?.control || 'text'),
          options: Array.isArray(field?.options) ? field.options.map(String) : [],
          question: String(field?.question || ''),
          required: field?.required === true,
          readonly: field?.readonly === true,
          value: String(field?.value || ''),
          source: String(field?.source || ''),
          status: String(field?.status || ''),
          missing: field?.missing === true,
          conflict: field?.conflict === true,
          autoResolved: field?.auto_resolved === true,
          validationError: String(field?.validation_error || ''),
          candidates: Array.isArray(field?.candidates) ? field.candidates.map((candidate: any) => ({
            value: String(candidate?.value || ''),
            label: String(candidate?.label || ''),
            evidence_id: String(candidate?.evidence_id || ''),
          })) : [],
        }))
        : [],
      formMissingFields: Array.isArray(item.form_missing_fields) ? item.form_missing_fields.map(String) : [],
      recordDraft: item.record_draft && typeof item.record_draft === 'object' ? (() => {
        const draft = item.record_draft as Record<string, any>
        return {
        status: String(draft.status || 'needs_record_type'),
        recordType: String(draft.record_type || '待定'),
        recordTypeConfirmed: draft.record_type_confirmed === true,
        formCode: String(draft.form_code || ''),
        formName: String(draft.form_name || ''),
        contractVersion: String(draft.contract_version || ''),
        recordDate: String(draft.record_date || ''),
        section: String(draft.section || ''),
        location: String(draft.location || ''),
        content: String(draft.content || ''),
        fields: draft.fields && typeof draft.fields === 'object'
          ? Object.fromEntries(Object.entries(draft.fields).map(([key, value]) => [key, String(value)]))
          : {},
        blockers: Array.isArray(draft.blockers) ? draft.blockers.map((blocker: any) => ({
          kind: String(blocker?.kind || ''),
          canonical: String(blocker?.canonical || ''),
          label: String(blocker?.label || ''),
          message: String(blocker?.message || ''),
        })) : [],
        generationAllowed: draft.generation_allowed === true,
        }
      })() : undefined,
    })),
    unassignedIds: Array.isArray(raw.unassigned_ids) ? raw.unassigned_ids.map(String) : [],
    confirmedRevision: result.confirmed_revision,
    serverId: result.id,
    serverRevision: result.revision,
    conversationId: result.conversation_id,
    status: result.status,
    executedRevision: result.executed_revision == null ? null : Number(result.executed_revision),
    executionResult: result.execution_result || null,
    notices: Array.isArray(raw.notices) ? raw.notices.map(String) : [],
    requestPlan: raw.request_plan && typeof raw.request_plan === 'object' ? raw.request_plan as Record<string, unknown> : undefined,
    formContracts: raw.form_contracts && typeof raw.form_contracts === 'object'
      ? Object.fromEntries(Object.entries(raw.form_contracts as Record<string, unknown>).map(([key, value]) => [key, String(value)]))
      : {},
    requestId: String(result.request_id || raw.request_id || ''),
  }
  validatePlan(plan)
  return plan
}

function toServerPlan(plan: ReviewPlan): Record<string, unknown> {
  return {
    schema_version: 1, request_text: plan.requestText, request_id: plan.requestId || '',
    evidence: plan.evidence.map(item => ({
      id: item.id, media_id: item.mediaId, image: item.image, observed_date: item.observedDate, observed_date_source: item.observedDateSource || '', note: item.note,
      project: item.project || '', section: item.section || '',
      location: item.location || '', activity: item.activity || '', observation: item.observation || '', watermark_available: item.watermarkAvailable === true,
      watermark_fields: item.watermarkFields || {},
      watermark_items: item.watermarkItems || [],
      watermark_status: item.watermarkStatus || (item.watermarkAvailable ? 'recognized' : 'unavailable'),
    })),
    events: plan.events.map(item => ({
      id: item.id, title: item.title, location: item.location, section: item.section || '', content: item.content,
      date: item.date, date_confirmed: item.dateConfirmed,
      date_source: item.dateSource || '',
      evidence_ids: item.evidenceIds, forms: item.forms.map(value => FORM_CODE_BY_LABEL[value] || value),
      record_type: item.recordType || '待定',
      record_type_confirmed: item.recordTypeConfirmed === true,
      record_type_confidence: item.recordTypeConfidence || 0,
      record_type_reason: item.recordTypeReason || '',
      field_sources: item.fieldSources || {},
      fields: item.fields || {},
      missing_fields: item.missingFields || [],
      issues: item.issues || [],
      item_status: item.itemStatus || '',
      entity_fields: item.entityFields || {},
      field_candidates: item.fieldCandidates || {},
      unresolved_conflicts: item.unresolvedConflicts || [],
    })),
    unassigned_ids: plan.unassignedIds,
    notices: plan.notices || [],
    request_plan: plan.requestPlan || {},
    form_contracts: plan.formContracts || {},
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

export async function createReviewPlan(conversationId: string, plan: ReviewPlan, requestId?: string, images?: string[]): Promise<ReviewPlan> {
  try {
    const result = await request<ReviewPlanResponse>('/agent/review-plans', {
      method: 'POST', data: {
        conversation_id: conversationId, plan: toServerPlan(plan),
        request_id: requestId || plan.requestId,
        images: (images || []).filter(Boolean),
      },
    })
    return fromServerPlan(result)
  } catch (error) {
    if (!(error instanceof ApiRequestError) || error.code !== 'ACTIVE_PLAN_EXISTS') throw error
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
    method: 'POST', data: { revision, request_id: plan.requestId },
  })
  return fromServerPlan(result)
}

export async function executeReviewPlan(plan: ReviewPlan, revision = plan.serverRevision ?? plan.revision): Promise<ReviewPlan> {
  if (!plan.serverId) throw new Error('整理计划尚未保存到服务端')
  const executionId = `review-plan:${plan.serverId}:revision:${revision}`
  try {
    const result = await request<ReviewPlanResponse>(`/agent/review-plans/${plan.serverId}/execute`, {
      method: 'POST',
      data: { revision, request_id: plan.requestId, execution_id: executionId },
      timeout: 120000,
    })
    return fromServerPlan(result)
  } catch (error) {
    if (!isExecutionRecoverable(error)) throw error
    const execution = await waitForAgentExecution(executionId)
    if (execution?.status === 'COMMITTED') {
      const latest = await fetchReviewPlan(plan.serverId)
      return { ...latest, status: 'executed', executionResult: execution.result as unknown as ReviewPlan['executionResult'] }
    }
    const latest = await fetchReviewPlan(plan.serverId).catch(() => null)
    if (latest?.status === 'executed' && latest.executionResult) return latest
    if (execution?.status === 'FAILED') {
      throw new Error(`执行未完成：${execution.error || '服务端已回滚本次写入'}`)
    }
    if (execution?.status === 'RUNNING') {
      throw new Error('服务端仍在确认本次执行结果，请稍后重新进入查看，不会重复建档')
    }
    throw error
  }
}

export function newEvent(id: string, evidenceIds: string[] = [], date = ''): WorkEvent {
  return { id, title: '现场事项（待核对）', location: '', date, dateConfirmed: Boolean(date), dateSource: date ? 'watermark' : '', evidenceIds, forms: [], recordType: '待定', recordTypeConfirmed: false, recordTypeConfidence: 0, recordTypeReason: '', fieldSources: {}, content: '' }
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

function normalizedEvidenceText(value: unknown): string {
  return String(value || '').normalize('NFKC').toLocaleLowerCase().replace(/[\s\p{P}\p{S}]+/gu, '')
}

function compatibleEvidenceText(left: unknown, right: unknown): boolean {
  const a = normalizedEvidenceText(left)
  const b = normalizedEvidenceText(right)
  if (!a || !b || a === b) return true
  return a.includes(b) || b.includes(a)
}

/**
 * Watermark/EXIF identity fields win over a conflicting user retelling.
 * Missing evidence falls back to the user value without fabricating a source.
 */
export function chooseReviewIdentity(
  user: ReviewUserIdentityFacts,
  evidence: Pick<ReviewEvidenceGroup, 'date' | 'location' | 'section'>,
): ReviewIdentitySelection {
  const evidenceDate = String(evidence.date || '')
  const evidenceLocation = String(evidence.location || evidence.section || '')
  const userDate = String(user.date || '')
  const userLocation = String(user.location || '')
  return {
    date: evidenceDate || userDate,
    location: evidenceLocation || userLocation,
    dateSource: evidenceDate ? 'watermark_or_exif' : userDate ? 'user_message' : '',
    locationSource: evidenceLocation
      ? evidence.location ? 'watermark' : 'watermark_section'
      : userLocation ? 'user_message' : '',
    userDateConflict: Boolean(userDate && evidenceDate && !compatibleEvidenceText(userDate, evidenceDate)),
    userLocationConflict: Boolean(
      userLocation && evidenceLocation && !compatibleEvidenceText(userLocation, evidenceLocation),
    ),
  }
}

/**
 * Group evidence only when the known identity fields are compatible.
 * A missing field is unknown, not a conflict. Non-empty differences split.
 */
export function groupReviewEvidence(
  evidence: Evidence[],
  options: { ignoreDate?: boolean } = {},
): ReviewEvidenceGroup[] {
  const groups: ReviewEvidenceGroup[] = []
  for (const item of evidence) {
    const candidate: ReviewEvidenceGroup = {
      date: options.ignoreDate ? '' : String(item.observedDate || ''),
      project: String(item.project || ''),
      location: String(item.location || ''),
      section: String(item.section || ''),
      evidenceIds: [item.id],
    }
    const group = groups.find(existing =>
      compatibleEvidenceText(existing.date, candidate.date) &&
      compatibleEvidenceText(existing.project, candidate.project) &&
      compatibleEvidenceText(existing.location, candidate.location) &&
      compatibleEvidenceText(existing.section, candidate.section),
    )
    if (!group) {
      groups.push(candidate)
      continue
    }
    if (!group.date && candidate.date) group.date = candidate.date
    if (!group.project && candidate.project) group.project = candidate.project
    if (!group.location && candidate.location) group.location = candidate.location
    if (!group.section && candidate.section) group.section = candidate.section
    group.evidenceIds.push(item.id)
  }
  return groups
}

function requestFieldValue(item: RequestPlanItem, key: string): string {
  const field = item.fields?.[key]
  return String(field?.value == null ? '' : field.value).trim()
}

/**
 * Adapt the server-owned item plan into the editable draft consumed by chat.
 * The adapter never invents an event count: item IDs, evidence indexes and
 * unresolved fields all come from the planner. Evidence the planner could not
 * place stays visible in the unassigned pool.
 */
export function reviewPlanFromItemPlan(
  itemPlan: RequestPlanItemPlan,
  evidence: Evidence[],
  requestText: string,
  notices: string[] = [],
  requestPlan?: Record<string, unknown>,
): ReviewPlan {
  const usedEventIds = new Set<string>()
  const events: WorkEvent[] = []
  for (const [index, item] of (itemPlan.items || []).entries()) {
    const baseId = String(item.id || `item-${index + 1}`)
    let eventId = baseId
    let suffix = 2
    while (usedEventIds.has(eventId)) eventId = `${baseId}-${suffix++}`
    usedEventIds.add(eventId)
    const evidenceIds = [...new Set((item.evidence_indexes || [])
      .map(evidenceIndex => evidence[evidenceIndex]?.id)
      .filter((id): id is string => Boolean(id)))]
    const date = requestFieldValue(item, 'record_date')
    const project = requestFieldValue(item, 'project')
    const location = requestFieldValue(item, 'location')
    const section = requestFieldValue(item, 'section')
    const recordType = ['巡视', '验收', '见证', '旁站', '平行检验', '起重机械安拆检查', '待定'].includes(String(item.record_type || ''))
      ? String(item.record_type)
      : '待定'
    const dateField = item.fields?.record_date
    const locationField = item.fields?.location
    const sectionField = item.fields?.section
    const entityFields: Record<string, string> = {}
    const plannedFields: Record<string, unknown> = {}
    const fieldCandidates: Record<string, string[]> = {}
    const unresolvedConflicts: string[] = []
    const canonicalEntityKeys: Record<string, string> = {
      project: 'project_name', project_name: 'project_name', location: 'location', section: 'section',
      owner_unit: 'owner_unit', construction_unit: 'construction_unit',
      supervision_unit: 'supervision_unit', supervision_org: 'supervision_org',
      inspector: 'inspector', weather: 'weather', work_content: 'work_content',
      problems: 'problems', treatment: 'treatment', handling: 'handling', remark: 'remark',
      worker_count: 'worker_count', start_time: 'start_time', end_time: 'end_time',
      station_process: 'station_process', acceptance_type: 'acceptance_type',
      acceptance_content: 'acceptance_content', self_check_conclusion: 'self_check_conclusion',
      supervision_conclusion: 'supervision_conclusion', photo: 'photo',
      material_name: 'material_name', material_spec: 'material_spec', quantity: 'quantity',
      unit: 'unit', manufacturer: 'manufacturer', batch_no: 'batch_no',
      sample_date: 'sample_date', sampler: 'sampler', cert_no: 'cert_no',
      test_result: 'test_result', equipment: 'equipment', material_info: 'material_info',
      qa_onsite: 'qa_onsite', self_check: 'self_check',
    }
    for (const [sourceKey, field] of Object.entries(item.fields || {})) {
      const value = String(field?.value ?? '').trim()
      if (value) {
        plannedFields[sourceKey] = {
          value,
          source: String(field?.source || 'candidate'),
          status: String(field?.status || 'candidate'),
          candidates: [...new Set((field?.candidates || []).map(String).map(candidate => candidate.trim()).filter(Boolean))],
        }
      }
      const key = canonicalEntityKeys[sourceKey]
      if (!key) continue
      if (value) entityFields[key] = value
      const candidates = [...new Set((field?.candidates || []).map(String).map(value => value.trim()).filter(Boolean))]
      if (value && !candidates.includes(value)) candidates.unshift(value)
      if (candidates.length > 1) fieldCandidates[key] = candidates
      if (field?.status === 'conflict') unresolvedConflicts.push(key)
    }
    const notes = (item.notes || []).map(String).filter(Boolean)
    const formCodeByType: Record<string, string> = {
      '巡视': 'gd_b_214_xunshi', '旁站': 'gd_b_212_pangzhan',
      '验收': 'acceptance_record', '见证': 'witness_record', '平行检验': 'gd_b_213_pingxing',
      '起重机械安拆检查': 'gdaq_30305_qizhong',
    }
    const requestedForm = ((item as RequestPlanItem & { forms?: string[] }).forms || [])
      .find(form => Object.values(FORM_CODE_BY_LABEL).includes(String(form)))
    const formCode = requestedForm || formCodeByType[recordType] || '__general__'
    const place = [project, section, location].filter(Boolean).join(' / ')
    const title = notes[0]?.slice(0, 80) || (place ? `${recordType === '待定' ? '现场工作' : recordType} · ${place}` : '')
    events.push({
      id: eventId,
      title: title || requestText.trim().slice(0, 80) || '现场工作事项（待核对）',
      location,
      section,
      date,
      dateConfirmed: Boolean(date),
      dateSource: dateField?.source || '',
      evidenceIds,
      forms: [],
      recordType,
      recordTypeConfirmed: false,
      recordTypeConfidence: typeof item.record_type_confidence === 'number' ? item.record_type_confidence : 0,
      recordTypeReason: recordType === '待定' ? '尚未确定记录类型，请从下方选择' : '由服务端受控动作和语义候选归一化',
      fieldSources: {
        ...(date ? { date: dateField?.source || 'candidate' } : {}),
        ...(location ? { location: locationField?.source || 'candidate' } : {}),
        ...(section ? { section: sectionField?.source || 'candidate' } : {}),
        ...Object.fromEntries(Object.entries(item.fields || {}).map(([key, field]) => [key, String(field?.source || 'candidate')])),
      },
      content: entityFields.work_content || notes[0] || '',
      missingFields: [...new Set((item.missing_fields || []).map(String))],
      issues: [...new Set((item.issues || []).map(String))],
      itemStatus: item.status || '',
      entityFields,
      fieldCandidates,
      unresolvedConflicts: [...new Set(unresolvedConflicts)],
      fields: Object.keys(plannedFields).length ? { [formCode]: plannedFields } : {},
    })
  }

  const knownIds = new Set(evidence.map(item => item.id))
  const assigned = new Set(events.flatMap(event => event.evidenceIds))
  const unassignedIds: string[] = []
  for (const evidenceIndex of itemPlan.unassigned_evidence_indexes || []) {
    const id = evidence[evidenceIndex]?.id
    if (id && knownIds.has(id) && !assigned.has(id) && !unassignedIds.includes(id)) unassignedIds.push(id)
  }
  for (const item of evidence) {
    if (!assigned.has(item.id) && !unassignedIds.includes(item.id)) unassignedIds.push(item.id)
  }

  const plan: ReviewPlan = {
    schemaVersion: 1,
    scenario: 0,
    revision: 1,
    requestText: requestText.trim() || '用户上传资料，尚未说明整理用途',
    evidence,
    events,
    unassignedIds,
    confirmedRevision: null,
    notices: [...new Set([
      ...notices,
      ...(itemPlan.issues || []).map(String),
      ...events.flatMap(event => event.issues || []),
    ].filter(Boolean))],
    requestPlan,
    requestId: String(requestPlan?.request_id || ''),
  }
  validatePlan(plan)
  return plan
}

/** Fields whose known watermark values disagree with the record being amended. */
export function evidenceTargetConflicts(
  evidence: Evidence[],
  target: ReviewEvidenceTarget,
): string[] {
  const labels: Array<[keyof ReviewEvidenceTarget, string, (item: Evidence) => unknown]> = [
    ['date', '日期', item => item.observedDate],
    ['project', '项目', item => item.project],
    ['location', '地点', item => item.location],
    ['section', '部位', item => item.section],
  ]
  const conflicts = new Set<string>()
  for (const item of evidence) {
    for (const [key, label, read] of labels) {
      const expected = String(target[key] || '')
      const actual = String(read(item) || '')
      if (expected && actual && !compatibleEvidenceText(expected, actual)) conflicts.add(label)
    }
  }
  return [...conflicts]
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
    if (event.recordType && !['巡视', '验收', '见证', '旁站', '平行检验', '起重机械安拆检查', '待定'].includes(event.recordType)) throw new Error('记录类型无效')
    if (event.recordTypeConfidence != null && (typeof event.recordTypeConfidence !== 'number' || event.recordTypeConfidence < 0 || event.recordTypeConfidence > 1)) throw new Error('记录类型置信度无效')
    if (new Set(event.forms).size !== event.forms.length || event.forms.some(form => !FORM_TYPES.includes(form))) throw new Error('表式无效')
    if (event.missingFields && (!Array.isArray(event.missingFields) || event.missingFields.some(field => typeof field !== 'string'))) throw new Error('缺项信息无效')
    if (event.issues && (!Array.isArray(event.issues) || event.issues.some(issue => typeof issue !== 'string'))) throw new Error('事项提示无效')
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
export function editEvent(plan: ReviewPlan, id: string, patch: Partial<Pick<WorkEvent, 'title' | 'location' | 'section' | 'date' | 'dateConfirmed' | 'dateSource' | 'forms' | 'content' | 'recordType' | 'recordTypeConfirmed' | 'recordTypeConfidence' | 'recordTypeReason' | 'fieldSources' | 'missingFields' | 'issues' | 'entityFields' | 'fieldCandidates' | 'unresolvedConflicts'>>): ReviewPlan {
  if (!plan.events.some(event => event.id === id)) throw new Error('事项不存在')
  return revised(plan, { events: plan.events.map(event => event.id === id ? { ...event, ...patch } : event) })
}
export function editFormField(plan: ReviewPlan, id: string, formCode: string, key: string, value: unknown): ReviewPlan {
  const event = plan.events.find(item => item.id === id)
  if (!event) throw new Error('事项不存在')
  const fields = {
    ...(event.fields || {}),
    [formCode]: {
      ...(event.fields?.[formCode] || {}),
      [key]: { value, source: 'user_message', status: 'user_confirmed' },
    },
  }
  return revised(plan, { events: plan.events.map(item => item.id === id ? {
    ...item,
    fields,
    fieldSources: { ...(item.fieldSources || {}), [key]: 'user_message' },
    unresolvedConflicts: (item.unresolvedConflicts || []).filter(field => field !== key),
    formFields: (item.formFields || []).map(field => field.canonical === key ? {
      ...field,
      value: String(value ?? ''),
      source: 'user_message',
      status: 'user_confirmed',
      missing: false,
      readonly: false,
    } : field),
  } : item) })
}
export function editEvidenceWatermark(plan: ReviewPlan, id: string, itemIndex: number, key: 'label' | 'value', value: string): ReviewPlan {
  const evidence = plan.evidence.find(item => item.id === id)
  if (!evidence) throw new Error('图片资料不存在')
  const items = [...(evidence.watermarkItems || [])]
  if (!items[itemIndex]) throw new Error('水印字段不存在')
  items[itemIndex] = { ...items[itemIndex], [key]: value }
  return revised(plan, { evidence: plan.evidence.map(item => item.id === id ? { ...item, watermarkItems: items } : item) })
}
export function addEvidenceWatermark(plan: ReviewPlan, id: string): ReviewPlan {
  const evidence = plan.evidence.find(item => item.id === id)
  if (!evidence) throw new Error('图片资料不存在')
  return revised(plan, { evidence: plan.evidence.map(item => item.id === id ? { ...item, watermarkItems: [...(item.watermarkItems || []), { label: '', value: '' }] } : item) })
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
  const descriptiveFields = new Set(['work_content', 'problems', 'treatment', 'handling', 'remark'])
  const entityFields: Record<string, string> = {}
  const fieldCandidates: Record<string, string[]> = {}
  const unresolvedConflicts = new Set(selected.flatMap(event => event.unresolvedConflicts || []))
  for (const key of [...new Set(selected.flatMap(event => Object.keys(event.entityFields || {})))]) {
    const values = [...new Set(
      selected
        .map(event => String(event.entityFields?.[key] || '').trim())
        .filter(Boolean),
    )]
    const candidates = [...new Set(selected.flatMap(event => event.fieldCandidates?.[key] || []))]
    if (!candidates.length) candidates.push(...values)
    if (candidates.length > 1) fieldCandidates[key] = candidates
    if (!values.length) continue
    if (descriptiveFields.has(key)) {
      entityFields[key] = values.join('\n')
    } else {
      // Identity/unit/person fields must remain one exact value. Pick the
      // current preferred value but keep every alternative selectable.
      entityFields[key] = values[0]
      if (values.length > 1) unresolvedConflicts.add(key)
    }
  }
  const merged: WorkEvent = {
    ...newEvent(newId, selected.flatMap(event => event.evidenceIds), dates.length === 1 ? dates[0] : ''),
    title: [...new Set(selected.map(event => event.title).filter(Boolean))].join('；').slice(0, 80) || '合并事项（待核对）',
    forms: [...new Set(selected.flatMap(event => event.forms))],
    recordType: selected.every(event => event.recordType === selected[0].recordType) ? (selected[0].recordType || '待定') : '待定',
    recordTypeConfidence: selected.every(event => event.recordType === selected[0].recordType) ? Math.min(...selected.map(event => event.recordTypeConfidence || 0)) : 0,
    recordTypeReason: '合并事项后需要重新核对记录类型',
    content: selected.map(event => event.content).filter(Boolean).join('\n'),
    location: [...new Set(selected.map(event => event.location).filter(Boolean))].join('；'),
    section: [...new Set(selected.map(event => event.section).filter(Boolean))].join('；'),
    fields,
    missingFields: [...new Set(selected.flatMap(event => event.missingFields || []))],
    issues: [...new Set(selected.flatMap(event => event.issues || []))],
    entityFields,
    fieldCandidates,
    unresolvedConflicts: [...unresolvedConflicts],
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
    if (!event.recordType || event.recordType === '待定') issues.push(`事项 ${i + 1} 请选择工作记录类型`)
    const missingLabels = (event.formFields || [])
      .filter(field => field.required && field.missing)
      .map(field => field.label)
    if (missingLabels.length) {
      issues.push(`事项 ${i + 1} 还需补充：${[...new Set(missingLabels)].join('、')}`)
    }
    // 2026-11-03 冲突自动裁决：多候选值不再阻断，智能体直接采用其一，
    // 候选保留在字段上供用户事后点选更改，不再生成“请先选择”阻断项。
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
