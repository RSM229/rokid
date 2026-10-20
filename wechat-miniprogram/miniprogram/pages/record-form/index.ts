import { request } from '../../services/request'
import { session } from '../../services/session'
import { themed } from '../../utils/theme'

/** 后端 /provform/schemas/{code}/steps 返回的单个字段 */
interface StepField {
  key: string
  canonical?: string
  label: string
  type: string
  kind?: string
  required: boolean
  readonly: boolean
  value: string
  options?: string[]
  placeholder?: string
  maxlen?: number
}

interface Step {
  index: number
  title: string
  fields: StepField[]
}

interface StepsResp {
  form_code: string
  name: string
  form_number: string
  source: string
  prefill_source: string
  prefill_count: number
  missing: string[]
  missing_count: number
  step_count: number
  signoffs?: Array<{ role: string; required?: boolean }>
  steps: Step[]
}

/** 喂给 prov-form 组件的字段形状（system = 只读） */
interface ProvFieldOut {
  key: string
  label: string
  type: string
  required: boolean
  system: boolean
  options?: string[]
  placeholder?: string
  maxlen?: number
}

interface FormValues {
  [key: string]: string
}

interface Signoff {
  role: string
  required?: boolean
}

Page(themed({
  data: {
    code: '',
    docId: 0,
    draftId: 0,
    isNew: true,
    title: '',
    loading: false,
    saving: false,
    // 分步
    steps: [] as Step[],
    stepIndex: 0,
    stepSchema: { sections: [] as Record<string, unknown>[], signoffs: [] as Signoff[] },
    stepDone: [] as boolean[],
    signoffs: [] as Signoff[],
    // 值 / 缺项
    formValues: {} as FormValues,
    missing: [] as string[],
    missingLabels: [] as string[],
    missingCount: 0,
    // 预填提示
    prefillSource: '',
    prefillCount: 0,
    source: '',
  },

  onLoad(options: Record<string, string>) {
    if (!session.getToken()) {
      wx.reLaunch({ url: '/pages/login/index' })
      return
    }
    const code = options.code || ''
    const id = Number(options.id || 0)
    const draftId = Number(options.draft_id || 0)
    if (!code) {
      wx.showToast({ title: '缺少表式参数', icon: 'none' })
      return
    }
    this.setData({ code, docId: id, draftId, isNew: !id })
    void this.loadSteps(id ? 0 : draftId)
    if (id) void this.loadValues(id)
  },

  /** 拉分步结构（字段表决定只读/必填），并用草稿预填 */
  async loadSteps(draftId: number) {
    this.setData({ loading: true })
    try {
      const query = draftId ? `?draft_id=${draftId}` : ''
      const resp = await request<StepsResp>(`/provform/schemas/${this.data.code}/steps${query}`)
      const steps = resp.steps || []
      const values: FormValues = {}
      for (const step of steps) {
        for (const field of step.fields) values[field.key] = field.value || ''
      }
      // 编辑态下 loadValues 可能先返回：已回显的非空值优先，避免被空预填覆盖
      const shown = this.data.formValues as FormValues
      for (const key of Object.keys(shown)) {
        if (String(shown[key] || '').trim()) values[key] = shown[key]
      }
      this.setData({
        steps,
        formValues: values,
        signoffs: resp.signoffs || [],
        title: this.data.isNew ? (resp.name || '') : this.data.title,
        source: resp.source || '',
        prefillSource: resp.prefill_source || '',
        prefillCount: resp.prefill_count || 0,
        loading: false,
      })
      this.applyStep(0)
      this.refreshMissing()
    } catch (error) {
      this.setData({ loading: false })
      wx.showToast({ title: (error as Error).message || '加载表式失败', icon: 'none' })
    }
  },

  /** 编辑回显：把文书 content（canonical key）反查为 schema-key → 值 */
  async loadValues(id: number) {
    try {
      const res = await request<{ form_code: string; title: string; fields: FormValues }>(
        `/document/${id}/form-values`,
      )
      const merged = { ...(this.data.formValues as FormValues), ...(res.fields || {}) }
      this.setData({ formValues: merged, title: res.title || this.data.title })
      this.refreshMissing()
    } catch (error) {
      wx.showToast({ title: (error as Error).message, icon: 'none' })
    }
  },

  /** 只把当前步的字段喂给 prov-form，组件本身零改动 */
  applyStep(index: number) {
    const steps = this.data.steps as Step[]
    const step = steps[index]
    if (!step) return
    const fields: ProvFieldOut[] = (step.fields || []).map((f) => {
      const out: ProvFieldOut = {
        key: f.key,
        label: f.label,
        type: f.type,
        // 只读栏不再标红星，否则用户会以为漏填
        required: f.readonly ? false : !!f.required,
        system: !!f.readonly,
      }
      if (f.options && f.options.length) out.options = f.options
      if (f.placeholder) out.placeholder = f.placeholder
      if (f.maxlen) out.maxlen = f.maxlen
      return out
    })
    this.setData({
      stepIndex: index,
      stepSchema: {
        sections: [{ title: step.title, fields }],
        // 签认栏只在最后一步出现，避免每步都重复一遍
        signoffs: index === steps.length - 1 ? (this.data.signoffs as Signoff[]) : [],
      },
    })
    const pf = this.selectComponent('#pf') as unknown as { clearErrors?: () => void } | null
    if (pf && pf.clearErrors) pf.clearErrors()
  },

  /** 重算缺项与每步完成度（本地口径与后端 build_steps 一致） */
  refreshMissing() {
    const steps = this.data.steps as Step[]
    const values = this.data.formValues as FormValues
    const missing: string[] = []
    const labels: string[] = []
    const done: boolean[] = []
    for (const step of steps) {
      let stepOk = true
      for (const field of step.fields) {
        if (field.readonly || !field.required) continue
        if (!String(values[field.key] || '').trim()) {
          missing.push(field.key)
          labels.push(field.label)
          stepOk = false
        }
      }
      done.push(stepOk)
    }
    this.setData({
      missing,
      missingCount: missing.length,
      missingLabels: labels.slice(0, 8),
      stepDone: done,
    })
  },

  onTitleInput(event: WechatMiniprogram.Input) {
    this.setData({ title: event.detail.value })
  },

  onFormChange(event: WechatMiniprogram.CustomEvent) {
    this.setData({ formValues: (event.detail.payload || {}) as FormValues })
    this.refreshMissing()
  },

  /** 用组件自带校验把当前步的必填/格式问题挡在切换前 */
  validateCurrent(): boolean {
    const pf = this.selectComponent('#pf') as unknown as {
      validate(): { ok: boolean; errors: Record<string, string> }
    } | null
    if (!pf) return true
    const result = pf.validate()
    if (result.ok) return true
    const first = Object.values(result.errors)[0]
    wx.showToast({ title: first || '请检查填写内容', icon: 'none' })
    return false
  },

  onNext() {
    if (!this.validateCurrent()) return
    this.applyStep((this.data.stepIndex as number) + 1)
  },

  onPrev() {
    this.applyStep((this.data.stepIndex as number) - 1)
  },

  onStepTap(event: WechatMiniprogram.TouchEvent) {
    const target = Number(event.currentTarget.dataset.i)
    const current = this.data.stepIndex as number
    if (!Number.isFinite(target) || target === current) return
    if (target > current && !this.validateCurrent()) return
    this.applyStep(target)
  },

  /** 跳到第一个缺项所在的步，并提示缺了几项 */
  jumpToFirstMissing() {
    const steps = this.data.steps as Step[]
    const missing = this.data.missing as string[]
    if (!missing.length) return
    const key = missing[0]
    const index = steps.findIndex((step) => step.fields.some((f) => f.key === key))
    if (index >= 0) this.applyStep(index)
    wx.showToast({ title: `还有 ${missing.length} 项必填`, icon: 'none' })
  },

  async save(): Promise<void> {
    if (this.data.saving) return
    if (!this.validateCurrent()) return
    this.refreshMissing()
    if ((this.data.missingCount as number) > 0) {
      this.jumpToFirstMissing()
      return
    }
    const values = this.data.formValues as FormValues
    this.setData({ saving: true })
    wx.showLoading({ title: '保存中…', mask: true })
    try {
      if (this.data.isNew) {
        const created = await request<{ doc_id: number }>('/document/create-from-schema', {
          method: 'POST',
          data: { form_code: this.data.code, title: this.data.title, fields: values },
        })
        this.setData({ docId: created.doc_id, isNew: false })
      } else {
        await request(`/document/${this.data.docId}/from-schema`, {
          method: 'PUT',
          data: { title: this.data.title, fields: values },
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
