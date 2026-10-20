import { request } from '../../services/request'
import { session } from '../../services/session'
import { themed } from '../../utils/theme'

interface NoteItem {
  id: number
  title: string
  updated_at?: string
  created_at?: string
}

Page(themed({
  data: {
    notes: [] as NoteItem[],
    loading: false,
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
      const res = await request<{ items: NoteItem[] }>('/notes')
      this.setData({ notes: res.items || [] })
    } catch (error) {
      wx.showToast({ title: (error as Error).message, icon: 'none' })
    } finally {
      this.setData({ loading: false })
    }
  },

  create() {
    wx.navigateTo({ url: '/pages/notes/detail?id=0' })
  },

  open(event: WechatMiniprogram.TouchEvent) {
    wx.navigateTo({ url: `/pages/notes/detail?id=${event.currentTarget.dataset.id}` })
  },

  del(event: WechatMiniprogram.TouchEvent) {
    const id = Number(event.currentTarget.dataset.id)
    wx.showModal({
      title: '删除笔记',
      content: '确定删除该笔记？',
      success: async (res) => {
        if (!res.confirm) return
        try {
          await request(`/notes/${id}`, { method: 'DELETE' })
          wx.showToast({ title: '已删除', icon: 'success' })
          this.load()
        } catch (error) {
          wx.showToast({ title: (error as Error).message, icon: 'none' })
        }
      },
    })
  },
}))
