/**
 * 角色常量（S2：接口出参为英文 key，中文仅用于展示）
 *
 * 背景：后端 `app/models/user.py` 的 `UserRole` 值已 key 化为英文，
 * 接口返回的 `role` / `org_role` 均为英文 key。
 *   - 展示：统一走 `ROLE_LABELS`
 *   - 比较 / 提交：必须用英文 key（用中文比较会**静默永假**）
 *
 * 唯一真源在后端；改角色一律先改后端再同步这里。
 */

/** 英文 key → 中文展示名 */
export const ROLE_LABELS: Record<string, string> = {
  chief_supervisor: '总监理工程师',
  deputy_chief: '总监代表',
  special_supervisor: '专业监理工程师',
  inspector: '监理员',
  documenter: '资料员',
  witness: '见证员',
  safety_supervisor: '安全监理员'
}

/** 全部角色 key（顺序即下拉展示顺序） */
export const ROLE_KEYS = Object.keys(ROLE_LABELS)

/** 默认角色（注册页兜底） */
export const DEFAULT_ROLE = 'inspector'

/** 公司级角色 key */
export const ORG_ROLE_SUPER_ADMIN = 'super_admin'
export const ORG_ROLE_ADMIN = 'admin'

/** 「总监 / 总代」这一档的 key 集合 */
export const CHIEF_ROLE_KEYS = ['chief_supervisor', 'deputy_chief']

/** 取中文展示名；未知 key 原样返回，避免界面空白 */
export function roleLabel(key?: string | null): string {
  if (!key) return ''
  return ROLE_LABELS[key] || key
}

/** 是否属于「总监 / 总代」档 */
export function isChiefRole(key?: string | null): boolean {
  return !!key && CHIEF_ROLE_KEYS.indexOf(key) >= 0
}

/** 是否超管（按公司级角色判断，取代 username === 'admin'） */
export function isSuperAdmin(user?: { org_role?: string | null } | null): boolean {
  return !!user && user.org_role === ORG_ROLE_SUPER_ADMIN
}
