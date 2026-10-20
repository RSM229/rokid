import { request } from '../../services/request'
import { session } from '../../services/session'
import { themed } from '../../utils/theme'

interface VisaItem {
  id: number
  title: string
  visa_no: string
  apply_date: string
  reason: string
  content: string
  amount: number
  status: string
  approver: string
  approve_date: string
  approve_opinion: string
  statusClass?: string
  amountText?: string
}

const STATUS_OPTIONS = ['全部', '草稿', '待审批', '已批准', '已驳回']

/** 状态 → ASCII 样式类（WXSS 编译器不接受非 ASCII 选择器） */
function statusClass(status: string): string {
  if (status === '待审批') return 's-pending'
  if (status === '已批准') return 's-ok'
  if (status === '已驳回') return 's-bad'
  return 's-draft'
}

/** 金额 → ¥xx,xxx.xx 千分位格式（¥ 在 wxml 里拼） */
function formatAmount(amount: number): string {
  const v = Number(amount) || 0
  return v.toFixed(2).replace(/\B(?=(\d{3})+(?!\d))/g, ',')
}

function withDisplay(items: VisaItem[]): VisaItem[] {
  return (Array.isArray(items) ? items : []).map((v) => ({
    ...v,
    statusClass: statusClass(v.status),
    amountText: formatAmount(v.amount),
  }))
}

