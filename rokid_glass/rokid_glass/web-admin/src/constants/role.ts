/**
 * 角色常量（S2：接口出参为英文 key，中文仅用于展示）
 *
 * 背景：后端 `app/models/user.py` 的 `UserRole` 值已 key 化为英文，
 * 接口返回的 `role` 也是英文 key。前端所有「展示」走本文件的 `ROLE_LABELS`，
 * 所有「比较 / 提交」用英文 key（`ROLE_KEYS` / `DEFAULT_ROLE`）。
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
  safety_supervisor: '安全监理员',
  // 公司级角色（Step 2）：仅 Web 管理后台分配/展示，小程序端不下发
  super_admin: '超级管理员',
  admin: '管理员'
}

/** 全部角色 key（顺序即下拉展示顺序） */
export const ROLE_KEYS = Object.keys(ROLE_LABELS)

/** 公司级角色 key（跨项目；不经 `project_members`） */
export const ORG_ROLE_KEYS = ['super_admin', 'admin']

/** 公司级角色 key → 中文展示名（从 ROLE_LABELS 派生，保持唯一真源） */
export const ORG_ROLE_LABELS: Record<string, string> = Object.fromEntries(
  ORG_ROLE_KEYS.map((key) => [key, ROLE_LABELS[key]])
)

/** 是否公司级角色 */
export function isOrgRole(key?: string | null): boolean {
  return !!key && ORG_ROLE_KEYS.includes(key)
}

/** 项目级角色 key（可被指派进项目的岗位） */
export const PROJECT_ROLE_KEYS = ROLE_KEYS.filter(
  (key) => !ORG_ROLE_KEYS.includes(key)
)

/** el-select 用的 { value: key, label: 中文 } 选项 */
export const ROLE_OPTIONS = ROLE_KEYS.map((key) => ({
  value: key,
  label: ROLE_LABELS[key]
}))

/** 仅项目级岗位的选项（成员指派 / 主岗用，不含公司级） */
export const PROJECT_ROLE_OPTIONS = PROJECT_ROLE_KEYS.map((key) => ({
  value: key,
  label: ROLE_LABELS[key]
}))

/** 仅公司级角色的选项（账号设置 org_role 用） */
export const ORG_ROLE_OPTIONS = ORG_ROLE_KEYS.map((key) => ({
  value: key,
  label: ROLE_LABELS[key]
}))

/** 默认角色（新建用户 / 登录页兜底） */
export const DEFAULT_ROLE = 'inspector'

/** 「总监 / 总代」这一档的 key 集合（台账等页面的判断用） */
export const CHIEF_ROLE_KEYS = ['chief_supervisor', 'deputy_chief']

/** 取中文展示名；未知 key 原样返回，避免界面空白 */
export function roleLabel(key?: string | null): string {
  if (!key) return ''
  return ROLE_LABELS[key] ?? key
}

/** 是否属于「总监 / 总代」档 */
export function isChiefRole(key?: string | null): boolean {
  return !!key && CHIEF_ROLE_KEYS.includes(key)
}
