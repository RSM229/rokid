import { request } from '../../services/request'
import { session } from '../../services/session'
import { config } from '../../config'
import { getCached, CACHE_KEYS } from '../../services/cache'
import { MODEL_CATALOG, findModel } from '../../services/modelCatalog'
import { themed } from '../../utils/theme'

interface KbOption {
  id: number
  name: string
  is_builtin?: boolean
}

const AI_MODEL_KEY = `gdjl_${config.storageScope}_ai_model`

Page(themed({
  data: {
    mode: 'task' as 'task',

    models: MODEL_CATALOG,
    currentModelId: MODEL_CATALOG[0].id,
    currentModelName: MODEL_CATALOG[0].name,
    currentModelShortName: MODEL_CATALOG[0].shortName,
    modelPickerVisible: false,

    kbs: [] as KbOption[],
    kbIndex: -1,
    useKb: false,

    taskPrompt: '',
    taskTitle: '',
    taskContent: '',
    running: false,
  },

  onLoad() {
    if (!session.getToken()) {
      wx.reLaunch({ url: '/pages/login/index' })
      return
    }
    const savedId = String(wx.getStorageSync(AI_MODEL_KEY) || MODEL_CATALOG[0].id)
    const saved = findModel(savedId)
    this.setData({ currentModelId: saved.id, currentModelName: saved.name, currentModelShortName: saved.shortName })
    this.loadKbs()
  },

  onModelPickerToggle() {
    this.setData({ modelPickerVisible: !this.data.modelPickerVisible })
  },
  onModelPicked(event: WechatMiniprogram.CustomEvent) {
    const id = (event.detail as { id: string }).id
    const model = findModel(id)
    wx.setStorageSync(AI_MODEL_KEY, model.id)
    this.setData({ currentModelId: model.id, currentModelName: model.name, currentModelShortName: model.shortName, modelPickerVisible: false })
  },

  async loadKbs() {
    try {
      const res = await getCached<{ items: KbOption[] }>(
        CACHE_KEYS.KB_LIST,
        () => request<{ items: KbOption[] }>('/kb'),
      )
      this.setData({ kbs: res.items || [] })
    } catch {
      // ignore
    }
  },

  onMode(event: WechatMiniprogram.TouchEvent) {
    this.setData({ mode: event.currentTarget.dataset.mode as 'task' })
  },

  onKbChange(event: WechatMiniprogram.PickerChange) {
    this.setData({ kbIndex: Number(event.detail.value) })
  },
  onUseKb(event: WechatMiniprogram.SwitchChange) {
    this.setData({ useKb: event.detail.value })
  },

  onInput(event: WechatMiniprogram.Input) {
    this.setData({ [event.currentTarget.dataset.field as string]: event.detail.value })
  },

  async runTask() {
    const prompt = this.data.taskPrompt.trim()
    if (!prompt) { wx.showToast({ title: '请输入任务要求', icon: 'none' }); return }
    const kbId = this.data.useKb && this.data.kbIndex >= 0 ? this.data.kbs[this.data.kbIndex].id : undefined
    const model = findModel(this.data.currentModelId).model
    this.setData({ running: true })
    wx.showLoading({ title: '生成中...', mask: true })
    try {
      const res = await request<{ title: string; content: string; note_id?: number }>('/ai/task', {
        method: 'POST',
        data: { prompt, kb_id: kbId, save_note: false, model },
        timeout: 180000,
      })
      wx.hideLoading()
      this.setData({ taskTitle: res.title, taskContent: res.content })
    } catch (error) {
      wx.hideLoading()
      wx.showToast({ title: (error as Error).message, icon: 'none' })
    } finally {
      this.setData({ running: false })
    }
  },

  async saveTaskNote() {
    if (!this.data.taskContent) return
    try {
      await request('/notes', {
        method: 'POST',
        data: { title: this.data.taskTitle || '任务报告', content: this.data.taskContent },
      })
      wx.showToast({ title: '已保存到笔记', icon: 'success' })
    } catch (error) {
      wx.showToast({ title: (error as Error).message, icon: 'none' })
    }
  },
}))
