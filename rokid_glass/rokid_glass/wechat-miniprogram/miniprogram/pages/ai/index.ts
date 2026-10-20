import { request } from '../../services/request'
import { session } from '../../services/session'
import { absoluteMediaUrl } from '../../config'
import { getCached, CACHE_KEYS } from '../../services/cache'
import { themed } from '../../utils/theme'

interface KbOption {
  id: number
  name: string
  is_builtin?: boolean
}

Page(themed({
  data: {
    mode: 'task' as 'task' | 'podcast' | 'image',

    kbs: [] as KbOption[],
    kbIndex: -1,
    useKb: false,

    taskPrompt: '',
    taskTitle: '',
    taskContent: '',
    running: false,

    podcastText: '',
    podcastScript: '',
    podcastAudio: '',
    podcasting: false,

    imagePrompt: '',
    imageUrl: '',
    imaging: false,
  },

  audioCtx: null as WechatMiniprogram.InnerAudioContext | null,

  onLoad() {
    if (!session.getToken()) {
      wx.reLaunch({ url: '/pages/login/index' })
      return
    }
    this.audioCtx = wx.createInnerAudioContext()
    this.loadKbs()
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
    this.setData({ mode: event.currentTarget.dataset.mode as 'task' | 'podcast' | 'image' })
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
    this.setData({ running: true })
    wx.showLoading({ title: '生成中...', mask: true })
    try {
      const res = await request<{ title: string; content: string; note_id?: number }>('/ai/task', {
        method: 'POST',
        data: { prompt, kb_id: kbId, save_note: false },
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

  async runPodcast() {
    const text = this.data.podcastText.trim()
    if (!text) { wx.showToast({ title: '请输入播客素材', icon: 'none' }); return }
    this.setData({ podcasting: true, podcastScript: '', podcastAudio: '' })
    wx.showLoading({ title: '生成中...', mask: true })
    try {
      const res = await request<{ script: string; audio_url: string }>('/ai/podcast', {
        method: 'POST',
        data: { text },
        timeout: 180000,
      })
      wx.hideLoading()
      this.setData({ podcastScript: res.script, podcastAudio: absoluteMediaUrl(res.audio_url) })
    } catch (error) {
      wx.hideLoading()
      wx.showToast({ title: (error as Error).message, icon: 'none' })
    } finally {
      this.setData({ podcasting: false })
    }
  },

  playPodcast() {
    if (!this.data.podcastAudio || !this.audioCtx) return
    this.audioCtx.src = this.data.podcastAudio
    this.audioCtx.play()
  },

  async runImage() {
    const prompt = this.data.imagePrompt.trim()
    if (!prompt) { wx.showToast({ title: '请输入画面描述', icon: 'none' }); return }
    this.setData({ imaging: true, imageUrl: '' })
    wx.showLoading({ title: '绘制中...', mask: true })
    try {
      const res = await request<{ image_url: string }>('/ai/image', {
        method: 'POST',
        data: { prompt },
        timeout: 180000,
      })
      wx.hideLoading()
      this.setData({ imageUrl: absoluteMediaUrl(res.image_url) })
    } catch (error) {
      wx.hideLoading()
      wx.showToast({ title: (error as Error).message, icon: 'none' })
    } finally {
      this.setData({ imaging: false })
    }
  },
}))
