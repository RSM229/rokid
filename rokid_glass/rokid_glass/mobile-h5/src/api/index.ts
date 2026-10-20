/**
 * 创建日期：2026-07-18
 * 集中 API 模块：与 web-admin 同构，仅暴露移动端所需接口
 * 后端：cloud-backend（端口 8000），所有请求走 /api 前缀
 */
import http from './client'

const WORK_TYPE_TO_BACKEND: Record<string, string> = {
  inspection: '巡视',
  acceptance: '验收',
  station: '旁站',
  witness: '见证',
}
const WORK_TYPE_FROM_BACKEND: Record<string, string> = Object.fromEntries(
  Object.entries(WORK_TYPE_TO_BACKEND).map(([key, value]) => [value, key]),
)
const WORK_STATUS_FROM_BACKEND: Record<string, string> = {
  草稿: 'draft',
  进行中: 'in_progress',
  已提交: 'submitted',
  已审批: 'approved',
  已驳回: 'rejected',
  已归档: 'archived',
}
const DOCUMENT_STATUS_TO_BACKEND: Record<string, string> = {
  draft: '草稿',
  submitted: '待审批',
  pending: '待审批',
  approved: '已审批',
  rejected: '已驳回',
  archived: '已归档',
}
const DOCUMENT_STATUS_FROM_BACKEND: Record<string, string> = {
  草稿: 'draft',
  待审批: 'submitted',
  已审批: 'approved',
  已驳回: 'rejected',
  已归档: 'archived',
}
const DOCUMENT_TYPE_TO_BACKEND: Record<string, string> = {
  inspection: '巡视记录',
  daily_log: '监理日志',
  station: '旁站记录',
  witness: '见证取样记录',
  notice: '监理工程师通知单（质量类）',
  quality_notice: '监理工程师通知单（质量类）',
  rectification: '安全隐患整改通知单',
  weekly_report: '监理周报',
  monthly_report: '监理月报',
}
const HAZARD_STATUS_FROM_BACKEND: Record<string, string> = {
  已提报: 'open',
  已通知整改: 'open',
  整改中: 'rectifying',
  待复查: 'rechecking',
  已闭环: 'closed',
  驳回整改: 'rejected',
}
const TASK_STATUS_TO_BACKEND: Record<string, string> = {
  pending: '待接单',
  accepted: '已接单',
  doing: '进行中',
  done: '已完成',
  rejected: '已拒单',
  cancelled: '已取消',
}
const TASK_STATUS_FROM_BACKEND: Record<string, string> = Object.fromEntries(
  Object.entries(TASK_STATUS_TO_BACKEND).map(([key, value]) => [value, key]),
)
const TASK_PRIORITY_FROM_BACKEND: Record<string, string> = {
  低: 'low',
  中: 'medium',
  高: 'high',
  紧急: 'urgent',
}

function normalizeWorkRecord(record: any) {
  const type = WORK_TYPE_FROM_BACKEND[record.record_type] || record.type || record.record_type
  const content = record.content_json || {}
  const acceptanceResult = record.acceptance_result
  return {
    ...record,
    type,
    status: WORK_STATUS_FROM_BACKEND[record.status] || record.status,
    construction_area: record.section,
    construction_content: record.work_content,
    remark: record.inspection_remark || record.remark,
    photos: record.inspection_photos || record.witness_photos || [],
    acceptance_type: content.acceptance_type,
    acceptance_content: content.acceptance_content,
    result:
      acceptanceResult === '合格'
        ? 'pass'
        : acceptanceResult === '不合格'
          ? 'fail'
          : acceptanceResult
            ? 'pending'
            : undefined,
    project_name: record.station_project_name,
    position: record.station_location,
    start_time: record.station_start_time,
    end_time: record.station_end_time,
    description: record.station_content,
    conclusion: record.station_conclusion,
    material_name: record.witness_material_name,
    specification: record.witness_material_spec,
  }
}

function normalizeHazard(hazard: any) {
  return {
    ...hazard,
    status: HAZARD_STATUS_FROM_BACKEND[hazard.status] || hazard.status,
    title: hazard.top1_risk || hazard.risk_category,
    description: hazard.top1_risk || hazard.remark,
    image_url: hazard.image_path,
    rectify_images: hazard.rectification_image_path
      ? [hazard.rectification_image_path]
      : [],
  }
}

function normalizeDocument(document: any) {
  return {
    ...document,
    status: DOCUMENT_STATUS_FROM_BACKEND[document.status] || document.status,
  }
}

