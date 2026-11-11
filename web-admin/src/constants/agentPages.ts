/**
 * 小程序页面路径 → Web 路由映射。
 *
 * 后端 `navigate` 工具沿用小程序页面词典（agent_tools._PAGE_ALIASES），
 * 回执里的 `actions[].url` 是 `/pages/xxx/index` 形式；Web 端在这里落地。
 * 新增页面时两端同步补一行即可。
 */
export interface WebPageTarget {
  path: string
  title: string
}

const PAGE_TABLE: Record<string, WebPageTarget> = {
  '/pages/agent/index': { path: '/dashboard', title: '工作台' },
  '/pages/work/index': { path: '/work-record/inspection', title: '工作记录' },
  '/pages/hazards/index': { path: '/inspection-dashboard', title: '巡检统计' },
  '/pages/documents/index': { path: '/documents', title: '文书审批' },
  '/pages/todo/index': { path: '/task-manage', title: '派单管理' },
  '/pages/dashboard/index': { path: '/company-dashboard', title: '公司看板' },
  '/pages/kb/index': { path: '/knowledge-base', title: '信息库' },
  '/pages/knowledge/index': { path: '/knowledge-base', title: '规范查询' },
  '/pages/capture/index': { path: '/camera-simulator', title: '现场识别' },
  '/pages/join-project/index': { path: '/project-manage', title: '项目管理' },
  '/pages/me/index': { path: '/profile', title: '个人中心' },
}

/** 同一页面带 tab 参数的变体（如文书页的台账 tab） */
const TAB_TABLE: Record<string, WebPageTarget> = {
  '/pages/documents/index|ledger': { path: '/ledger', title: '台账管理' },
}

export function resolveAgentUrl(raw?: string | null): WebPageTarget | null {
  const input = String(raw || '').trim()
  if (!input) return null
  const [path, query = ''] = input.split('?')
  if (!path.startsWith('/')) return null
  const tab = new URLSearchParams(query).get('tab') || ''
  return TAB_TABLE[`${path}|${tab}`] || PAGE_TABLE[path] || null
}

/** 回执卡片类型 → Web 列表页（点卡片跳过去找） */
const CARD_TABLE: Record<string, WebPageTarget> = {
  document: { path: '/documents', title: '文书审批' },
  work_record: { path: '/work-record/inspection', title: '巡视记录' },
  parallel_inspection: { path: '/work-record/inspection', title: '巡视记录' },
  hazard: { path: '/inspection-dashboard', title: '巡检统计' },
}

export function resolveCardTarget(kind?: string | null): WebPageTarget | null {
  return CARD_TABLE[String(kind || '')] || null
}
