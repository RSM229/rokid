import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { authApi, projectApi } from '@/api'

export const useAuthStore = defineStore('auth', () => {
  const token = ref(localStorage.getItem('token') || '')
  const userInfo = ref(JSON.parse(localStorage.getItem('userInfo') || 'null'))
  const permissions = ref<string[]>(JSON.parse(localStorage.getItem('permissions') || '[]'))

  const isLoggedIn = computed(() => !!token.value)
  const role = computed(() => userInfo.value?.role || '')
  /** 当前所在项目（多项目负责人切换后即变；页面据此重建并重拉数据） */
  const currentProjectId = computed<number | null>(() => userInfo.value?.project_id ?? null)
  const currentProjectName = computed<string>(() => userInfo.value?.project_name || '')

  /**
   * 「全部项目」视角（仅公司级账号）。
   *
   * 为什么不复用 `users.project_id`：那是**写入**的归属，也是权限合成的输入；
   * 置空会让所有写接口 400。这里只改**读取**范围 —— 前端把 `all_projects=1`
   * 带给列表接口，后端 `project_scope_id()` 认这个参数时取消项目过滤。
   */
  const ALL_PROJECTS_KEY = 'allProjects'
  const allProjects = ref(localStorage.getItem(ALL_PROJECTS_KEY) === '1')
  /** 公司级账号（超管 / 管理员）——与后端 `_can_manage_all_projects` 同口径 */
  const isCompanyLevel = computed(() =>
    ['super_admin', 'admin'].includes(String(userInfo.value?.org_role || '')),
  )

  /**
   * 「全部项目」视角下**本次写入的目标项目**。
   *
   * 全部项目视角只看不改归属不清楚，写进哪个项目必须由用户明确选择：
   * 页面在发起新建前调用 `ensureWritableProject()` 弹窗选项目，选中的 id
   * 存在这里，请求拦截器把它作为 `X-Project-Id` 带给后端，
   * `require_project_id()` 优先采用 —— 视角不变、数据落到用户选的项目。
   */
  const WRITE_PROJECT_KEY = 'writeProjectId'
  const writeProjectId = ref<number | null>(
    Number(localStorage.getItem(WRITE_PROJECT_KEY)) || null,
  )

  function setAllProjects(value: boolean) {
    const next = value && isCompanyLevel.value
    allProjects.value = next
    localStorage.setItem(ALL_PROJECTS_KEY, next ? '1' : '0')
  }

  function setWriteProjectId(id: number | null) {
    writeProjectId.value = id && id > 0 ? id : null
    if (writeProjectId.value) {
      localStorage.setItem(WRITE_PROJECT_KEY, String(writeProjectId.value))
    } else {
      localStorage.removeItem(WRITE_PROJECT_KEY)
    }
  }

  /** 写入目标项目的名称（用于页面提示「将写入 XX 项目」）。 */
  function writeProjectName(projects: { id: number; name: string }[]): string {
    const hit = projects.find((p) => p.id === writeProjectId.value)
    return hit?.name || ''
  }

  function hasPermission(perm: string): boolean {
    return permissions.value.includes(perm)
  }

  /**
   * 按后端 TokenResponse 落地登录态（token / 用户信息 / 权限 一并落盘）。
   *
   * 抽出来的原因：注册改造后 ``/auth/register`` **注册即激活并直接返回 token**，
   * 与登录返回的结构完全同构。抽成一处可避免「登录」与「注册即登录」两份
   * 落地逻辑走偏（比如漏写 ``org_role`` 导致超管入口不显示）。
   */
  function setSession(res: any) {
    token.value = res.access_token
    userInfo.value = {
      user_id: res.user_id,
      username: res.username,
      real_name: res.real_name,
      role: res.role,
      org_role: res.org_role ?? null,
      avatar_url: res.avatar_url,
      // 当前项目（多项目负责人可切换；登录/切换后都以此为准）
      project_id: res.project_id ?? null,
      project_name: res.project_name ?? null,
      // 管理员重置为一次性口令后为 true —— 各端据此强制先改密
      must_change_password: Boolean(res.must_change_password),
    }
    permissions.value = res.permissions || []
    localStorage.setItem('token', token.value)
    localStorage.setItem('userInfo', JSON.stringify(userInfo.value))
    localStorage.setItem('permissions', JSON.stringify(permissions.value))
    // 换号登录时不能沿用上一个账号的「全部项目」视角（非公司级账号后端也不认）
    if (!['super_admin', 'admin'].includes(String(userInfo.value.org_role || ''))) {
      allProjects.value = false
      localStorage.setItem(ALL_PROJECTS_KEY, '0')
      writeProjectId.value = null
      localStorage.removeItem(WRITE_PROJECT_KEY)
    }
  }

  async function login(username: string, password: string, role?: string) {
    const res = await authApi.login(username, password, role)
    setSession(res)
  }

  async function fetchUserInfo() {
    try {
      const res = await authApi.me()
      userInfo.value = res
      if (res.permissions) {
        permissions.value = res.permissions
        localStorage.setItem('permissions', JSON.stringify(res.permissions))
      }
      localStorage.setItem('userInfo', JSON.stringify(res))
    } catch (e) {
      console.error('获取用户信息失败', e)
    }
  }

  function logout() {
    token.value = ''
    userInfo.value = null
    permissions.value = []
    allProjects.value = false
    writeProjectId.value = null
    localStorage.removeItem('token')
    localStorage.removeItem('userInfo')
    localStorage.removeItem('permissions')
    localStorage.removeItem(ALL_PROJECTS_KEY)
    localStorage.removeItem(WRITE_PROJECT_KEY)
    // Step 3：权限版本快照一并清掉，避免下一个账号沿用上一个账号的版本
    localStorage.removeItem('permVersion')
  }

  /** 改密成功后解除「强制改密」锁定（并把状态落盘，避免刷新又跳回改密页）。 */
  function clearMustChangePassword() {
    if (userInfo.value) {
      userInfo.value = { ...userInfo.value, must_change_password: false }
      localStorage.setItem('userInfo', JSON.stringify(userInfo.value))
    }
  }

  /**
   * 切换当前项目（多项目负责人场景）。
   *
   * 后端把「当前项目」落在 ``users.project_id`` 上，并且**每个请求都按
   * 「公司级 ∪ 当前项目岗位」实时合成权限**，所以这里切完只需重新拉一次
   * ``/auth/me``，项目名与权限就都跟上了 —— 不需要重新登录、也不需要换 token。
   */
  async function switchProject(projectId: number) {
    const res = await projectApi.switch(projectId)
    await fetchUserInfo()
    return res
  }

  return {
    token, userInfo, permissions, isLoggedIn, role, hasPermission,
    currentProjectId, currentProjectName,
    allProjects, isCompanyLevel, setAllProjects,
    writeProjectId, setWriteProjectId, writeProjectName,
    login, setSession, fetchUserInfo, logout, clearMustChangePassword, switchProject,
  }
})
