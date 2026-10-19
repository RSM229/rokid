import axios from 'axios'
import type { AxiosInstance, AxiosRequestConfig } from 'axios'

type DataAxiosInstance = Omit<AxiosInstance, 'get' | 'post' | 'put' | 'delete'> & {
  get<T = any>(url: string, config?: AxiosRequestConfig): Promise<T>
  post<T = any>(url: string, data?: unknown, config?: AxiosRequestConfig): Promise<T>
  put<T = any>(url: string, data?: unknown, config?: AxiosRequestConfig): Promise<T>
  delete<T = any>(url: string, config?: AxiosRequestConfig): Promise<T>
}

// 响应拦截器会返回 response.data，这里同步修正实例类型，避免调用方误用 AxiosResponse。
const http = axios.create({
  baseURL: '/api',
  timeout: 30000,
}) as DataAxiosInstance

// ---- Step 3：权限版本联动（权限变更即时生效） ----
// 后端在每个响应上带 `X-Perm-Version`。若它和本地快照不一致，说明管理员改过
// 角色权限 —— 此时回调一次刷新（由 main.ts 注册为 auth store 的 fetchUserInfo），
// 用户无需重新登录，菜单与按钮就会跟上最新权限。
//
// 这里**不直接 import auth store**：store 反过来依赖本模块的 authApi，
// 直接引用会形成循环依赖。用回调注册保持依赖单向。
const PERM_VERSION_KEY = 'permVersion'
let permVersionHandler: (() => void | Promise<void>) | null = null
let permRefreshing = false

export function setPermVersionHandler(handler: () => void | Promise<void>) {
  permVersionHandler = handler
}

function syncPermVersion(headers: any) {
  const raw =
    (typeof headers?.get === 'function' ? headers.get('x-perm-version') : undefined) ??
    headers?.['x-perm-version']
  if (raw === undefined || raw === null || raw === '') return
  const version = Number(raw)
  if (!Number.isFinite(version) || version <= 0) return

  const stored = Number(localStorage.getItem(PERM_VERSION_KEY) || 0)
  if (stored === version) return
  localStorage.setItem(PERM_VERSION_KEY, String(version))
  // stored 为 0 表示本次会话第一次拿到版本号，此刻权限就是最新的，无需刷新
  if (!stored || !permVersionHandler || permRefreshing) return

  permRefreshing = true
  Promise.resolve(permVersionHandler())
    .catch(() => undefined)
    .finally(() => {
      permRefreshing = false
    })
}