function normalizeLedgerRecord(record: any) {
  const photos = Array.isArray(record.photos)
    ? record.photos
    : [
        record.watermarked_path,
        record.original_path,
        record.image_path,
        record.rectification_image_path,
      ].filter(Boolean)
  return {
    ...record,
    title: record.title || record.top1_risk || record.remark || `台账记录 #${record.id}`,
    doc_no: record.doc_number || record.doc_no || '',
    area: record.section || record.construction_area || record.area || '',
    created_at: record.created_at || record.taken_at || record.date,
    description: record.description || record.remark || record.top1_risk || record.content || '',
    photos,
  }
}

function normalizeTask(task: any) {
  let attachments = task.attachments
  if (typeof attachments === 'string') {
    try {
      attachments = JSON.parse(attachments)
    } catch {
      attachments = []
    }
  }
  return {
    ...task,
    status: TASK_STATUS_FROM_BACKEND[task.status] || task.status,
    priority: TASK_PRIORITY_FROM_BACKEND[task.priority] || task.priority,
    construction_area: task.section,
    creator_name: task.assigner_name,
    attachments: Array.isArray(attachments) ? attachments : [],
  }
}

// 认证
export const authApi = {
  roles: () => http.get('/auth/roles'),
  login: (username: string, password: string, role: string) =>
    http.post('/auth/login', { username, password, role }),
  me: () => http.get('/auth/me'),
  updateProfile: (data: { real_name?: string; phone?: string; email?: string }) =>
    http.put('/auth/me/profile', data),
  changePassword: (data: { current_password: string; new_password: string }) =>
    http.put('/auth/me/password', data),
  // ---- 「忘记密码」自助重置（公开接口）----
  forgotQuestions: (username: string) => http.post('/auth/forgot/questions', { username }),
  forgotVerify: (username: string, answers: string[]) =>
    http.post('/auth/forgot/verify', { username, answers }),
  forgotReset: (reset_token: string, new_password: string) =>
    http.post('/auth/forgot/reset', { reset_token, new_password }),
  // ---- 密保问题（登录态维护）----
  securityQuestions: () => http.get('/auth/me/security-questions'),
  setSecurityQuestions: (data: {
    current_password: string
    questions: { question: string; answer: string }[]
  }) => http.put('/auth/me/security-questions', data),
}

// 项目管理（用于个人中心项目切换）
export const projectApi = {
  list: () => http.get('/project/list'),
  switch: (projectId: number) =>
    http.post(`/project/${projectId}/switch`),
}

// 工作记录（巡视/验收/见证/旁站）
export const workRecordApi = {
  list: async (params?: any) => {
    // 后端期望 record_type 而非 type
    const { type, ...rest } = params || {}
    delete rest.page
    delete rest.page_size
    const response: any = await http.get('/work-record/list', {
      params: type
        ? { record_type: WORK_TYPE_TO_BACKEND[type] || type, ...rest }
        : rest,
    })
    const records = (response?.records || []).map(normalizeWorkRecord)
    return { ...response, records, items: records }
  },
  detail: async (id: number) => normalizeWorkRecord(await http.get(`/work-record/${id}`)),
  stats: () => http.get('/work-record/stats'),
  /** 通用创建：按 type 分发到对应子接口 */
  create: (data: any) => {
    const t = data.type
    if (t === 'acceptance') {
      return http.post('/work-record/acceptance', {
        section: data.construction_area,
        location: data.location,
        acceptance_type: data.acceptance_type,
        acceptance_content: data.acceptance_content,
        acceptance_result: {
          pass: '合格',
          fail: '不合格',
          pending: '待整改',
        }[data.result as string] || data.result,
      })
    }
    if (t === 'witness') {
      return http.post('/work-record/witness', {
        section: data.construction_area,
        location: data.location,
        witness_material_name: data.material_name,
        witness_material_spec: data.specification,
        remark: data.remark,
      })
    }
    if (t === 'station') {
      return http.post('/work-record/station', {
        section: data.position,
        location: data.position,
        station_project_name: data.project_name,
        station_location: data.position,
        station_content: data.description,
        station_conclusion: data.conclusion,
        start_time: data.start_time || undefined,
        end_time: data.end_time || undefined,
      })
    }
    return http.post('/work-record/inspection', {
      section: data.construction_area,
      location: data.location,
      work_content: data.construction_content,
      weather: data.weather,
      inspection_remark: data.remark,
    })
  },
  createWithPhotos: (data: any, files: File[]) => {
    const form = new FormData()
    form.append('record_type', data.type)
    form.append('payload_json', JSON.stringify(data))
    files.forEach((file) => form.append('photos', file))
    return http.post('/work-record/create-with-photos', form)
  },
  createInspection: (data: any) => http.post('/work-record/inspection', data),
  createInspectionWithPhoto: (file: File, data: Record<string, unknown>) => {
    const form = new FormData()
    form.append('photo', file)
    Object.entries(data).forEach(([key, value]) => {
      if (value !== undefined && value !== null) form.append(key, String(value))
    })
    return http.post('/work-record/inspection-with-photo', form)
  },
  uploadInspectionPhotos: (recordId: number, params: any, files: File[]) => {
    const form = new FormData()
    files.forEach((f) => form.append('photos', f))
    Object.entries(params).forEach(([k, v]) => form.append(k, String(v)))
    return http.post(`/work-record/inspection/${recordId}/photos`, form)
  },
  uploadRecordPhotos: async (recordId: number, files: File[]) => {
    const mediaIds: number[] = []
    for (const file of files) {
      const form = new FormData()
      form.append('file', file)
      form.append('source', 'mobile_h5')
      const result: any = await http.post('/media/images', form)
      mediaIds.push(Number(result.id))
    }
    if (mediaIds.length) {
      await http.post('/media/link', {
        media_ids: mediaIds,
        business_type: 'work_record',
        business_id: recordId,
      })
    }
    return { media_ids: mediaIds }
  },
  submitInspection: (recordId: number) => http.post(`/work-record/inspection/${recordId}/submit`),
  createAcceptance: (data: any) => http.post('/work-record/acceptance', data),
  createWitness: (data: any) => http.post('/work-record/witness', data),
  createStation: (data: any) => http.post('/work-record/station', data),
  endStation: (recordId: number) => http.put(`/work-record/station/${recordId}`),
  listGuides: (params?: any) => http.get('/work-record/guides', { params }),
  /** 通用状态流转 */
  submit: (id: number) => http.post(`/work-record/${id}/submit`),
  approve: (id: number) => http.post(`/work-record/${id}/approve`),
  reject: (id: number, reason: string) =>
    http.post(`/work-record/${id}/reject`, { reason }),
  archive: (id: number) => http.post(`/work-record/${id}/archive`),
}

