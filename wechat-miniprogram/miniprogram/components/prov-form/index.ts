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

import { config, absoluteMediaUrl } from '../../config'
import { session } from '../../services/session'

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
    /** 照片字段的预览绝对地址（按 image 类型键算，避免误把文本值当地址拼） */
    absUrls: {} as Record<string, string>,
    /** 各照片字段的上传中状态 */
    uploading: {} as Record<string, boolean>,
  },

  observers: {
    'schema, value'() {
      const val = this.data.value as Record<string, string>
      const imgKeys = this._imageKeys()
      const absUrls: Record<string, string> = {}
      const v = val || {}
      for (const k of imgKeys) {
        if (v[k]) absUrls[k] = absoluteMediaUrl(v[k])
      }
      this.setData({ absUrls })
    },
  },

  methods: {
    _schema(): ProvSchemaData {
      return (this.data.schema as ProvSchemaData) || { sections: [], signoffs: [] }
    },

    _imageKeys(): string[] {
      const keys: string[] = []
      for (const sec of this._schema().sections || []) {
        for (const f of sec.fields || []) {
          if (f.type === 'image') keys.push(f.key)
        }
      }
      return keys
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
          if (f.type === 'image') {
            // 照片字段：非空即视为已上传，不再做格式/长度校验
            continue
          }
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

    /** 照片字段：调起拍照/选图并上传，成功后把返回的媒体地址存进值 */
    onChooseImage(event: WechatMiniprogram.TouchEvent) {
      const key = String(event.currentTarget.dataset.key || '')
      if (!key) return
      const uploading = this.data.uploading as Record<string, boolean>
      if (uploading[key]) return
      wx.chooseMedia({
        count: 1,
        mediaType: ['image'],
        sourceType: ['album', 'camera'],
        camera: 'back',
        success: (res) => {
          const file = res.tempFiles && res.tempFiles[0]
          if (file && file.tempFilePath) this._uploadImage(key, file.tempFilePath)
        },
      })
    },

    _uploadImage(key: string, filePath: string) {
      const uploading = { ...(this.data.uploading as Record<string, boolean>), [key]: true }
      this.setData({ uploading })
      const done = (ok: boolean, msg?: string) => {
        const u = { ...(this.data.uploading as Record<string, boolean>) }
        u[key] = false
        this.setData({ uploading: u })
        if (!ok && msg) wx.showToast({ title: msg, icon: 'none' })
      }
      wx.uploadFile({
        url: `${config.apiBaseUrl}/media/images`,
        filePath,
        name: 'file',
        header: { Authorization: `Bearer ${session.getToken()}` },
        success: (response) => {
          try {
            const data = JSON.parse(response.data) as { url?: string; detail?: string }
            if (response.statusCode !== 200 || !data.url) {
              throw new Error(typeof data.detail === 'string' ? data.detail : '照片上传失败')
            }
            this._set(key, data.url)
            done(true)
            wx.showToast({ title: '照片已上传', icon: 'success' })
          } catch (error) {
            done(false, (error as Error).message || '照片上传失败')
          }
        },
        fail: () => done(false, '上传失败，请重试'),
      })
    },

    onPreviewImage(event: WechatMiniprogram.TouchEvent) {
      const key = String(event.currentTarget.dataset.key || '')
      const url = (this.data.absUrls as Record<string, string>)[key]
      if (url) wx.previewImage({ urls: [url], current: url })
    },

    clearErrors() {
      this.setData({ errors: {} })
    },
  },
})
