import { request } from '../../services/request'
import { session } from '../../services/session'
import { themed } from '../../utils/theme'

interface MeetingItem {
  id: number
  title: string
  meeting_type: string
  meeting_date: string
  location: string
  source: string
  topic_total: number
  topic_open: number
  updated_at?: string
}

interface PendingTopic {
  meeting_id: number
  meeting_title: string
  topic: string
  owner: string
  deadline: string
  status: string
  statusClass?: string
  overdue?: boolean
}

const TYPE_OPTIONS = ['全部类型', '例会', '专题会', '协调会', '交底会', '其他']

/** 状态 → ASCII 样式类（WXSS 编译器不接受非 ASCII 选择器） */
function statusClass(status: string): string {
  if (status === '进行中') return 's-doing'
  if (status === '已完成') return 's-done'
  return 's-todo'
}

Page(themed({
  data: {
    meetings: [] as MeetingItem[],
    pendingTopics: [] as PendingTopic[],
    typeOptions: TYPE_OPTIONS,
    typeIndex: 0,
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
      const type = TYPE_OPTIONS[this.data.typeIndex]
      const qs = this.data.typeIndex > 0 ? `?meeting_type=${encodeURIComponent(type)}` : ''
      const res = await request<{ items: MeetingItem[] }>(`/meeting${qs}`)
      this.setData({ meetings: Array.isArray(res.items) ? res.items : [] })
      this.loadPending()
    } catch (error) {
      wx.showToast({ title: (error as Error).message, icon: 'none' })
    } finally {
      this.setData({ loading: false })
    }
  },

  async loadPending() {
    try {
      const today = new Date()
      const ymd = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, '0')}-${String(today.getDate()).padStart(2, '0')}`
      const res = await request<{ items: PendingTopic[] }>('/meeting/topics/pending')
      const items = (Array.isArray(res.items) ? res.items : []).map((t) => ({
        ...t,
        statusClass: statusClass(t.status),
        overdue: !!t.deadline && t.deadline < ymd,
      }))
      this.setData({ pendingTopics: items })
    } catch {
      // 议题跟踪加载失败不阻塞列表
    }
  },

  onTypeChange(event: WechatMiniprogram.PickerChange) {
    this.setData({ typeIndex: Number(event.detail.value) })
    this.load()
  },

  create() {
    wx.navigateTo({ url: '/pages/meeting/detail?id=0' })
  },

  open(event: WechatMiniprogram.TouchEvent) {
    wx.navigateTo({ url: `/pages/meeting/detail?id=${event.currentTarget.dataset.id}` })
  },

  del(event: WechatMiniprogram.TouchEvent) {
    const id = Number(event.currentTarget.dataset.id)
    wx.showModal({
      title: '删除纪要',
      content: '确定删除该会议纪要？',
      success: async (res) => {
        if (!res.confirm) return
        try {
          await request(`/meeting/${id}`, { method: 'DELETE' })
          wx.showToast({ title: '已删除', icon: 'success' })
          this.load()
        } catch (error) {
          wx.showToast({ title: (error as Error).message, icon: 'none' })
        }
      },
    })
  },
}))
