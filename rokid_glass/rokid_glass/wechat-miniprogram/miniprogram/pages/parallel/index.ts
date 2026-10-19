import { request } from '../../services/request'
import { session } from '../../services/session'
import { themed } from '../../utils/theme'

interface ParallelItem {
  id: number
  inspect_date: string
  part: string
  item: string
  method: string
  basis: string
  result_detail: string
  conclusion: string
  treatment: string
  statusClass?: string
}

interface ParallelForm {
  inspect_date: string
  part: string
  item: string
  method: string
  basis: string
  result_detail: string
  conclusion: string
  treatment: string
}

const EMPTY_FORM: ParallelForm = {
  inspect_date: '',
  part: '',
  item: '',
  method: '',
  basis: '',
  result_detail: '',
  conclusion: '',
  treatment: '',
}

const CONCLUSION_OPTIONS = ['合格', '不合格']

function today(): string {
  const d = new Date()
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

/** 结论 → ASCII 样式类（WXSS 编译器不接受非 ASCII 选择器） */
function statusClass(conclusion: string): string {
  if (conclusion === '合格') return 's-ok'
  if (conclusion === '不合格') return 's-bad'
  return 's-pending'
}

Page(themed({
  data: {
    records: [] as ParallelItem[],
    keyword: '',
    conclusionOptions: CONCLUSION_OPTIONS,
    loading: false,
    showForm: false,
    saving: false,
    editingId: 0,
    form: { ...EMPTY_FORM } as ParallelForm,
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
      const qs = this.data.keyword.trim() ? `?keyword=${encodeURIComponent(this.data.keyword.trim())}` : ''
      const res = await request<{ items: ParallelItem[] }>(`/parallel${qs}`)
      const items = (Array.isArray(res.items) ? res.items : []).map((r) => ({
        ...r,
        statusClass: statusClass(r.conclusion),
      }))
      this.setData({ records: items })
    } catch (error) {
      wx.showToast({ title: (error as Error).message, icon: 'none' })
    } finally {
      this.setData({ loading: false })
    }
  },

  // ---------- 搜索 ----------
  onKeywordInput(event: WechatMiniprogram.Input) {
    this.setData({ keyword: event.detail.value })
  },

  onSearch() {
    this.load()
  },

  // ---------- 弹层表单 ----------
  openCreate() {
    this.setData({ showForm: true, editingId: 0, form: { ...EMPTY_FORM, inspect_date: today() } })
  },

  async openEdit(event: WechatMiniprogram.TouchEvent) {
    const id = Number(event.currentTarget.dataset.id)
    let rec: ParallelItem
    try {
      rec = await request<ParallelItem>(`/parallel/${id}`)
    } catch (error) {
      wx.showToast({ title: (error as Error).message, icon: 'none' })
      return
    }
    this.setData({
      showForm: true,
      editingId: id,
      form: {
        inspect_date: rec.inspect_date || today(),
        part: rec.part || '',
        item: rec.item || '',
        method: rec.method || '',
        basis: rec.basis || '',
        result_detail: rec.result_detail || '',
        conclusion: rec.conclusion || '',
        treatment: rec.treatment || '',
      },
    })
  },

  closeForm() {
    this.setData({ showForm: false })
  },

  onDateChange(event: WechatMiniprogram.PickerChange) {
    this.setData({ 'form.inspect_date': String(event.detail.value) })
  },

  onPartInput(event: WechatMiniprogram.Input) {
    this.setData({ 'form.part': event.detail.value })
  },

  onItemInput(event: WechatMiniprogram.Input) {
    this.setData({ 'form.item': event.detail.value })
  },

  onMethodInput(event: WechatMiniprogram.Input) {
    this.setData({ 'form.method': event.detail.value })
  },

  async generateDocument() {
    const id = this.data.editingId
    if (!id) { wx.showToast({ title: '请先保存检查记录', icon: 'none' }); return }
    try {
      const res = await request<{ doc_id: number }>(`/parallel/${id}/document`, { method: 'POST' })
      wx.navigateTo({ url: `/pages/documents/detail?id=${res.doc_id}` })
    } catch (error) {
      wx.showToast({ title: (error as Error).message, icon: 'none' })
    }
  },

  onBasisInput(event: WechatMiniprogram.Input) {
    this.setData({ 'form.basis': event.detail.value })
  },

  onResultInput(event: WechatMiniprogram.Input) {
    this.setData({ 'form.result_detail': event.detail.value })
  },

  onConclusionChange(event: WechatMiniprogram.PickerChange) {
    this.setData({ 'form.conclusion': CONCLUSION_OPTIONS[Number(event.detail.value)] })
  },

  onTreatmentInput(event: WechatMiniprogram.Input) {
    this.setData({ 'form.treatment': event.detail.value })
  },

  // ---------- 保存 / 删除 ----------
  async save() {
    if (this.data.saving) return
    const f = this.data.form
    if (!f.inspect_date) {
      wx.showToast({ title: '请选择检验日期', icon: 'none' })
      return
    }
    if (!f.part.trim()) {
      wx.showToast({ title: '请输入检验部位', icon: 'none' })
      return
    }
    if (!f.item.trim()) {
      wx.showToast({ title: '请输入检验项目', icon: 'none' })
      return
    }
    if (!f.result_detail.trim()) {
      wx.showToast({ title: '请填写检查记录', icon: 'none' })
      return
    }
    if (!CONCLUSION_OPTIONS.includes(f.conclusion)) {
      wx.showToast({ title: '请选择结论', icon: 'none' })
      return
    }
    this.setData({ saving: true })
    try {
      const body = {
        inspect_date: f.inspect_date,
        part: f.part.trim(),
        item: f.item.trim(),
        method: f.method.trim(),
        basis: f.basis.trim(),
        result_detail: f.result_detail.trim(),
        conclusion: f.conclusion,
        treatment: f.treatment.trim(),
      }
      if (this.data.editingId) {
        await request(`/parallel/${this.data.editingId}`, { method: 'PUT', data: body })
      } else {
        await request('/parallel', { method: 'POST', data: body })
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
      title: '删除检验记录',
      content: '确定删除该平行检验记录？',
      success: async (res) => {
        if (!res.confirm) return
        try {
          await request(`/parallel/${id}`, { method: 'DELETE' })
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
