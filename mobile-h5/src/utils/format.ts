/**
 * 创建日期：2026-07-18
 * 格式化工具：日期、文件大小、状态映射
 */
import dayjs from 'dayjs'
import relativeTime from 'dayjs/plugin/relativeTime'
import 'dayjs/locale/zh-cn'

dayjs.extend(relativeTime)
dayjs.locale('zh-cn')

/** 格式化日期：YYYY-MM-DD HH:mm */
export function formatDateTime(input?: string | number | Date | null): string {
  if (!input) return '-'
  return dayjs(input).format('YYYY-MM-DD HH:mm')
}

/** 格式化日期：YYYY-MM-DD */
export function formatDate(input?: string | number | Date | null): string {
  if (!input) return '-'
  return dayjs(input).format('YYYY-MM-DD')
}

/** 格式化时间：HH:mm */
export function formatTime(input?: string | number | Date | null): string {
  if (!input) return '-'
  return dayjs(input).format('HH:mm')
}

/** 相对时间（如「3 小时前」） */
export function formatFromNow(input?: string | number | Date | null): string {
  if (!input) return '-'
  return dayjs(input).fromNow()
}

/** 文件大小格式化 */
export function formatFileSize(bytes: number): string {
  if (!bytes || bytes <= 0) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB']
  const i = Math.floor(Math.log(bytes) / Math.log(1024))
  const size = bytes / Math.pow(1024, i)
  return `${size.toFixed(i === 0 ? 0 : 1)} ${units[i]}`
}

/** 文书状态映射 */
export const DOCUMENT_STATUS_MAP: Record<string, { label: string; type: string }> = {
  draft: { label: '草稿', type: 'default' },
  submitted: { label: '待审批', type: 'warning' },
  approved: { label: '已审批', type: 'success' },
  rejected: { label: '已驳回', type: 'danger' },
  archived: { label: '已归档', type: 'primary' },
}

/** 隐患状态映射 */
export const HAZARD_STATUS_MAP: Record<string, { label: string; type: string }> = {
  open: { label: '待整改', type: 'danger' },
  rectifying: { label: '整改中', type: 'warning' },
  rechecking: { label: '待复查', type: 'warning' },
  closed: { label: '已闭环', type: 'success' },
}

/** 任务状态映射 */
export const TASK_STATUS_MAP: Record<string, { label: string; type: string }> = {
  pending: { label: '待接单', type: 'warning' },
  doing: { label: '进行中', type: 'primary' },
  done: { label: '已完成', type: 'success' },
  cancelled: { label: '已取消', type: 'default' },
  accepted: { label: '已接单', type: 'primary' },
  rejected: { label: '已拒单', type: 'danger' },
}

/** 任务优先级映射 */
export const TASK_PRIORITY_MAP: Record<string, { label: string; type: string }> = {
  low: { label: '低', type: 'default' },
  medium: { label: '中', type: 'primary' },
  high: { label: '高', type: 'warning' },
  urgent: { label: '紧急', type: 'danger' },
}

/** 工作记录状态映射 */
export const WORK_RECORD_STATUS_MAP: Record<string, { label: string; type: string }> = {
  draft: { label: '草稿', type: 'default' },
  submitted: { label: '已提交', type: 'warning' },
  approved: { label: '已审批', type: 'success' },
  rejected: { label: '已驳回', type: 'danger' },
  archived: { label: '已归档', type: 'primary' },
}

/** 工作记录类型映射 */
export const WORK_RECORD_TYPE_MAP: Record<string, { label: string; type: string }> = {
  inspection: { label: '巡视', type: 'primary' },
  acceptance: { label: '验收', type: 'success' },
  station: { label: '旁站', type: 'warning' },
  witness: { label: '见证', type: 'danger' },
}

/** 巡检会话状态映射 */
export const INSPECTION_STATUS_MAP: Record<string, { label: string; type: string }> = {
  active: { label: '进行中', type: 'primary' },
  running: { label: '进行中', type: 'primary' },
  completed: { label: '已完成', type: 'success' },
  stopped: { label: '已停止', type: 'default' },
  failed: { label: '失败', type: 'danger' },
}

/** 台账类型映射（与后端 LEDGER_TYPES 键一致） */
export const LEDGER_TYPE_MAP: Record<string, { label: string; type: string }> = {
  image: { label: '影像台账', type: 'primary' },
  旁站记录: { label: '旁站台账', type: 'warning' },
  监理日志: { label: '监理日志台账', type: 'success' },
  安全隐患整改通知单: { label: '整改通知单台账', type: 'danger' },
  hazard: { label: 'AI安全巡检台账', type: 'primary' },
  监理周报: { label: '周报台账', type: 'warning' },
  监理月报: { label: '月报台账', type: 'success' },
  见证取样记录: { label: '见证取样台账', type: 'danger' },
}

/** 通用状态文本获取 */
export function getStatusLabel(map: Record<string, { label: string }>, status?: string): string {
  if (!status) return '-'
  return map[status]?.label ?? status
}