function today(): string {
  const d = new Date()
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

Page(themed({
  data: {
    items: [] as VisaItem[],
    statusOptions: STATUS_OPTIONS,
    statusIndex: 0,
    loading: false,
    // 新建/编辑弹层
    showForm: false,
    editId: 0,
    formTitle: '',
    formVisaNo: '',
    formApplyDate: '',
    formReason: '',
    formContent: '',
    formAmount: '',
    saving: false,
    // 详情弹层
    showDetail: false,
    detail: null as VisaItem | null,
  },

  onShow() {
    if (!session.getToken()) {
      wx.reLaunch({ url: '/pages/login/index' })
      return
    }
    this.load()
  },

  onPullDownRefresh() {
    this.load().finally(() => wx.stopPullDownRefresh())
  },

  async load() {
    this.setData({ loading: true })
    try {
      const status = STATUS_OPTIONS[this.data.statusIndex]
      const qs = this.data.statusIndex > 0 ? `?status=${encodeURIComponent(status)}` : ''
      const res = await request<{ items: VisaItem[] }>(`/visa${qs}`)
      this.setData({ items: withDisplay(res.items) })
    } catch (error) {
      wx.showToast({ title: (error as Error).message, icon: 'none' })
    } finally {
      this.setData({ loading: false })
    }
  },

  onStatusChange(event: WechatMiniprogram.PickerChange) {
    this.setData({ statusIndex: Number(event.detail.value) })
    this.load()
  },

  _find(event: WechatMiniprogram.TouchEvent): VisaItem | undefined {
    const id = Number(event.currentTarget.dataset.id)
    return this.data.items.find((v) => v.id === id)
  },

  // ---------- 详情弹层 ----------
  openDetail(event: WechatMiniprogram.TouchEvent) {
    const item = this._find(event)
    if (!item) return
    this.setData({ showDetail: true, detail: item })
  },

  closeDetail() {
    this.setData({ showDetail: false, detail: null })
  },

  // ---------- 新建/编辑弹层 ----------
  openCreate() {
    this.setData({
      showForm: true,
      editId: 0,
      formTitle: '',
      formVisaNo: '',
      formApplyDate: today(),
      formReason: '',
      formContent: '',
      formAmount: '',
    })
  },

  openEdit(event: WechatMiniprogram.TouchEvent) {
    const item = this._find(event)
    if (!item) return
    this.setData({
      showDetail: false,
      showForm: true,
      editId: item.id,
      formTitle: item.title || '',
      formVisaNo: item.visa_no || '',
      formApplyDate: item.apply_date || today(),
      formReason: item.reason || '',
      formContent: item.content || '',
      formAmount: item.amount || item.amount === 0 ? String(item.amount) : '',
    })
  },

  closeForm() {
    this.setData({ showForm: false })
  },

  onFormInput(event: WechatMiniprogram.Input) {
    const { field } = event.currentTarget.dataset as { field: string }
    this.setData({ [field]: event.detail.value })
  },

  onApplyDateChange(event: WechatMiniprogram.PickerChange) {
    this.setData({ formApplyDate: String(event.detail.value) })
  },

  async save() {
    if (this.data.saving) return
    if (!this.data.formTitle.trim()) {
      wx.showToast({ title: '请填写标题', icon: 'none' })
      return
    }
    const amount = this.data.formAmount === '' ? 0 : Number(this.data.formAmount)
    if (Number.isNaN(amount) || amount < 0) {
      wx.showToast({ title: '金额格式不正确', icon: 'none' })
      return
    }
    const body = {
      title: this.data.formTitle.trim(),
      visa_no: this.data.formVisaNo.trim(),
      apply_date: this.data.formApplyDate,
      reason: this.data.formReason.trim(),
      content: this.data.formContent.trim(),
      amount,
    }
    this.setData({ saving: true })
    wx.showLoading({ title: '保存中...', mask: true })
    try {
      if (this.data.editId) {
        await request(`/visa/${this.data.editId}`, { method: 'PUT', data: body })
      } else {
        await request('/visa', { method: 'POST', data: body })
      }
      wx.hideLoading()
      wx.showToast({ title: '已保存', icon: 'success' })
      this.setData({ showForm: false })
      this.load()
    } catch (error) {
      wx.hideLoading()
      wx.showToast({ title: (error as Error).message, icon: 'none' })
    } finally {
      this.setData({ saving: false })
    }
  },

  // ---------- 流程操作 ----------
  del(event: WechatMiniprogram.TouchEvent) {
    const item = this._find(event)
    if (!item) return
    wx.showModal({
      title: '删除签证',
      content: `确定删除「${item.title}」？`,
      success: async (res) => {
        if (!res.confirm) return
        try {
          await request(`/visa/${item.id}`, { method: 'DELETE' })
          wx.showToast({ title: '已删除', icon: 'success' })
          this.setData({ showDetail: false, detail: null })
          this.load()
        } catch (error) {
          wx.showToast({ title: (error as Error).message, icon: 'none' })
        }
      },
    })
  },

  submit(event: WechatMiniprogram.TouchEvent) {
    const item = this._find(event)
    if (!item) return
    wx.showModal({
      title: '提交审批',
      content: `提交「${item.title}」进入待审批？`,
      success: async (res) => {
        if (!res.confirm) return
        try {
          await request(`/visa/${item.id}/submit`, { method: 'POST' })
          wx.showToast({ title: '已提交', icon: 'success' })
          this.setData({ showDetail: false, detail: null })
          this.load()
        } catch (error) {
          wx.showToast({ title: (error as Error).message, icon: 'none' })
        }
      },
    })
  },

  /** 批准/驳回：用可输入的 showModal 收集审批意见 */
  review(event: WechatMiniprogram.TouchEvent) {
    const item = this._find(event)
    if (!item) return
    const { action } = event.currentTarget.dataset as { action: 'approve' | 'reject' }
    const isApprove = action === 'approve'
    wx.showModal({
      title: isApprove ? '批准签证' : '驳回签证',
      editable: true,
      placeholderText: '审批意见',
      confirmText: isApprove ? '批准' : '驳回',
      success: async (res) => {
        if (!res.confirm) return
        try {
          await request(`/visa/${item.id}/review`, {
            method: 'POST',
            data: { action, opinion: (res.content || '').trim() },
          })
          wx.showToast({ title: isApprove ? '已批准' : '已驳回', icon: 'success' })
          this.setData({ showDetail: false, detail: null })
          this.load()
        } catch (error) {
          wx.showToast({ title: (error as Error).message, icon: 'none' })
        }
      },
    })
  },
}))
