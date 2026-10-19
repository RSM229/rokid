import { request } from '../../services/request'
import { session } from '../../services/session'
import { themed } from '../../utils/theme'

Page(themed({
  data: {
    noteId: 0,
    isNew: true,
    title: '',
    content: '',
    saving: false,
  },

  onLoad(options: Record<string, string>) {
    if (!session.getToken()) {
      wx.reLaunch({ url: '/pages/login/index' })
      return
    }
    const id = Number(options.id || 0)
    if (id) {
      this.setData({ noteId: id, isNew: false })
      this.load()
    }
  },

  async load() {
    try {
      const note = await request<{ title: string; content: string }>(`/notes/${this.data.noteId}`)
      this.setData({ title: note.title, content: note.content || '' })
    } catch (error) {
      wx.showToast({ title: (error as Error).message, icon: 'none' })
    }
  },

  onTitleInput(event: WechatMiniprogram.Input) { this.setData({ title: event.detail.value }) },
  onContentInput(event: WechatMiniprogram.Input) { this.setData({ content: event.detail.value }) },

  async save() {
    if (this.data.saving) return
    this.setData({ saving: true })
    wx.showLoading({ title: '保存中...', mask: true })
    try {
      if (this.data.isNew) {
        const created = await request<{ id: number }>('/notes', {
          method: 'POST',
          data: { title: this.data.title || '未命名笔记', content: this.data.content },
        })
        this.setData({ noteId: created.id, isNew: false })
      } else {
        await request(`/notes/${this.data.noteId}`, {
          method: 'PUT',
          data: { title: this.data.title || '未命名笔记', content: this.data.content },
        })
      }
      wx.hideLoading()
      wx.showToast({ title: '已保存', icon: 'success' })
    } catch (error) {
      wx.hideLoading()
      wx.showToast({ title: (error as Error).message, icon: 'none' })
    } finally {
      this.setData({ saving: false })
    }
  },

  del() {
    if (this.data.isNew || !this.data.noteId) { wx.navigateBack(); return }
    wx.showModal({
      title: '删除笔记',
      content: '确定删除该笔记？',
      success: async (res) => {
        if (!res.confirm) return
        try {
          await request(`/notes/${this.data.noteId}`, { method: 'DELETE' })
          wx.showToast({ title: '已删除', icon: 'success' })
          setTimeout(() => wx.navigateBack(), 600)
        } catch (error) {
          wx.showToast({ title: (error as Error).message, icon: 'none' })
        }
      },
    })
  },
}))
