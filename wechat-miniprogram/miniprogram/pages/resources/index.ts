import { request } from '../../services/request'
import { session } from '../../services/session'
import { themed } from '../../utils/theme'

interface MachineItem {
  name: string
  count: number
}

interface MaterialItem {
  name: string
  spec: string
  quantity: number
  unit: string
}

interface ResourceItem {
  id: number
  record_date: string
  labor_count: number
  labor_detail: string
  machines: MachineItem[]
  materials: MaterialItem[]
  remark: string
}

interface ResourceSummary {
  days: number
  labor_total: number
  labor_by_day: { date: string; labor: number }[]
  machine_top: MachineItem[]
}

/** 表单里的动态行（数量先按字符串收，提交时再转数值） */
interface MachineFormRow {
  name: string
  count: string
}

interface MaterialFormRow {
  name: string
  spec: string
  quantity: string
  unit: string
}

function today(): string {
  const d = new Date()
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

Page(themed({
  data: {
    items: [] as ResourceItem[],
    summary: null as ResourceSummary | null,
    machineTopText: '',
    loading: false,
    showForm: false,
    editId: 0,
    formDate: '',
    formLaborCount: '',
    formLaborDetail: '',
    formMachines: [] as MachineFormRow[],
    formMaterials: [] as MaterialFormRow[],
    formRemark: '',
    saving: false,
  },

  onShow() {
    if (!session.getToken()) {
      wx.reLaunch({ url: '/pages/login/index' })
      return
    }
    this.load()
    this.loadSummary()
  },

  onPullDownRefresh() {
    Promise.all([this.load(), this.loadSummary()]).finally(() => wx.stopPullDownRefresh())
  },

  async load() {
    this.setData({ loading: true })
    try {
      const res = await request<{ items: ResourceItem[] }>('/resources')
      this.setData({ items: Array.isArray(res.items) ? res.items : [] })
    } catch (error) {
      wx.showToast({ title: (error as Error).message, icon: 'none' })
    } finally {
      this.setData({ loading: false })
    }
  },

  async loadSummary() {
    try {
      const res = await request<ResourceSummary>('/resources/summary')
      const top = Array.isArray(res.machine_top) ? res.machine_top.slice(0, 3) : []
      this.setData({
        summary: res,
        machineTopText: top.map((m) => `${m.name}×${m.count}`).join('、'),
      })
    } catch {
      // 统计条加载失败不阻塞列表
    }
  },

  // ---------- 弹层表单 ----------
  openCreate() {
    this.setData({
      showForm: true,
      editId: 0,
      formDate: today(),
      formLaborCount: '',
      formLaborDetail: '',
      formMachines: [],
      formMaterials: [],
      formRemark: '',
    })
  },

  openEdit(event: WechatMiniprogram.TouchEvent) {
    const id = Number(event.currentTarget.dataset.id)
    const item = this.data.items.find((r) => r.id === id)
    if (!item) return
    this.setData({
      showForm: true,
      editId: id,
      formDate: item.record_date || today(),
      formLaborCount: String(item.labor_count ?? ''),
      formLaborDetail: item.labor_detail || '',
      formMachines: (Array.isArray(item.machines) ? item.machines : []).map((m) => ({
        name: m.name || '',
        count: String(m.count ?? ''),
      })),
      formMaterials: (Array.isArray(item.materials) ? item.materials : []).map((m) => ({
        name: m.name || '',
        spec: m.spec || '',
        quantity: String(m.quantity ?? ''),
        unit: m.unit || '',
      })),
      formRemark: item.remark || '',
    })
  },

  closeForm() {
    this.setData({ showForm: false })
  },

  onDateChange(event: WechatMiniprogram.PickerChange) {
    this.setData({ formDate: String(event.detail.value) })
  },

  onFormInput(event: WechatMiniprogram.Input) {
    const { field } = event.currentTarget.dataset as { field: string }
    this.setData({ [field]: event.detail.value })
  },

  // ---------- 机械动态行 ----------
  addMachine() {
    this.setData({ formMachines: this.data.formMachines.concat([{ name: '', count: '' }]) })
  },

  removeMachine(event: WechatMiniprogram.TouchEvent) {
    const idx = Number(event.currentTarget.dataset.index)
    const rows = this.data.formMachines.slice()
    rows.splice(idx, 1)
    this.setData({ formMachines: rows })
  },

  onMachineInput(event: WechatMiniprogram.Input) {
    const { index, field } = event.currentTarget.dataset as { index: number; field: string }
    this.setData({ [`formMachines[${index}].${field}`]: event.detail.value })
  },

  // ---------- 材料动态行 ----------
  addMaterial() {
    this.setData({
      formMaterials: this.data.formMaterials.concat([{ name: '', spec: '', quantity: '', unit: '' }]),
    })
  },

  removeMaterial(event: WechatMiniprogram.TouchEvent) {
    const idx = Number(event.currentTarget.dataset.index)
    const rows = this.data.formMaterials.slice()
    rows.splice(idx, 1)
    this.setData({ formMaterials: rows })
  },

  onMaterialInput(event: WechatMiniprogram.Input) {
    const { index, field } = event.currentTarget.dataset as { index: number; field: string }
    this.setData({ [`formMaterials[${index}].${field}`]: event.detail.value })
  },

  // ---------- 保存 / 删除 ----------
  async save() {
    if (this.data.saving) return
    if (!this.data.formDate) {
      wx.showToast({ title: '请选择日期', icon: 'none' })
      return
    }
    const laborCount = Number(this.data.formLaborCount)
    if (this.data.formLaborCount === '' || Number.isNaN(laborCount) || laborCount < 0) {
      wx.showToast({ title: '请填写人工数量', icon: 'none' })
      return
    }
    const machines = this.data.formMachines
      .filter((m) => m.name.trim())
      .map((m) => ({ name: m.name.trim(), count: Number(m.count) || 0 }))
    const materials = this.data.formMaterials
      .filter((m) => m.name.trim())
      .map((m) => ({
        name: m.name.trim(),
        spec: m.spec.trim(),
        quantity: Number(m.quantity) || 0,
        unit: m.unit.trim(),
      }))
    const body = {
      record_date: this.data.formDate,
      labor_count: laborCount,
      labor_detail: this.data.formLaborDetail.trim(),
      machines,
      materials,
      remark: this.data.formRemark.trim(),
    }
    this.setData({ saving: true })
    wx.showLoading({ title: '保存中...', mask: true })
    try {
      if (this.data.editId) {
        await request(`/resources/${this.data.editId}`, { method: 'PUT', data: body })
      } else {
        await request('/resources', { method: 'POST', data: body })
      }
      wx.hideLoading()
      wx.showToast({ title: '已保存', icon: 'success' })
      this.setData({ showForm: false })
      this.load()
      this.loadSummary()
    } catch (error) {
      wx.hideLoading()
      wx.showToast({ title: (error as Error).message, icon: 'none' })
    } finally {
      this.setData({ saving: false })
    }
  },

  del() {
    const id = this.data.editId
    if (!id) return
    wx.showModal({
      title: '删除记录',
      content: '确定删除该日资源投入记录？',
      success: async (res) => {
        if (!res.confirm) return
        try {
          await request(`/resources/${id}`, { method: 'DELETE' })
          wx.showToast({ title: '已删除', icon: 'success' })
          this.setData({ showForm: false })
          this.load()
          this.loadSummary()
        } catch (error) {
          wx.showToast({ title: (error as Error).message, icon: 'none' })
        }
      },
    })
  },
}))
