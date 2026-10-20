// Local interaction prototype. Observations and candidate plans are simulated, not OCR output.
export const FORM_TYPES = [
  '巡视记录', '危大工程巡视记录', '旁站记录', '验收记录', '见证记录',
  '材料台账', '监理通知单', '安全隐患整改通知单', '安全联系单', '监理日志', '监理月报', '会议纪要',
]
export const SCENARIOS = ['混合日期与缺失水印', '九张图片 / 九个日期', '同一历史日期 / 多项工作', '资料不足 / 用途不明', '纯文字 / 无图片']
export interface Evidence {
  id: string
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
  evidenceIds: string[]
  forms: string[]
  content: string
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
}

export function newEvent(id: string, evidenceIds: string[] = [], date = ''): WorkEvent {
  return { id, title: '现场事项（待核对）', location: '', date, dateConfirmed: false, evidenceIds, forms: [], content: '' }
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
    if (!/^photo-[1-9]$/.test(photo.id) || photo.image !== `/assets/work-review/${photo.id}.jpg` || !validDateOrEmpty(photo.observedDate) || typeof photo.note !== 'string') throw new Error('示例资料无效')
  }
  const assigned = [...plan.events.flatMap(event => event.evidenceIds), ...plan.unassignedIds]
  if (assigned.length !== ids.length || new Set(assigned).size !== ids.length || assigned.some(id => !ids.includes(id))) throw new Error('资料归属不完整或重复')
  for (const event of plan.events) {
    if (typeof event.id !== 'string' || typeof event.title !== 'string' || typeof event.location !== 'string' || typeof event.content !== 'string' || typeof event.dateConfirmed !== 'boolean') throw new Error('事项数据无效')
    if (!validDateOrEmpty(event.date) || (event.dateConfirmed && !event.date)) throw new Error('日期无效')
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
export function editEvent(plan: ReviewPlan, id: string, patch: Partial<Pick<WorkEvent, 'title' | 'location' | 'date' | 'dateConfirmed' | 'forms' | 'content'>>): ReviewPlan {
  if (!plan.events.some(event => event.id === id)) throw new Error('事项不存在')
  return revised(plan, { events: plan.events.map(event => event.id === id ? { ...event, ...patch } : event) })
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
  const dates = [...new Set(selected.map(event => event.date))]
  const merged: WorkEvent = {
    ...newEvent(newId, selected.flatMap(event => event.evidenceIds), dates.length === 1 ? dates[0] : ''),
    title: '合并事项（待核对）', forms: [...new Set(selected.flatMap(event => event.forms))],
    content: selected.map(event => event.content).filter(Boolean).join('\n'),
    location: [...new Set(selected.map(event => event.location).filter(Boolean))].join('；'),
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
    if (!event.dateConfirmed) issues.push(`事项 ${i + 1} 的业务日期待确认`)
    if (!event.forms.length) issues.push(`事项 ${i + 1} 的表式待确定`)
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
