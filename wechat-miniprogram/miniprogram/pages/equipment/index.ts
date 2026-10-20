import { request } from '../../services/request'
import { session } from '../../services/session'
import { themed } from '../../utils/theme'

interface EquipmentItem {
  id: number
  name: string
  model_no: string
  serial_no: string
  unit: string
  calib_date: string
  calib_due: string
  status: string
  remark: string
  statusClass?: string
}

interface ExpiringItem extends EquipmentItem {
  days_left: number
  urgency?: string
}

interface EquipmentForm {
  id: number
  name: string
  model_no: string
  serial_no: string
  unit: string
  calib_date: string
  calib_due: string
  remark: string
}

const STATUS_OPTIONS = ['全部', '在用', '停用', '维修']
const EQ_STATUS = ['在用', '停用', '维修']

const EMPTY_FORM: EquipmentForm = {
  id: 0,
  name: '',
  model_no: '',
  serial_no: '',
  unit: '',
  calib_date: '',
  calib_due: '',
  remark: '',
}

/** 状态 → ASCII 样式类（WXSS 编译器不接受非 ASCII 选择器） */
function statusClass(status: string): string {
  if (status === '在用') return 's-active'
  if (status === '停用') return 's-off'
  return 's-fix'
}

Page(themed({
  data: {
    list: [] as EquipmentItem[],
    expiring: [] as ExpiringItem[],
    statusOptions: STATUS_OPTIONS,
    statusIndex: 0,
    formStatuses: EQ_STATUS,
    formStatusIndex: 0,
    showForm: false,
    form: { ...EMPTY_FORM } as EquipmentForm,
    loading: false,
    saving: false,
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
      const res = await request<{ items: EquipmentItem[] }>(`/equipment${qs}`)
      this.setData({
        list: (Array.isArray(res.items) ? res.items : []).map((e) => ({ ...e, statusClass: statusClass(e.status) })),
      })
      this.loadExpiring()
    } catch (error) {
      wx.showToast({ title: (error as Error).message, icon: 'none' })
    } finally {
      this.setData({ loading: false })
    }
  },

  async loadExpiring() {
    try {
      const res = await request<{ items: ExpiringItem[] }>('/equipment/expiring?days=30')
      const items = (Array.isArray(res.items) ? res.items : []).map((e) => ({
        ...e,
        urgency: e.days_left <= 7 ? 'dl-urgent' : 'dl-soon',
      }))
      this.setData({ expiring: items })
    } catch {
      // 到期提醒加载失败不阻塞列表
    }
  },

  onStatusChange(event: WechatMiniprogram.PickerChange) {
    this.setData({ statusIndex: Number(event.detail.value) })
    this.load()
  },

  // ---------- 弹层表单 ----------
  create() {
    this.setData({ showForm: true, form: { ...EMPTY_FORM }, formStatusIndex: 0 })
  },

  edit(event: WechatMiniprogram.TouchEvent) {
    const id = Number(event.currentTarget.dataset.id)
    const item = this.data.list.find((e) => e.id === id)
    if (!item) return
    this.setData({
      showForm: true,
      form: {
        id: item.id,
        name: item.name || '',
        model_no: item.model_no || '',
        serial_no: item.serial_no || '',
        unit: item.unit || '',
        calib_date: item.calib_date || '',
        calib_due: item.calib_due || '',
        remark: item.remark || '',
      },
      formStatusIndex: Math.max(0, EQ_STATUS.indexOf(item.status)),
    })
  },

  closeForm() {
    this.setData({ showForm: false })
  },

  onFormInput(event: WechatMiniprogram.Input) {
    const { field } = event.currentTarget.dataset as { field: string }
    this.setData({ [`form.${field}`]: event.detail.value })
  },

  onFormDateChange(event: WechatMiniprogram.PickerChange) {
    const { field } = event.currentTarget.dataset as { field: string }
    this.setData({ [`form.${field}`]: String(event.detail.value) })
  },

  onFormStatusChange(event: WechatMiniprogram.PickerChange) {
    this.setData({ formStatusIndex: Number(event.detail.value) })
  },

  async submitForm() {
    if (this.data.saving) return
    const f = this.data.form
    if (!f.name.trim()) {
      wx.showToast({ title: '请输入设备名称', icon: 'none' })
      return
    }
    this.setData({ saving: true })
    try {
      const body = {
        name: f.name.trim(),
        model_no: f.model_no.trim(),
        serial_no: f.serial_no.trim(),
        unit: f.unit.trim(),
        calib_date: f.calib_date,
        calib_due: f.calib_due,
        status: EQ_STATUS[this.data.formStatusIndex],
        remark: f.remark,
      }
      if (f.id) {
        await request(`/equipment/${f.id}`, { method: 'PUT', data: body })
      } else {
        await request('/equipment', { method: 'POST', data: body })
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

  del(event: WechatMiniprogram.TouchEvent) {
    const id = Number(event.currentTarget.dataset.id)
    wx.showModal({
      title: '删除设备',
      content: '确定删除该设备台账？',
      success: async (res) => {
        if (!res.confirm) return
        try {
          await request(`/equipment/${id}`, { method: 'DELETE' })
          wx.showToast({ title: '已删除', icon: 'success' })
          this.load()
        } catch (error) {
          wx.showToast({ title: (error as Error).message, icon: 'none' })
        }
      },
    })
  },
}))