// 隐患识别与整改
export const hazardApi = {
  list: async (params?: any) => {
    // 后端期望 status_filter 而非 status
    const { status, ...rest } = params || {}
    delete rest.page
    delete rest.page_size
    const response: any = await http.get('/hazard/list', {
      params: status ? { status_filter: status, ...rest } : rest,
    })
    return (Array.isArray(response) ? response : []).map(normalizeHazard)
  },
  detect: (file: File) => {
    const form = new FormData()
    form.append('image', file)
    return http.post('/hazard/detect', form)
  },
  detail: async (id: number) => normalizeHazard(await http.get(`/hazard/${id}`)),
  recheck: (id: number, passed: boolean, remark?: string) =>
    http.post(`/hazard/${id}/recheck`, { passed, remark }),
  uploadRectifyImage: (id: number, file: File) => {
    const form = new FormData()
    form.append('image', file)
    return http.post(`/hazard/${id}/rectify-image`, form)
  },
}

// 监理文书
export const documentApi = {
  list: async (params?: any) => {
    const normalized = { ...(params || {}) }
    delete normalized.page
    delete normalized.page_size
    if (normalized.status) {
      normalized.status = DOCUMENT_STATUS_TO_BACKEND[normalized.status] || normalized.status
    }
    const response: any = await http.get('/document/list', { params: normalized })
    return (Array.isArray(response) ? response : []).map(normalizeDocument)
  },
  detail: async (id: number) => normalizeDocument(await http.get(`/document/${id}`)),
  create: async (data: any) => {
    const response: any = await http.post('/document/create', {
      ...data,
      doc_type: DOCUMENT_TYPE_TO_BACKEND[data.doc_type] || data.doc_type,
      content:
        typeof data.content === 'string'
          ? { summary: data.content }
          : (data.content || {}),
    })
    return { ...response, id: response.doc_id }
  },
  update: (id: number, data: any) =>
    http.put(`/document/${id}`, {
      ...data,
      content:
        typeof data.content === 'string'
          ? { summary: data.content }
          : data.content,
    }),
  submit: (id: number) => http.post(`/document/${id}/submit`),
  approve: (id: number) => http.post(`/document/${id}/approve`),
  reject: (id: number, reason?: string) =>
    http.post(`/document/${id}/reject`, { reason: reason || '' }),
  generateWithLlm: async (data: any) => {
    const response: any = await http.post('/document/generate-with-llm', {
      doc_type: DOCUMENT_TYPE_TO_BACKEND[data.doc_type] || data.doc_type,
      title: data.title || DOCUMENT_TYPE_TO_BACKEND[data.doc_type] || data.doc_type,
      section: data.section,
      location: data.location,
      hazard_id: data.hazard_id,
      extra_context: data.extra_context || data.points || '',
    })
    return {
      ...response,
      id: response.doc_id,
      content: response.llm_content,
    }
  },
  generateDailyLog: (date: string) =>
    http.post('/document/generate-daily-log', null, { params: { date } }),
}

