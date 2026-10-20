import { request } from '../../services/request'
import { session } from '../../services/session'
import { themed } from '../../utils/theme'

interface Topic {
  topic: string
  conclusion: string
  owner: string
  deadline: string
  status: string
  statusClass?: string
}

interface MeetingDetail {
  title: string
  meeting_type: string
  meeting_date: string
  location: string
  host: string
  attendees: string
  topics: Topic[]
  content: string
  source: string
}

interface ProvSchemaSection {
  title: string
  fields: Record<string, unknown>[]
}

interface ProvSchema {
  sections: ProvSchemaSection[]
  signoffs?: Record<string, unknown>[]
}

interface AiDraft {
  title?: string
  meeting_type?: string
  meeting_date?: string
  location?: string
  host?: string
  attendees?: string
  topics?: Topic[]
  content?: string
}

const TYPE_OPTIONS = ['例会', '专题会', '协调会', '交底会', '其他']
const STATUS_CYCLE = ['待办', '进行中', '已完成']

/** 状态 → ASCII 样式类（WXSS 编译器不接受非 ASCII 选择器） */
function statusClass(status: string): string {
  if (status === '进行中') return 's-doing'
  if (status === '已完成') return 's-done'
  return 's-todo'
}

function withStatusClass(topics: Topic[]): Topic[] {
  return (Array.isArray(topics) ? topics : []).map((t) => ({ ...t, statusClass: statusClass(t.status) }))
}

