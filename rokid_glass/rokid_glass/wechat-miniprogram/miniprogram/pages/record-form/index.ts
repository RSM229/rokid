import { request } from '../../services/request'
import { session } from '../../services/session'
import { themed } from '../../utils/theme'

interface ProvSchemaSection {
  title: string
  fields: Record<string, unknown>[]
}

interface ProvSchema {
  form_code: string
  form_number: string
  name: string
  version: string
  sections: ProvSchemaSection[]
  signoffs?: Record<string, unknown>[]
}

interface FormValues {
  [key: string]: string
}

Page(themed({
  data: {
    code: '',
    docId: 0,
    isNew: true,
    schema: {} as ProvSchema,
    formValues: {} as FormValues,
    title: '',
    loading: false,
    saving: false,
  },

  onLoad(options: Record<string, string>) {
    if (!session.getToken()) {
      wx.reLaunch({ url: '/pages/login/index' })
      return
    }
    const code = options.code || ''
    const id = Number(options.id || 0)
    if (!code) {
      wx.showToast({ title: '缺少表式参数', icon: 'none' })
      return
    }
    this.setData({ code, docId: id, isNew: !id })
    this.loadSchema()
    if (id) this.loadValues(id)
  },

  /** 加载省统表字段级 schema（全量 sections，含 system 标记） */
  async loadSchema() {
    try {
      const schema = await request<ProvSchema>(`/provform/schemas/${this.data.code}`)
      this.setData({ schema, title: schema.name || this.data.title })
    } catch {
      wx.showToast({ title: '加载表式失败', icon: 'none' })
    }
  },

  /** 编辑回显：把文书 content（canonical key）反查为 schema-key → 值 */
  async loadValues(id: number) {
    try {
      const res = await request<{ form_code: string; title: string; fields: FormValues }>(
        `/document/${id}/form-values`,
      )
      this.setData({ formValues: res.fields || {}, title: res.title || this.data.title })
    } catch (error) {
      wx.showToast({ title: (error as Error).message, icon: 'none' })
    }
  },

  onTitleInput(event: WechatMiniprogram.Input) {
    this.setData({ title: event.detail.value })
  },

  onFormChange(event: WechatMiniprogram.CustomEvent) {
    this.setData({ formValues: event.detail.payload || {} })
  },

  async save() {
    if (this.data.saving) return
    const pf = this.selectComponent('#pf') as unknown as {
      validate(): { ok: boolean; errors: Record<string, string>; payload: FormValues }
    } | null
    const result: { ok: boolean; errors: Record<string, string>; payload: FormValues } = pf
      ? pf.validate()
      : { ok: true, errors: {}, payload: this.data.formValues }
    if (!result.ok) {
      const first = Object.values(result.errors)[0]
      wx.showToast({ title: first || '请完善必填项', icon: 'none' })
      return
    }
    const v = result.payload
    this.setData({ saving: true })
    wx.showLoading({ title: '保存中…', mask: true })
    try {
      if (this.data.isNew) {
        const created = await request<{ doc_id: number }>('/document/create-from-schema', {
          method: 'POST',
          data: { form_code: this.data.code, title: this.data.title, fields: v },
        })
        this.setData({ docId: created.doc_id, isNew: false })
      } else {
        await request(`/document/${this.data.docId}/from-schema`, {
          method: 'PUT',
          data: { title: this.data.title, fields: v },
        })
      }
      wx.hideLoading()
      wx.showToast({ title: '已保存', icon: 'success' })
      setTimeout(() => {
        wx.navigateBack({ fail: () => wx.reLaunch({ url: '/pages/work/index' }) })
      }, 600)
    } catch (error) {
      wx.hideLoading()
      wx.showToast({ title: (error as Error).message, icon: 'none' })
    } finally {
      this.setData({ saving: false })
    }
  },
}))
