import { request } from '../../services/request'
import { session } from '../../services/session'
import { themed } from '../../utils/theme'

interface ProgressItem {
  id: number
  record_date: string
  part: string
  plan_percent: number
  actual_percent: number | null
  description: string
  updated_at?: string
  statusClass?: string
  statusText?: string
}

interface LatestProgress {
  actual_percent: number | null
  plan_percent: number
  record_date: string
  part: string
}

interface ProgressForm {
  record_date: string
  part: string
  plan_percent: number
  actual_percent: number
  description: string
}

const EMPTY_FORM: ProgressForm = {
  record_date: '',
  part: '',
  plan_percent: 0,
  actual_percent: 0,
  description: '',
}

function today(): string {
  const d = new Date()
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

/** 实际 vs 计划 → ASCII 样式类（WXSS 编译器不接受非 ASCII 选择器） */
function statusClass(item: ProgressItem): { statusClass: string; statusText: string } {
  if (item.actual_percent === null || item.actual_percent === undefined) {
    return { statusClass: 's-pending', statusText: '待记录' }
  }
  if (item.actual_percent >= item.plan_percent) {
    return { statusClass: 's-ok', statusText: '达标' }
  }
  return { statusClass: 's-bad', statusText: '滞后' }
}

Page(themed({
  data: {
    latest: null as LatestProgress | null,
    records: [] as ProgressItem[],
    loading: false,
    showForm: false,
    saving: false,
    editingId: 0,
    form: { ...EMPTY_FORM } as ProgressForm,
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
      const res = await request<{ items: ProgressItem[] }>('/progress')
      const items = (Array.isArray(res.items) ? res.items : []).map((r) => ({
        ...r,
        ...statusClass(r),
      }))
      this.setData({ records: items })
      this.loadLatest()
    } catch (error) {
      wx.showToast({ title: (error as Error).message, icon: 'none' })
    } finally {
      this.setData({ loading: false })
    }
  },

  async loadLatest() {
    try {
      const res = await request<LatestProgress>('/progress/latest')
      this.setData({ latest: res })
    } catch {
      // 最新进度加载失败不阻塞列表
    }
  },

  // ---------- 弹层表单 ----------
  openCreate() {
    this.setData({ showForm: true, editingId: 0, form: { ...EMPTY_FORM, record_date: today() } })
  },

  openEdit(event: WechatMiniprogram.TouchEvent) {
    const id = Number(event.currentTarget.dataset.id)
    const rec = this.data.records.find((r) => r.id === id)
    if (!rec) return
    this.setData({
      showForm: true,
      editingId: id,
      form: {
        record_date: rec.record_date || today(),
        part: rec.part || '',
        plan_percent: rec.plan_percent || 0,
        actual_percent: rec.actual_percent ?? 0,
        description: rec.description || '',
      },
    })
  },

  closeForm() {
    this.setData({ showForm: false })
  },

  onDateChange(event: WechatMiniprogram.PickerChange) {
    this.setData({ 'form.record_date': String(event.detail.value) })
  },

  onPartInput(event: WechatMiniprogram.Input) {
    this.setData({ 'form.part': event.detail.value })
  },

  onPlanSlide(event: WechatMiniprogram.SliderChange) {
    this.setData({ 'form.plan_percent': Number(event.detail.value) })
  },

  onActualSlide(event: WechatMiniprogram.SliderChange) {
    this.setData({ 'form.actual_percent': Number(event.detail.value) })
  },

  onDescInput(event: WechatMiniprogram.Input) {
    this.setData({ 'form.description': event.detail.value })
  },

  // ---------- 保存 / 删除 ----------
  async save() {
    if (this.data.saving) return
    const f = this.data.form
    if (!f.record_date) {
      wx.showToast({ title: '请选择记录日期', icon: 'none' })
      return
    }
    if (!f.part.trim()) {
      wx.showToast({ title: '请输入施工部位', icon: 'none' })
      return
    }
    this.setData({ saving: true })
    try {
      const body = {
        record_date: f.record_date,
        part: f.part.trim(),
        plan_percent: f.plan_percent,
        actual_percent: f.actual_percent,
        description: f.description.trim(),
      }
      if (this.data.editingId) {
        await request(`/progress/${this.data.editingId}`, { method: 'PUT', data: body })
      } else {
        await request('/progress', { method: 'POST', data: body })
      }
      wx.showToast({ title: '已保存', icon: 'success' })
      this.setData({ showForm: false })
      this.load()
    } catch (error) {
      wx.showToast({ title: (error as Error).message, icon: 'none' })
    } finally {
      this.setData({ saving: false })
    }
  },

  removeRecord() {
    const id = this.data.editingId
    if (!id) return
    wx.showModal({
      title: '删除记录',
      content: '确定删除该进度记录？',
      success: async (res) => {
        if (!res.confirm) return
        try {
          await request(`/progress/${id}`, { method: 'DELETE' })
          wx.showToast({ title: '已删除', icon: 'success' })
          this.setData({ showForm: false })
          this.load()
        } catch (error) {
          wx.showToast({ title: (error as Error).message, icon: 'none' })
        }
      },
    })
  },
}))