Page(themed({
  data: {
    meetingId: 0,
    isNew: true,
    typeOptions: TYPE_OPTIONS,
    typeIndex: 0,
    basicSchema: { sections: [], signoffs: [] } as ProvSchema,
    formValues: {} as Record<string, string>,
    topics: [] as Topic[],
    content: '',
    source: 'manual',
    saving: false,
    showAiDialog: false,
    aiTranscript: '',
    aiLoading: false,
  },

  onLoad(options: Record<string, string>) {
    if (!session.getToken()) {
      wx.reLaunch({ url: '/pages/login/index' })
      return
    }
    this.loadSchema()
    const id = Number(options.id || 0)
    if (id) {
      this.setData({ meetingId: id, isNew: false })
      this.load(id)
    }
  },

  /** 省统表 GD-B-217 字段级 schema → 只取「基本信息」段交给 prov-form 渲染 */
  async loadSchema() {
    try {
      const schema = await request<ProvSchema>('/provform/schemas/gd_b_217_huiyi')
      const basic = (schema.sections || []).filter((s) => s.title === '基本信息')
      this.setData({
        basicSchema: { sections: basic, signoffs: schema.signoffs || [] },
      })
    } catch {
      // schema 拉取失败时用内置兜底（与后端 provform_schema 保持一致）
      this.setData({
        basicSchema: {
          sections: [{
            title: '基本信息',
            fields: [
              { key: 'title', label: '会议名称', type: 'text', required: true },
              { key: 'date', label: '会议时间', type: 'date', required: true },
              { key: 'location', label: '会议地点', type: 'text', required: true },
              { key: 'host', label: '主持人', type: 'text', required: true },
              { key: 'attendees', label: '参会单位及人员', type: 'textarea', required: true },
            ],
          }],
          signoffs: [],
        },
      })
    }
  },

  async load(id: number) {
    try {
      const m = await request<MeetingDetail>(`/meeting/${id}`)
      const typeIndex = Math.max(0, TYPE_OPTIONS.indexOf(m.meeting_type))
      this.setData({
        typeIndex,
        formValues: {
          title: m.title || '',
          date: m.meeting_date || '',
          location: m.location || '',
          host: m.host || '',
          attendees: m.attendees || '',
        },
        topics: withStatusClass(m.topics),
        content: m.content || '',
        source: m.source || 'manual',
      })
    } catch (error) {
      wx.showToast({ title: (error as Error).message, icon: 'none' })
    }
  },

  onTypeChange(event: WechatMiniprogram.PickerChange) {
    this.setData({ typeIndex: Number(event.detail.value) })
  },

  onFormChange(event: WechatMiniprogram.CustomEvent) {
    this.setData({ formValues: event.detail.payload || {} })
  },

  onContentInput(event: WechatMiniprogram.Input) {
    this.setData({ content: event.detail.value })
  },

  // ---------- 议题编辑 ----------
  addTopic() {
    const topics = this.data.topics.concat([{
      topic: '', conclusion: '', owner: '', deadline: '', status: '待办', statusClass: 's-todo',
    }])
    this.setData({ topics })
  },

  removeTopic(event: WechatMiniprogram.TouchEvent) {
    const idx = Number(event.currentTarget.dataset.index)
    const topics = this.data.topics.slice()
    topics.splice(idx, 1)
    this.setData({ topics })
  },

  cycleStatus(event: WechatMiniprogram.TouchEvent) {
    const idx = Number(event.currentTarget.dataset.index)
    const cur = this.data.topics[idx]
    const next = STATUS_CYCLE[(STATUS_CYCLE.indexOf(cur.status) + 1) % STATUS_CYCLE.length]
    this.setData({
      [`topics[${idx}].status`]: next,
      [`topics[${idx}].statusClass`]: statusClass(next),
    })
  },

  onTopicInput(event: WechatMiniprogram.Input) {
    const { index, field } = event.currentTarget.dataset as { index: number; field: string }
    this.setData({ [`topics[${index}].${field}`]: event.detail.value })
  },

  onTopicDeadline(event: WechatMiniprogram.PickerChange) {
    const idx = Number(event.currentTarget.dataset.index)
    this.setData({ [`topics[${idx}].deadline`]: String(event.detail.value) })
  },

  // ---------- AI 整理 ----------
  openAiDialog() {
    this.setData({ showAiDialog: true })
  },

  closeAiDialog() {
    this.setData({ showAiDialog: false })
  },

  onAiInput(event: WechatMiniprogram.Input) {
    this.setData({ aiTranscript: event.detail.value })
  },

  _applyDraft(draft: AiDraft) {
    const formValues = { ...this.data.formValues }
    if (draft.title) formValues.title = draft.title
    if (draft.meeting_date) formValues.date = draft.meeting_date
    if (draft.location) formValues.location = draft.location
    if (draft.host) formValues.host = draft.host
    if (draft.attendees) formValues.attendees = draft.attendees
    const typeIndex = Math.max(0, TYPE_OPTIONS.indexOf(draft.meeting_type || '例会'))
    this.setData({
      formValues,
      typeIndex,
      topics: Array.isArray(draft.topics) && draft.topics.length
        ? withStatusClass(draft.topics)
        : this.data.topics,
      content: draft.content || this.data.content,
      source: 'ai',
      showAiDialog: false,
      aiTranscript: '',
    })
  },

  runAiDraft() {
    const transcript = this.data.aiTranscript.trim()
    if (!transcript) {
      wx.showToast({ title: '请先粘贴转写文本', icon: 'none' })
      return
    }
    const hasContent = this.data.topics.length > 0 || !!this.data.content.trim()
    wx.showModal({
      title: 'AI 整理',
      content: hasContent ? '生成结果将覆盖当前议题与正文，确认继续？' : '开始按转写文本生成纪要草稿？',
      success: async (res) => {
        if (!res.confirm) return
        this.setData({ aiLoading: true })
        try {
          const draft = await request<AiDraft>('/meeting/ai-draft', {
            method: 'POST',
            data: { transcript, hint: '' },
          })
          this._applyDraft(draft)
          wx.showToast({ title: '已生成草稿，请核对', icon: 'none' })
        } catch (error) {
          wx.showToast({ title: (error as Error).message, icon: 'none' })
        } finally {
          this.setData({ aiLoading: false })
        }
      },
    })
  },

  // ---------- 保存 ----------
  async save() {
    if (this.data.saving) return
    const pf = this.selectComponent('#pf') as unknown as {
      validate(): { ok: boolean; errors: Record<string, string>; payload: Record<string, string> }
    } | null
    const result: { ok: boolean; errors: Record<string, string>; payload: Record<string, string> } = pf
      ? pf.validate()
      : { ok: true, errors: {}, payload: this.data.formValues }
    if (!result.ok) {
      const first = Object.values(result.errors)[0]
      wx.showToast({ title: first || '请完善必填项', icon: 'none' })
      return
    }
    const v = result.payload
    this.setData({ saving: true })
    wx.showLoading({ title: '保存中...', mask: true })
    try {
      const body = {
        title: v.title,
        meeting_type: TYPE_OPTIONS[this.data.typeIndex],
        meeting_date: v.date || '',
        location: v.location || '',
        host: v.host || '',
        attendees: v.attendees || '',
        topics: this.data.topics,
        content: this.data.content,
        source: this.data.source,
      }
      if (this.data.isNew) {
        const created = await request<{ id: number }>('/meeting', { method: 'POST', data: body })
        this.setData({ meetingId: created.id, isNew: false })
      } else {
        await request(`/meeting/${this.data.meetingId}`, { method: 'PUT', data: body })
      }
      wx.hideLoading()
      wx.showToast({ title: '已保存', icon: 'success' })
      setTimeout(() => wx.navigateBack({ fail: () => wx.reLaunch({ url: '/pages/meeting/index' }) }), 600)
    } catch (error) {
      wx.hideLoading()
      wx.showToast({ title: (error as Error).message, icon: 'none' })
    } finally {
      this.setData({ saving: false })
    }
  },
}))
