import { request } from '../../services/request'
import { session } from '../../services/session'
import { themed } from '../../utils/theme'

interface RecordItem {
  id: number
  direction: string
  doc_name: string
  doc_no: string
  unit: string
  trans_date: string
  handler: string
  remark: string
  dirClass?: string
}

interface IssueItem {
  id: number
  title: string
  issue_type: string
  part: string
  description: string
  status: string
  deadline: string
  statusClass?: string
  overdue?: boolean
}

interface RecordForm {
  id: number
  doc_name: string
  doc_no: string
  unit: string
  trans_date: string
  handler: string
  remark: string
}

interface IssueForm {
  id: number
  title: string
  part: string
  description: string
  deadline: string
}

const DIRECTION_OPTIONS = ['全部', '收', '发']
const DIRECTIONS = ['收', '发']
const ISSUE_STATUS_OPTIONS = ['全部', '待处理', '处理中', '已关闭']
const ISSUE_STATUS = ['待处理', '处理中', '已关闭']
const ISSUE_TYPES = ['质量', '安全', '进度', '其他']

const EMPTY_REC: RecordForm = { id: 0, doc_name: '', doc_no: '', unit: '', trans_date: '', handler: '', remark: '' }
const EMPTY_ISSUE: IssueForm = { id: 0, title: '', part: '', description: '', deadline: '' }

/** 状态 → ASCII 样式类（WXSS 编译器不接受非 ASCII 选择器） */
function statusClass(status: string): string {
  if (status === '处理中') return 's-doing'
  if (status === '已关闭') return 's-done'
  return 's-todo'
}

function dirClass(direction: string): string {
  return direction === '收' ? 's-in' : 's-out'
}

function todayYmd(): string {
  const today = new Date()
  return `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, '0')}-${String(today.getDate()).padStart(2, '0')}`
}