// 知识库
export const knowledgeApi = {
  categories: () => http.get('/knowledge/regulations/categories'),
  searchRegulations: (keyword: string, category?: string) =>
    http.get('/knowledge/regulations/search', { params: { keyword, category } }),
  listRegulations: (category?: string) =>
    http.get('/knowledge/regulations/list', { params: { category } }),
  acceptanceGuide: (keyword: string) => http.post('/knowledge/acceptance-guide', { keyword }),
  acceptanceGuideGet: (keyword: string) =>
    http.get('/knowledge/acceptance-guide', { params: { keyword } }),
  docTemplate: (docType: string) => http.get(`/knowledge/doc-template/${docType}`),
  llmQuery: (question: string, useLlm = true) =>
    http.post('/knowledge/llm-query', { question, use_llm: useLlm }),
}

// 台账
export const ledgerApi = {
  types: () => http.get('/ledger/types'),
  list: async (params: any) => {
    const normalized = { ...(params || {}) }
    if (normalized.type && !normalized.ledger_type) {
      normalized.ledger_type = normalized.type
    }
    delete normalized.type
    const response: any = await http.get('/ledger/list', {
      params: normalized,
    })
    const records = (response?.records || []).map(normalizeLedgerRecord)
    return { ...response, records, items: records, list: records }
  },
  detail: async (type: string, id: number) =>
    normalizeLedgerRecord(await http.get(`/ledger/${encodeURIComponent(type)}/${id}`)),
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

// 统一媒体
export const mediaApi = {
  list: (params?: {
    media_type?: 'image' | 'video' | 'audio'
    business_type?: string
    business_id?: number
    limit?: number
  }) => http.get('/media', { params }),
  detail: (id: number) => http.get(`/media/${id}`),
}

// 派单/任务
export const taskApi = {
  myTasks: async (params?: any) => {
    const mine = params?.mine === true
    const status = params?.status
      ? TASK_STATUS_TO_BACKEND[params.status] || params.status
      : undefined
    const response: any = await http.get(
      mine ? '/task/assigned-by-me' : '/task/my-tasks',
      { params: status ? { status } : {} },
    )
    const tasks = (response?.tasks || []).map(normalizeTask)
    return { ...response, tasks, items: tasks }
  },
  todoStats: () => http.get('/task/todo-stats'),
  detail: async (id: number) => normalizeTask(await http.get(`/task/${id}`)),
  respond: (id: number, action: 'accept' | 'reject', reason?: string) =>
    http.post(`/task/${id}/respond`, { action, reject_reason: reason }),
  start: (id: number) => http.post(`/task/${id}/start`),
  complete: (id: number, data: { receipt: string; result_files?: File[] }) => {
    if (data.result_files && data.result_files.length) {
      const form = new FormData()
      form.append('completion_remark', data.receipt)
      data.result_files.forEach((f) => form.append('result_files', f))
      return http.post(`/task/${id}/complete-with-files`, form, {
        headers: { 'Content-Type': 'multipart/form-data' },
      })
    }
    return http.post(`/task/${id}/complete`, { completion_remark: data.receipt })
  },
  notifications: (sinceTs?: number) =>
    http.get('/task/notifications', { params: sinceTs ? { since_ts: sinceTs } : {} }),
  clearNotifications: () => http.post('/task/notifications/clear'),
}

// AI 巡检（云端安全隐患智能识别）
export const inspectionApi = {
  start: (data: { frequency?: number; project_id?: number }) => {
    const { frequency, ...rest } = data
    return http.post('/inspection/start', frequency ? { frequency_seconds: frequency, ...rest } : rest)
  },
  history: (params?: any) => http.get('/inspection/history', { params }),
  detail: (id: number) => http.get(`/inspection/${id}/stats`),
  stop: (id: number) => http.post(`/inspection/${id}/stop`),
  notifications: (sinceTs?: number) =>
    http.get('/task/notifications', { params: sinceTs ? { since_ts: sinceTs } : {} }),
  clearNotifications: () => http.post('/task/notifications/clear'),
}

export { default as http } from './client'
