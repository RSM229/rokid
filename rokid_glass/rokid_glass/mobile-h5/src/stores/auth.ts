/**
 * 创建日期：2026-07-18
 * 认证状态：token（localStorage 持久化）、user、permissions
 * 与 web-admin 同构，user 形态收敛为移动端需要的字段
 */
import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { authApi } from '@/api'

export interface MobileUser {
  id: number
  username: string
  name: string
  role: string
  avatar_url?: string
  project_id?: number
  project_name?: string
  /** 公司级角色（Step 1 新增）：'super_admin' / 'admin' / undefined */
  org_role?: string | null
  /** 管理员重置为一次性口令后为 true —— 需强制先改密 */
  must_change_password?: boolean
}

const TOKEN_KEY = 'token'
const USER_KEY = 'mobileUser'
const PERMS_KEY = 'mobilePermissions'

function readUser(): MobileUser | null {
  try {
    return JSON.parse(localStorage.getItem(USER_KEY) || 'null')
  } catch {
    return null
  }
}

function readPerms(): string[] {
  try {
    return JSON.parse(localStorage.getItem(PERMS_KEY) || '[]')
  } catch {
    return []
  }
}

export const useAuthStore = defineStore('auth', () => {
  const token = ref<string>(localStorage.getItem(TOKEN_KEY) || '')
  const user = ref<MobileUser | null>(readUser())
  const permissions = ref<string[]>(readPerms())

  const isLoggedIn = computed(() => !!token.value)
  const role = computed(() => user.value?.role || '')

  /** 权限判断 */
  function hasPermission(perm: string): boolean {
    return permissions.value.includes(perm)
  }

  /** 登录 */
  async function login(username: string, password: string, role: string) {
    const res: any = await authApi.login(username, password, role)
    token.value = res.access_token
    user.value = {
      id: res.user_id,
      username: res.username,
      name: res.real_name || res.username,
      role: res.role || role,
      avatar_url: res.avatar_url,
      project_id: res.project_id,
      project_name: res.project_name,
      org_role: res.org_role ?? null,
      must_change_password: Boolean(res.must_change_password),
    }
    permissions.value = res.permissions || []
    localStorage.setItem(TOKEN_KEY, token.value)
    localStorage.setItem(USER_KEY, JSON.stringify(user.value))
    localStorage.setItem(PERMS_KEY, JSON.stringify(permissions.value))
  }

  /** 拉取最新用户信息（含项目） */
  async function fetchUserInfo() {
    try {
      const res: any = await authApi.me()
      user.value = {
        id: res.user_id ?? user.value?.id,
        username: res.username ?? user.value?.username,
        name: res.real_name ?? res.username ?? user.value?.name,
        role: res.role ?? user.value?.role,
        avatar_url: res.avatar_url ?? user.value?.avatar_url,
        project_id: res.project_id ?? user.value?.project_id,
        project_name: res.project_name ?? user.value?.project_name,
        org_role: res.org_role ?? user.value?.org_role ?? null,
        must_change_password:
          res.must_change_password ?? user.value?.must_change_password ?? false,
      }
      if (res.permissions) {
        permissions.value = res.permissions
        localStorage.setItem(PERMS_KEY, JSON.stringify(res.permissions))
      }
      localStorage.setItem(USER_KEY, JSON.stringify(user.value))
    } catch (e) {
      console.error('获取用户信息失败', e)
    }
  }

  /** 更新当前项目信息（项目切换后调用） */
  function setProject(projectId: number, projectName: string) {
    if (user.value) {
      user.value = { ...user.value, project_id: projectId, project_name: projectName }
      localStorage.setItem(USER_KEY, JSON.stringify(user.value))
    }
  }

  /** 退出登录 */
  function logout() {
    token.value = ''
    user.value = null
    permissions.value = []
    localStorage.removeItem(TOKEN_KEY)
    localStorage.removeItem(USER_KEY)
    localStorage.removeItem(PERMS_KEY)
  }

  /** 改密成功后解除「强制改密」锁定 */
  function clearMustChangePassword() {
    if (user.value) {
      user.value = { ...user.value, must_change_password: false }
      localStorage.setItem(USER_KEY, JSON.stringify(user.value))
    }
  }

  return {
    token,
    user,
    permissions,
    isLoggedIn,
    role,
    hasPermission,
    login,
    fetchUserInfo,
    setProject,
    logout,
    clearMustChangePassword,
  }
})