Page(themed({
  data: {
    tab: 'records',
    records: [] as RecordItem[],
    issues: [] as IssueItem[],
    directionOptions: DIRECTION_OPTIONS,
    directionIndex: 0,
    keyword: '',
    issueStatusOptions: ISSUE_STATUS_OPTIONS,
    issueStatusIndex: 0,
    showRecForm: false,
    recForm: { ...EMPTY_REC } as RecordForm,
    directions: DIRECTIONS,
    recDirIndex: 0,
    recSaving: false,
    showIssueForm: false,
    issueForm: { ...EMPTY_ISSUE } as IssueForm,
    issueTypes: ISSUE_TYPES,
    issueTypeIndex: 0,
    issueStatuses: ISSUE_STATUS,
    issueFormStatusIndex: 0,
    issueSaving: false,
    loading: false,
  },

  onShow() {
    if (!session.getToken()) {
      wx.reLaunch({ url: '/pages/login/index' })
      return
    }
    this.loadAll()
  },

  onPullDownRefresh() {
    this.loadAll().finally(() => wx.stopPullDownRefresh())
  },

  async loadAll() {
    this.setData({ loading: true })
    try {
      await Promise.all([this.loadRecords(), this.loadIssues()])
    } finally {
      this.setData({ loading: false })
    }
  },

  async loadRecords() {
    try {
      const parts: string[] = []
      if (this.data.directionIndex > 0) {
        parts.push(`direction=${encodeURIComponent(DIRECTION_OPTIONS[this.data.directionIndex])}`)
      }
      const keyword = this.data.keyword.trim()
      if (keyword) parts.push(`keyword=${encodeURIComponent(keyword)}`)
      const qs = parts.length ? `?${parts.join('&')}` : ''
      const res = await request<{ items: RecordItem[] }>(`/archive/records${qs}`)
      this.setData({
        records: (Array.isArray(res.items) ? res.items : []).map((r) => ({ ...r, dirClass: dirClass(r.direction) })),
      })
    } catch (error) {
      wx.showToast({ title: (error as Error).message, icon: 'none' })
    }
  },

  async loadIssues() {
    try {
      const status = ISSUE_STATUS_OPTIONS[this.data.issueStatusIndex]
      const qs = this.data.issueStatusIndex > 0 ? `?status=${encodeURIComponent(status)}` : ''
      const res = await request<{ items: IssueItem[] }>(`/archive/issues${qs}`)
      const ymd = todayYmd()
      this.setData({
        issues: (Array.isArray(res.items) ? res.items : []).map((i) => ({
          ...i,
          statusClass: statusClass(i.status),
          overdue: !!i.deadline && i.deadline < ymd,
        })),
      })
    } catch (error) {
      wx.showToast({ title: (error as Error).message, icon: 'none' })
    }
  },

  switchTab(event: WechatMiniprogram.TouchEvent) {
    const tab = String(event.currentTarget.dataset.tab)
    if (tab !== this.data.tab) this.setData({ tab })
  },

  onDirectionChange(event: WechatMiniprogram.PickerChange) {
    this.setData({ directionIndex: Number(event.detail.value) })
    this.loadRecords()
  },

  onKeywordInput(event: WechatMiniprogram.Input) {
    this.setData({ keyword: event.detail.value })
  },

  searchRecords() {
    this.loadRecords()
  },

  onIssueStatusChange(event: WechatMiniprogram.PickerChange) {
    this.setData({ issueStatusIndex: Number(event.detail.value) })
    this.loadIssues()
  },

  // ---------- 资料台账：弹层表单 ----------
  createRec() {
    this.setData({ showRecForm: true, recForm: { ...EMPTY_REC }, recDirIndex: 0 })
  },

  editRec(event: WechatMiniprogram.TouchEvent) {
    const id = Number(event.currentTarget.dataset.id)
    const item = this.data.records.find((r) => r.id === id)
    if (!item) return
    this.setData({
      showRecForm: true,
      recForm: {
        id: item.id,
        doc_name: item.doc_name || '',
        doc_no: item.doc_no || '',
        unit: item.unit || '',
        trans_date: item.trans_date || '',
        handler: item.handler || '',
        remark: item.remark || '',
      },
      recDirIndex: Math.max(0, DIRECTIONS.indexOf(item.direction)),
    })
  },

  closeRecForm() {
    this.setData({ showRecForm: false })
  },

  onRecFormInput(event: WechatMiniprogram.Input) {
    const { field } = event.currentTarget.dataset as { field: string }
    this.setData({ [`recForm.${field}`]: event.detail.value })
  },

  onRecDirChange(event: WechatMiniprogram.PickerChange) {
    this.setData({ recDirIndex: Number(event.detail.value) })
  },

  onRecFormDateChange(event: WechatMiniprogram.PickerChange) {
    this.setData({ 'recForm.trans_date': String(event.detail.value) })
  },

  async submitRecForm() {
    if (this.data.recSaving) return
    const f = this.data.recForm
    if (!f.doc_name.trim()) {
      wx.showToast({ title: '请输入资料名称', icon: 'none' })
      return
    }
    this.setData({ recSaving: true })
    try {
      const body = {
        direction: DIRECTIONS[this.data.recDirIndex],
        doc_name: f.doc_name.trim(),
        doc_no: f.doc_no.trim(),
        unit: f.unit.trim(),
        trans_date: f.trans_date,
        handler: f.handler.trim(),
        remark: f.remark,
      }
      if (f.id) {
        await request(`/archive/records/${f.id}`, { method: 'PUT', data: body })
      } else {
        await request('/archive/records', { method: 'POST', data: body })
      }
      wx.showToast({ title: '已保存', icon: 'success' })
      this.setData({ showRecForm: false })
      this.loadRecords()
    } catch (error) {
      wx.showToast({ title: (error as Error).message, icon: 'none' })
    } finally {
      this.setData({ recSaving: false })
    }
  },

  delRec(event: WechatMiniprogram.TouchEvent) {
    const id = Number(event.currentTarget.dataset.id)
    wx.showModal({
      title: '删除资料',
      content: '确定删除该资料记录？',
      success: async (res) => {
        if (!res.confirm) return
        try {
          await request(`/archive/records/${id}`, { method: 'DELETE' })
          wx.showToast({ title: '已删除', icon: 'success' })
          this.loadRecords()
        } catch (error) {
          wx.showToast({ title: (error as Error).message, icon: 'none' })
        }
      },
    })
  },

  // ---------- 问题清单：弹层表单 ----------
  createIssue() {
    this.setData({ showIssueForm: true, issueForm: { ...EMPTY_ISSUE }, issueTypeIndex: 0, issueFormStatusIndex: 0 })
  },

  editIssue(event: WechatMiniprogram.TouchEvent) {
    const id = Number(event.currentTarget.dataset.id)
    const item = this.data.issues.find((i) => i.id === id)
    if (!item) return
    this.setData({
      showIssueForm: true,
      issueForm: {
        id: item.id,
        title: item.title || '',
        part: item.part || '',
        description: item.description || '',
        deadline: item.deadline || '',
      },
      issueTypeIndex: Math.max(0, ISSUE_TYPES.indexOf(item.issue_type)),
      issueFormStatusIndex: Math.max(0, ISSUE_STATUS.indexOf(item.status)),
    })
  },

  closeIssueForm() {
    this.setData({ showIssueForm: false })
  },

  onIssueFormInput(event: WechatMiniprogram.Input) {
    const { field } = event.currentTarget.dataset as { field: string }
    this.setData({ [`issueForm.${field}`]: event.detail.value })
  },

  onIssueTypeChange(event: WechatMiniprogram.PickerChange) {
    this.setData({ issueTypeIndex: Number(event.detail.value) })
  },

  onIssueFormStatusChange(event: WechatMiniprogram.PickerChange) {
    this.setData({ issueFormStatusIndex: Number(event.detail.value) })
  },

  onIssueFormDateChange(event: WechatMiniprogram.PickerChange) {
    this.setData({ 'issueForm.deadline': String(event.detail.value) })
  },

  async submitIssueForm() {
    if (this.data.issueSaving) return
    const f = this.data.issueForm
    if (!f.title.trim()) {
      wx.showToast({ title: '请输入问题标题', icon: 'none' })
      return
    }
    this.setData({ issueSaving: true })
    try {
      const body = {
        title: f.title.trim(),
        issue_type: ISSUE_TYPES[this.data.issueTypeIndex],
        part: f.part.trim(),
        description: f.description,
        status: ISSUE_STATUS[this.data.issueFormStatusIndex],
        deadline: f.deadline,
      }
      if (f.id) {
        await request(`/archive/issues/${f.id}`, { method: 'PUT', data: body })
      } else {
        await request('/archive/issues', { method: 'POST', data: body })
      }
      wx.showToast({ title: '已保存', icon: 'success' })
      this.setData({ showIssueForm: false })
      this.loadIssues()
    } catch (error) {
      wx.showToast({ title: (error as Error).message, icon: 'none' })
    } finally {
      this.setData({ issueSaving: false })
    }
  },

  delIssue(event: WechatMiniprogram.TouchEvent) {
    const id = Number(event.currentTarget.dataset.id)
    wx.showModal({
      title: '删除问题单',
      content: '确定删除该问题单？',
      success: async (res) => {
        if (!res.confirm) return
        try {
          await request(`/archive/issues/${id}`, { method: 'DELETE' })
          wx.showToast({ title: '已删除', icon: 'success' })
          this.loadIssues()
        } catch (error) {
          wx.showToast({ title: (error as Error).message, icon: 'none' })
        }
      },
    })
  },
}))
