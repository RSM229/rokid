/**
 * prov-form — 省统表动态表单渲染组件（P2.1 模板层配套）
 *
 * 输入后端 /provform/schemas/{form_code} 返回的 schema（可只取部分 sections），
 * 按字段类型渲染 text/textarea/number/date/time/select，validate() 做与后端
 * 一致的必填/取值/格式校验并输出结构化 payload。
 *
 * 用法：
 *   <prov-form schema="{{basicSchema}}" value="{{formValues}}" bind:change="onFormChange" />
 *   const res = this.selectComponent('#pf').validate()  // {ok, errors, payload}
 */
interface ProvField {
  key: string
  label: string
  type: string
  required?: boolean
  options?: string[]
  placeholder?: string
  maxlen?: number
  system?: boolean
}

interface ProvSection {
  title: string
  fields: ProvField[]
}

interface ProvSchemaData {
  sections: ProvSection[]
  signoffs?: { role: string; required?: boolean }[]
}

Component({
  properties: {
    schema: {
      type: Object,
      value: { sections: [], signoffs: [] } as ProvSchemaData,
    },
    value: {
      type: Object,
      value: {} as Record<string, string>,
    },
  },

  data: {
    errors: {} as Record<string, string>,
  },

  methods: {
    _schema(): ProvSchemaData {
      return (this.data.schema as ProvSchemaData) || { sections: [], signoffs: [] }
    },

    _set(key: string, val: string) {
      const value = { ...(this.data.value as Record<string, string>), [key]: val }
      const errors = { ...this.data.errors }
      delete errors[key]
      this.setData({ value, errors })
      this.triggerEvent('change', { key, value, payload: value })
    },

    onInput(event: WechatMiniprogram.Input) {
      const key = String(event.currentTarget.dataset.key || '')
      if (key) this._set(key, event.detail.value)
    },

    onPicker(event: WechatMiniprogram.PickerChange) {
      const key = String(event.currentTarget.dataset.key || '')
      if (key) this._set(key, String(event.detail.value))
    },

    onDateTime(event: WechatMiniprogram.CustomEvent<{ value: string }>) {
      const key = String(event.currentTarget.dataset.key || '')
      if (key) this._set(key, event.detail.value)
    },

    onSelect(event: WechatMiniprogram.PickerChange) {
      const key = String(event.currentTarget.dataset.key || '')
      const field = this._field(key)
      const idx = Number(event.detail.value)
      const options: string[] = (field && field.options) || []
      if (key && options[idx] !== undefined) this._set(key, String(options[idx]))
    },

    _field(key: string): ProvField | null {
      for (const sec of this._schema().sections || []) {
        for (const f of sec.fields || []) {
          if (f.key === key) return f
        }
      }
      return null
    },

    /** 与后端 provform_schema.validate_values 同规则的客户端校验 */
    validate(): { ok: boolean; errors: Record<string, string>; payload: Record<string, string> } {
      const errors: Record<string, string> = {}
      const payload: Record<string, string> = {}
      const values = this.data.value as Record<string, string>
      for (const sec of this._schema().sections || []) {
      for (const f of sec.fields || []) {
        const raw = values[f.key]
        const val = raw === undefined || raw === null ? '' : String(raw).trim()
        payload[f.key] = val
        // 系统生成字段（工程项目名称/编号/施工单位/项目监理机构）由后端自填，
        // 前端只读展示，跳过其必填/格式校验
        if (f.system) continue
        if (f.required && !val) {
            errors[f.key] = '必填项不能为空'
            continue
          }
          if (!val) continue
          if (f.type === 'select' && f.options && f.options.length && !f.options.includes(val)) {
            errors[f.key] = '取值不在可选范围内'
          } else if (f.type === 'number' && isNaN(Number(val))) {
            errors[f.key] = '应为数字'
          } else if (f.type === 'date' && !/^\d{4}-\d{2}-\d{2}$/.test(val)) {
            errors[f.key] = '日期格式应为 YYYY-MM-DD'
          } else if (f.type === 'time' && !/^\d{2}:\d{2}$/.test(val)) {
            errors[f.key] = '时间格式应为 HH:MM'
          } else if (f.type === 'datetime' && !/^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(:\d{2})?$/.test(val)) {
            errors[f.key] = '日期时间格式应为 YYYY-MM-DD HH:mm'
          }
          if (f.maxlen && val.length > f.maxlen) {
            errors[f.key] = `长度超过 ${f.maxlen} 字`
          }
        }
      }
      this.setData({ errors })
      return { ok: Object.keys(errors).length === 0, errors, payload }
    },

    clearErrors() {
      this.setData({ errors: {} })
    },
  },
})