http.interceptors.request.use((config) => {
  const token = localStorage.getItem('token')
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

/**
 * FastAPI 的 422 参数校验错误 ``detail`` 是数组（[{loc,msg,...}]），直接塞给
 * ElMessage 会渲染成 [object Object]。这里归一化成一句人话，全站页面都受益。
 */
function normalize422(error: any) {
  const detail = error?.response?.data?.detail
  if (!Array.isArray(detail)) return error
  const FIELD_ZH: Record<string, string> = {
    username: '用户名', password: '密码', real_name: '真实姓名', role: '岗位',
    org_role: '公司级角色', project_id: '所属项目', phone: '手机号',
    new_password: '新密码', current_password: '原密码',
  }
  const TYPE_ZH: Record<string, (ctx: any) => string> = {
    string_too_short: (c) => `至少 ${c?.min_length ?? '?'} 个字符`,
    string_too_long: (c) => `最多 ${c?.max_length ?? '?'} 个字符`,
    missing: () => '为必填项',
    greater_than_equal: (c) => `不能小于 ${c?.ge ?? '?'}`,
    less_than_equal: (c) => `不能大于 ${c?.le ?? '?'}`,
  }
  const first = detail[0] || {}
  const field = FIELD_ZH[String(first.loc?.[1] ?? first.loc?.[0] ?? '')] ?? String(first.loc?.[1] ?? '参数')
  const reason = TYPE_ZH[String(first.type)]?.(first.ctx) ?? String(first.msg ?? '格式不正确')
  error.response.data.detail = `${field}${reason}`
  return error
}

http.interceptors.response.use(
  (response) => {
    syncPermVersion(response.headers)
    return response.data
  },
  (error) => {
    if (error.response?.status === 422) {
      error = normalize422(error)
    }
    if (error.response?.status === 401) {
      const url = error.config?.url || ''
      if (!url.includes('/api/auth/login')) {
        localStorage.removeItem('token')
        localStorage.removeItem('userInfo')
        localStorage.removeItem('permissions')
        localStorage.removeItem(PERM_VERSION_KEY)
        window.location.href = '/login'
      }
    }
    return Promise.reject(error)
  }
)

function normalizeHazard(data: any) {
  const result = data?.result || data || {}
  const riskStatus = result.risk_status || ''
  const evidence = Array.isArray(result.evidence) ? result.evidence : []
  const suggestions = Array.isArray(result.recheck_suggestion)
    ? result.recheck_suggestion
    : []
  return {
    ...data,
    ...result,
    id: data?.hazard_id ?? result.id ?? null,
    has_hazard:
      result.has_hazard === true || riskStatus === '存在隐患',
    confidence: Number(result.confidence ?? 0),
    hazard_type: result.hazard_type || result.risk_category || '',
    description:
      result.description ||
      [result.top1_risk, evidence.length ? `证据：${evidence.join('；')}` : '']
        .filter(Boolean)
        .join('。'),
    suggestion: result.suggestion || suggestions.join('；'),
    image_path: result.image_path || data?.image_url || '',
  }
}

// 认证
export const authApi = {
  roles: () => http.get('/auth/roles'),
  validateInviteCode: (code: string) =>
    http.get(`/auth/invite-codes/validate?code=${encodeURIComponent(code)}`),
  // role 可省略：省略时后端按账号真实岗位自动判定
  login: (username: string, password: string, role?: string) =>
    http.post('/auth/login', role ? { username, password, role } : { username, password }),
  // 已登录用户用项目邀请码加入项目（后端 /auth/invite-codes/{code}/join）
  joinProjectByInvite: (code: string) =>
    http.post(`/auth/invite-codes/${encodeURIComponent(code)}/join`),
  // 项目负责人为本项目生成邀请码（权限 key = member_manage；码带 project_id + role）。
  // 注意：该接口挂在 auth router 下（/api/auth），不是 /api/project
  createProjectInviteCode: (projectId: number, data: {
    role: string
    level?: string
    max_uses?: number
    expires_in_days?: number | null
    code?: string | null
  }) => http.post(`/auth/projects/${projectId}/invite-codes`, data),
  me: () => http.get('/auth/me'),
  updateProfile: (data: any) => http.put('/auth/me/profile', data),
  changePassword: (data: { current_password: string; new_password: string }) =>
    http.put('/auth/me/password', data),
  users: () => http.get('/auth/users'),
  createUser: (data: any) => http.post('/auth/users', data),
  updateUser: (id: number, data: any) => http.put(`/auth/users/${id}`, data),
  // 管理员兜底解绑微信：账号被错误身份占用或用户换微信号时使用
  unbindWechat: (id: number) => http.delete(`/auth/users/${id}/wechat-binding`),
  // 邀请码管理（仅超管；后端权限 key = user_manage）
  inviteCodes: (includeInactive = false) =>
    http.get(`/auth/invite-codes?include_inactive=${includeInactive}`),
  // 全局「无项目」创建端点已移除：发码唯一入口 = 项目级 createProjectInviteCode
  // 项目负责人查看「自家项目」邀请码（权限 key = member_manage + 本项目成员）
  listProjectInviteCodes: (projectId: number, includeInactive = false) =>
    http.get(`/auth/projects/${projectId}/invite-codes?include_inactive=${includeInactive}`),
  revokeInviteCode: (code: string) =>
    http.post(`/auth/invite-codes/${encodeURIComponent(code)}/revoke`),
  // 微信绑定管理（仅超管；后端权限 key = user_manage）
  wechatBindings: (q?: string) =>
    http.get(`/auth/wechat-bindings${q ? `?q=${encodeURIComponent(q)}` : ''}`),
  unbindWechatBinding: (userId: number) =>
    http.delete(`/auth/users/${userId}/wechat-binding`),
  // 人员准入审批（Step 4；后端权限 key = user_approve，归公司级角色）
  registrations: (status = 'pending') =>
    http.get(`/auth/registrations?status=${encodeURIComponent(status)}`),
  approveRegistration: (
    userId: number,
    data: { project_id?: number | null; roles?: string[]; org_role?: string | null; remark?: string },
  ) => http.post(`/auth/registrations/${userId}/approve`, data),
  rejectRegistration: (userId: number, reason?: string) =>
    http.post(`/auth/registrations/${userId}/reject`, { reason: reason || null }),
  // ---- 「忘记密码」自助重置（公开接口，无需登录）----
  // ① 按用户名取回密保题目（不含答案）
  forgotQuestions: (username: string) =>
    http.post('/auth/forgot/questions', { username }),
  // ② 提交密保答案换取一次性重置凭证 reset_token
  forgotVerify: (username: string, answers: string[]) =>
    http.post('/auth/forgot/verify', { username, answers }),
  // ③ 凭重置凭证设置新密码
  forgotReset: (reset_token: string, new_password: string) =>
    http.post('/auth/forgot/reset', { reset_token, new_password }),
  // ---- 密保问题（登录态维护）----
  securityQuestions: () => http.get('/auth/me/security-questions'),
  setSecurityQuestions: (data: {
    current_password: string
    questions: { question: string; answer: string }[]
  }) => http.put('/auth/me/security-questions', data),
  // ---- 管理员兜底：重置为一次性临时口令（权限 user_manage）----
  adminResetPassword: (id: number) =>
    http.post(`/auth/users/${id}/reset-password`),
}

// 角色权限配置（Step 3；后端权限 key = role_grant，当前仅超管持有）
export const permissionApi = {
  matrix: () => http.get('/permissions/matrix'),
  updateRole: (roleKey: string, permissions: string[], remark?: string) =>
    http.put(`/permissions/matrix/${encodeURIComponent(roleKey)}`, { permissions, remark }),
  reset: () => http.post('/permissions/matrix/reset'),
}

// 隐患识别
export const hazardApi = {
  list: async (params?: any) => {
    const response: any = await http.get('/hazard/list', { params })
    return (Array.isArray(response) ? response : []).map(normalizeHazard)
  },
  detect: (file: File) => {
    const form = new FormData()
    form.append('image', file)
    return http.post('/hazard/detect', form)
  },
  detectAndSave: async (file: File, params?: any) => {
    const form = new FormData()
    form.append('image', file)
    if (params) {
      Object.entries(params).forEach(([k, v]) => {
        if (v !== undefined && v !== null) form.append(k, String(v))
      })
    }
    return normalizeHazard(await http.post('/hazard/detect-and-save', form))
  },
  detail: async (id: number) => normalizeHazard(await http.get(`/hazard/${id}`)),
  recheck: (id: number, passed: boolean, remark?: string) =>
    http.post(`/hazard/${id}/recheck`, { passed, remark }),
  uploadRectifyImage: (id: number, file: File) => {
    const form = new FormData()
    form.append('image', file)
    return http.post(`/hazard/${id}/rectify-image`, form)
  },
  createRectificationDoc: (id: number, deadline?: string) =>
    http.post(`/hazard/${id}/create-rectification-doc`, null, { params: { deadline } }),
}

// 监理文书
export const documentApi = {
  list: (params?: any) => http.get('/document/list', { params }),
  detail: (id: number) => http.get(`/document/${id}`),
  create: (data: any) => http.post('/document/create', data),
  update: (id: number, data: any) => http.put(`/document/${id}`, data),
  delete: (id: number) => http.delete(`/document/${id}`),
  submit: (id: number) => http.post(`/document/${id}/submit`),
  approve: (id: number) => http.post(`/document/${id}/approve`),
  reject: (id: number) => http.post(`/document/${id}/reject`),
  exportPdf: (id: number) => http.get(`/document/${id}/export-pdf`),
  downloadPdf: (id: number) =>
    http.get(`/document/${id}/download-pdf`, { responseType: 'blob' }),
  generateWithLlm: (data: any) => http.post('/document/generate-with-llm', data),
  generateDailyLog: (date: string) => http.post('/document/generate-daily-log', null, { params: { date } }),
}

// 知识库
export const knowledgeApi = {
  categories: () => http.get('/knowledge/regulations/categories'),
  // 获取规范分类列表（含数量统计），用于规范库维护
  listCategories: () => http.get('/knowledge/regulations/categories'),
  searchRegulations: (keyword: string, category?: string) =>
    http.get('/knowledge/regulations/search', { params: { keyword, category } }),
  searchRegulationsPost: (data: any) => http.post('/knowledge/regulations/search', data),
  listRegulations: (category?: string) =>
    http.get('/knowledge/regulations/list', { params: { category } }),
  downloadRegulation: (filename: string, category?: string) =>
    http.get(`/knowledge/regulations/download/${filename}`, {
      params: { category },
      responseType: 'blob',
    }),
  acceptanceGuide: (keyword: string) =>
    http.post('/knowledge/acceptance-guide', { keyword }),
  acceptanceGuideGet: (keyword: string) =>
    http.get('/knowledge/acceptance-guide', { params: { keyword } }),
  docTemplate: (docType: string) => http.get(`/knowledge/doc-template/${docType}`),
  maintain: (data: any) => http.post('/knowledge/regulations/maintain', data),
  // 规范库维护：删除/重命名/移动分类
  maintainRegulation: (data: {
    action: 'delete' | 'update' | 'move_category'
    filename: string
    new_filename?: string
    target_category?: string
  }) => http.post('/knowledge/regulations/maintain', data),
  llmQuery: (question: string, useLlm: boolean = true, knowledgeScope: string = 'all') =>
    http.post('/knowledge/llm-query', {
      question,
      use_llm: useLlm,
      knowledge_scope: knowledgeScope,
    }),
  importRegulation: (file: File, category: string, name?: string) => {
    const form = new FormData()
    form.append('file', file)
    form.append('category', category)
    if (name) form.append('name', name)
    return http.post('/knowledge/regulations/import', form)
  },
  importRegulationBatch: (files: File[], category: string) => {
    const form = new FormData()
    files.forEach(f => form.append('files', f))
    form.append('category', category)
    return http.post('/knowledge/regulations/import-batch', form)
  },
  // 规范版本管理
  listVersions: (params?: { filename?: string; category?: string; active_only?: boolean }) =>
    http.get('/knowledge/regulations/versions', { params }),
  createVersion: (data: any) => http.post('/knowledge/regulations/versions', data),
  versionHistory: (filename: string) =>
    http.get(`/knowledge/regulations/versions/${encodeURIComponent(filename)}`),
  rollbackVersion: (versionId: number) =>
    http.post('/knowledge/regulations/versions/rollback', { version_id: versionId }),
  syncVersions: () => http.post('/knowledge/regulations/versions/sync'),
}

// 台账
export const ledgerApi = {
  types: () => http.get('/ledger/types'),
  list: (params: any) => http.get('/ledger/list', { params }),
  export: (ledger_type: string, format: string = 'excel', params?: any) =>
    http.get('/ledger/export', { params: { ledger_type, format, ...params } }),
  download: (filename: string) =>
    http.get(`/ledger/download/${filename}`, { responseType: 'blob' }),
  // 批量导出多类台账（返回 zip 文件）
  batchExport: (data: {
    ledger_types: string[]
    date_from?: string
    date_to?: string
    format: 'excel' | 'pdf' | 'zip'
  }) => http.post('/ledger/batch-export', data, { responseType: 'blob' }),
  // 批量归档多类台账
  batchArchive: (data: {
    ledger_types: string[]
    date_from?: string
    date_to?: string
    archive_name: string
  }) => http.post('/ledger/batch-archive', data),
  // 归档管理
  listArchives: () => http.get('/ledger/archives'),
  downloadArchive: (id: number) =>
    http.get(`/ledger/archives/${id}/download`, { responseType: 'blob' }),
  deleteArchive: (id: number) => http.delete(`/ledger/archives/${id}`),
}

// 水印
export const watermarkApi = {
  apply: (file: File, params: any) => {
    const form = new FormData()
    form.append('image', file)
    Object.entries(params).forEach(([k, v]) => {
      if (v !== undefined && v !== null) form.append(k, String(v))
    })
    return http.post('/watermark/apply', form)
  },
}

// 工作记录
export const workRecordApi = {
  // 巡视
  createInspection: (data: any) => http.post('/work-record/inspection', data),
  uploadInspectionPhotos: (recordId: number, params: any, files: File[]) => {
    const form = new FormData()
    files.forEach(f => form.append('photos', f))
    Object.entries(params).forEach(([k, v]) => form.append(k, String(v)))
    return http.post(`/work-record/inspection/${recordId}/photos`, form)
  },
  submitInspection: (recordId: number) => http.post(`/work-record/inspection/${recordId}/submit`),
  // 验收
  createAcceptance: (data: any) => http.post('/work-record/acceptance', data),
  // 见证
  createWitness: (data: any) => http.post('/work-record/witness', data),
  uploadWitnessPhotos: (recordId: number, files: File[]) => {
    const form = new FormData()
    files.forEach(f => form.append('photos', f))
    return http.post(`/work-record/witness/${recordId}/photos`, form)
  },
  generateWitnessReport: (recordId: number) =>
    http.post(`/work-record/witness/${recordId}/report`),
  // 旁站
  createStation: (data: any) => http.post('/work-record/station', data),
  endStation: (recordId: number) => http.put(`/work-record/station/${recordId}`),
  // 通用
  list: (params?: any) => http.get('/work-record/list', { params }),
  detail: (id: number) => http.get(`/work-record/${id}`),
  update: (id: number, data: any) => http.put(`/work-record/${id}`, data),
  stats: () => http.get('/work-record/stats'),
  markViewed: (id: number) => http.put(`/work-record/${id}/viewed`),
  // 巡检指引（知识图谱）
  listGuides: (params?: any) => http.get('/work-record/guides', { params }),
}

// 知识图谱
export const kgApi = {
  listEntities: (params?: any) => http.get('/kg/entities', { params }),
  createEntity: (data: any) => http.post('/kg/entities', data),
  searchEntities: (data: any) => http.post('/kg/entities/search', data),
  deleteEntity: (id: number) => http.delete(`/kg/entities/${id}`),
  createRelation: (data: any) => http.post('/kg/relations', data),
  subgraph: (entityId: number, depth: number = 2) =>
    http.get('/kg/graph/subgraph', { params: { entity_id: entityId, depth } }),
  extract: (text: string, sourceDoc?: string) =>
    http.post('/kg/extract', { text, source_doc: sourceDoc }),
  stats: () => http.get('/kg/stats'),
  importFromKB: () => http.post('/kg/import-from-knowledge-base'),
}

// 派单管理
export const taskApi = {
  create: (data: any) => http.post('/task', data),
  assignableUsers: () => http.get('/task/assignable-users'),
  myTasks: (params?: any) => http.get('/task/my-tasks', { params }),
  assignedByMe: (params?: any) => http.get('/task/assigned-by-me', { params }),
  todoStats: () => http.get('/task/todo-stats'),
  detail: (id: number) => http.get(`/task/${id}`),
  respond: (id: number, action: string, rejectReason?: string) =>
    http.post(`/task/${id}/respond`, { action, reject_reason: rejectReason }),
  start: (id: number) => http.post(`/task/${id}/start`),
  complete: (id: number, data?: any) => http.post(`/task/${id}/complete`, data || {}),
  cancel: (id: number) => http.post(`/task/${id}/cancel`),
  reassign: (id: number, data: any) => http.post(`/task/${id}/reassign`, data),
  urge: (id: number) => http.post(`/task/${id}/urge`),
  notifications: (sinceTs?: number) =>
    http.get('/task/notifications', { params: sinceTs ? { since_ts: sinceTs } : {} }),
  clearNotifications: () => http.post('/task/notifications/clear'),
}

// 项目管理
export const projectApi = {
  // includeClosed=true 时连已结项（完工/归档）项目一并返回（Step 5）
  list: (includeClosed = false) =>
    http.get('/project/list', {
      params: includeClosed ? { include_closed: true } : {},
    }),
  create: (data: any) => http.post('/project/create', data),
  update: (projectId: number, data: any) => http.put(`/project/${projectId}`, data),
  switch: (projectId: number) => http.post(`/project/${projectId}/switch`),
  // Step 2：项目成员管理（权限 key = member_manage；
  // 公司级管理员可跨项目，总监限本项目）
  // Step 5：includeLeft=true 连已离场成员一并返回（带在场区间 joined_at ~ left_at）
  members: (projectId: number, includeLeft = false) =>
    http.get(`/project/${projectId}/members`, {
      params: includeLeft ? { include_left: true } : {},
    }),
  // role = 主岗（必填）；extra_roles = 附加岗（可叠加岗位，排他岗不可作附加）
  addMember: (projectId: number, data: { user_id: number; role?: string; extra_roles?: string[] }) =>
    http.post(`/project/${projectId}/members`, data),
  removeMember: (projectId: number, userId: number) =>
    http.delete(`/project/${projectId}/members/${userId}`),
  // Step 5：项目生命周期（权限 project_complete，默认仅超管）
  complete: (projectId: number) => http.post(`/project/${projectId}/complete`),
  reopen: (projectId: number) => http.post(`/project/${projectId}/reopen`),
}

// 电子签章
export const signatureApi = {
  list: () => http.get('/signatures/me'),
  upload: (data: FormData) => http.post('/signatures/upload', data),
  delete: (id: number, password: string) =>
    http.delete(`/signatures/${id}`, { data: { password } }),
  signoffs: (docId: number) => http.get(`/documents/${docId}/signoffs`),
  signDocument: (docId: number, signatureId: number, password: string, slot?: string) =>
    http.post(`/documents/${docId}/sign`, { signature_id: signatureId, password, slot }),
  downloadSignedPdf: (docId: number) =>
    http.get(`/documents/${docId}/signed-pdf`, { responseType: 'blob' }),
}

// 验收流程模板
export const acceptanceTemplateApi = {
  list: () => http.get('/acceptance-templates'),
  get: (id: number) => http.get(`/acceptance-templates/${id}`),
  create: (data: any) => http.post('/acceptance-templates', data),
  update: (id: number, data: any) => http.put(`/acceptance-templates/${id}`, data),
  delete: (id: number) => http.delete(`/acceptance-templates/${id}`),
}

// AI 巡检统计
export const inspectionApi = {
  stats: (params?: any) => http.get('/inspection/stats', { params }),
  trend: (days: number = 7) => http.get('/inspection/trend', { params: { days } }),
  categoryDistribution: () => http.get('/inspection/category-distribution'),
  recentRecords: (limit: number = 20) =>
    http.get('/inspection/recent', { params: { limit } }),
}

// 公司级看板（P4 多项目横向对比）
export const statsApi = {
  company: () => http.get('/stats/company'),
}

export type ModelType = 'llm' | 'tts' | 'stt' | 'vlm'

export interface ModelSetting {
  model_type: ModelType
  provider: string
  base_url: string
  model_id: string
  api_key_masked: string
  has_api_key: boolean
  enabled: boolean
  options: Record<string, unknown>
  updated_at?: string | null
}

export interface ModelSettingUpdate {
  provider: string
  base_url: string
  model_id: string
  api_key?: string
  clear_api_key?: boolean
  enabled: boolean
  options: Record<string, unknown>
}

// 模型调用设置。后端响应只包含掩码，绝不返回真实 API Key。
export const modelSettingsApi = {
  list: () => http.get<{ items: ModelSetting[] }>('/model-settings'),
  update: (modelType: ModelType, data: ModelSettingUpdate) =>
    http.put<ModelSetting>(`/model-settings/${modelType}`, data),
  reset: (modelType: ModelType) =>
    http.post<ModelSetting>(`/model-settings/${modelType}/reset`),
}

export default http
