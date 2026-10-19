/**
 * 创建日期：2026-07-18
 * 权限校验工具：角色与权限的判断
 */
import { useAuthStore } from '@/stores/auth'

/** 监理角色常量（与后端 UserRole.value 一致，S2 起为英文 key） */
export const ROLES = {
  CHIEF: 'chief_supervisor',
  DEPUTY: 'deputy_chief',
  PROFESSIONAL: 'special_supervisor',
  SUPERVISOR: 'inspector',
  DOCUMENTER: 'documenter',
  WITNESS: 'witness',
  SAFETY: 'safety_supervisor',
} as const

/** 英文 key → 中文展示名（S2：接口出参为 key，中文仅用于展示） */
export const ROLE_LABELS: Record<string, string> = {
  chief_supervisor: '总监理工程师',
  deputy_chief: '总监代表',
  special_supervisor: '专业监理工程师',
  inspector: '监理员',
  documenter: '资料员',
  witness: '见证员',
  safety_supervisor: '安全监理员',
}

/** 取中文展示名；未知 key 原样返回 */
export function roleLabel(key?: string | null): string {
  if (!key) return ''
  return ROLE_LABELS[key] ?? key
}

/** 角色下拉选项（登录页选择；value 为 key，label 为中文） */
export const ROLE_OPTIONS: { value: string; label: string; desc: string }[] = [
  { value: ROLES.CHIEF, label: '总监', desc: '全局管理：派单、文书签发、系统设置' },
  { value: ROLES.DEPUTY, label: '总代', desc: '协助总监：派单、文书审批' },
  { value: ROLES.PROFESSIONAL, label: '专监', desc: '专业领域：验收、旁站、见证、隐患识别' },
  { value: ROLES.SUPERVISOR, label: '监理员', desc: '基层执行：巡视记录、隐患识别' },
]

/** 是否拥有指定权限 */
export function hasPermission(perm: string): boolean {
  const auth = useAuthStore()
  return auth.permissions.includes(perm)
}

/** 是否为指定角色之一 */
export function hasRole(...roles: string[]): boolean {
  const auth = useAuthStore()
  return roles.includes(auth.user?.role ?? '')
}

/** 是否总监（含 admin） */
export function isChief(): boolean {
  return hasRole(ROLES.CHIEF) || isAdmin()
}

/** 是否总监/总代（含 admin） */
export function isChiefOrDeputy(): boolean {
  return hasRole(ROLES.CHIEF, ROLES.DEPUTY) || isAdmin()
}

/** 是否超管（Step 1：按公司级角色判断，取代 username === 'admin'） */
export function isAdmin(): boolean {
  const auth = useAuthStore()
  return auth.user?.org_role === 'super_admin'
}
