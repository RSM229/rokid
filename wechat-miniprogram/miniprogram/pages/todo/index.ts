import { request } from '../../services/request'
import { requestSubscribeMessage, preloadNoticeTemplates, NOTICE_TEMPLATE_KEYS } from '../../services/notice'
import { requireLogin } from '../../utils/guard'
import { themed } from '../../utils/theme'

interface TaskItem {
  id: number
  title: string
  description?: string
  status: string
  priority: string
  category: string
  assigner_name?: string
  deadline?: string
  location?: string
  section?: string
  completion_remark?: string
  created_at?: string
}

interface CollaborationItem {
  id: number
  title: string
  description: string
  mode: 'action' | 'cc'
  action_type: string
  is_signoff?: boolean
  object_type: 'work_record' | 'document'
  object_id: number
  status: string
  status_label?: string
  mode_label?: string
  action_label?: string
  sender_name: string
  recipient_name?: string
  cc_recipient_names?: string[]
  cc_names_text?: string
  can_cancel?: boolean
  can_transfer?: boolean
  created_at?: string
}

interface CollaborationHistory {
  action: string
  action_label?: string
  actor_name?: string
  detail: string
  created_at: string
}

interface CollaborationDetail extends CollaborationItem {
  object?: Record<string, any>
  object_deleted?: boolean
  history?: CollaborationHistory[]
}

Page(themed({
  data: {
    tasks: [] as TaskItem[],
    items: [] as CollaborationItem[],
    view: 'action',
    views: [
      { key: 'action', label: '待我处理' },
      { key: 'cc', label: '抄送跟踪' },
      { key: 'sent', label: '我发出的' },
      { key: 'tasks', label: '派单任务' },
    ],
    pendingCount: 0,
    detail: null as CollaborationDetail | null,
    loading: false,
    actionId: 0,
    statusFilter: '',
    statuses: ['全部', '待接单', '已接单', '进行中', '已完成', '已拒单'],
    statusIndex: 0,
    focusId: 0,
  },
  onLoad(options: Record<string, string>) {
    if (!requireLogin('pages/todo/index', options)) return
    // 预加载订阅消息模板：用户可能直接进待办页（通知跳转），此时首页的预加载不会执行
    void preloadNoticeTemplates()
    this.setData({ focusId: Number(options.id || wx.getStorageSync('gdjl_focus_task') || 0) })
    wx.removeStorageSync('gdjl_focus_task')
    this.load()
  },
  onShow() {
    const focusId = Number(wx.getStorageSync('gdjl_focus_task') || 0)
    if (focusId) {
      this.setData({ focusId })
      wx.removeStorageSync('gdjl_focus_task')
    }
    this.load()
  },
  onPullDownRefresh() {
    this.load().finally(() => wx.stopPullDownRefresh())
  },
  async load() {
    this.setData({ loading: true })
    try {
      const [collaboration, taskResult] = await Promise.all([
        request<{ items: CollaborationItem[]; tasks: TaskItem[]; pending_count: number }>(`/collaboration/inbox?view=${this.data.view === 'tasks' ? 'action' : this.data.view}`),
        request<{ tasks: TaskItem[] }>(`/task/my-tasks${this.data.view === 'action' ? '?status=待处理' : this.data.statusFilter ? `?status=${encodeURIComponent(this.data.statusFilter)}` : ''}`),
      ])
      const tasks = [...(taskResult.tasks || [])]
      if (this.data.focusId) {
        tasks.sort((a, b) => Number(b.id === this.data.focusId) - Number(a.id === this.data.focusId))
      }
      this.setData({ tasks, items: collaboration.items || [], pendingCount: collaboration.pending_count || 0 })
    } catch (error) {
      wx.showToast({ title: (error as Error).message, icon: 'none' })
    } finally {
      this.setData({ loading: false })
    }
  },
  selectView(event: WechatMiniprogram.TouchEvent) {
    const view = String(event.currentTarget.dataset.view || 'action')
    if (view === this.data.view) return
    this.setData({ view })
    this.load()
  },
  async openDetail(event: WechatMiniprogram.TouchEvent) {
    const id = Number(event.currentTarget.dataset.id)
    try {
      const detail = await request<NonNullable<typeof this.data.detail>>(`/collaboration/${id}`)
      this.setData({ detail })
    } catch (error) {
      wx.showToast({ title: (error as Error).message, icon: 'none' })
    }
  },
  closeDetail() { this.setData({ detail: null }) },
  openTransfer() {
    const detail = this.data.detail
    if (!detail) return
    this.setData({ detail: null })
    wx.navigateTo({ url: `/pages/todo/compose?transfer=${detail.id}` })
  },
  async cancelItem() {
    const detail = this.data.detail
    if (!detail) return
    const confirm = await wx.showModal({ title: '撤回事项', content: '撤回后，处理人将不再承担此事项。' })
    if (!confirm.confirm) return
    try {
      await request(`/collaboration/${detail.id}/cancel`, { method: 'POST', data: {} })
      wx.showToast({ title: '已撤回', icon: 'success' })
      this.setData({ detail: null })
      await this.load()
    } catch (error) { wx.showModal({ title: '撤回失败', content: (error as Error).message, showCancel: false }) }
  },
  openBusiness() {
    const detail = this.data.detail
    if (!detail) return
    this.setData({ detail: null })
    if (detail.object_deleted) {
      wx.showToast({ title: '关联业务对象已删除', icon: 'none' })
      return
    }
    const url = detail.object_type === 'work_record'
      ? `/pages/work/index?id=${detail.object_id}&type=record`
      : `/pages/documents/detail?id=${detail.object_id}`
    wx.navigateTo({ url })
  },
  async completeItem() {
    const detail = this.data.detail
    if (!detail) return
    try {
      if (detail.action_type === 'approve') {
        const base = detail.object_type === 'work_record' ? '/work-record' : '/document'
        await request(`${base}/${detail.object_id}/approve`, { method: 'POST', data: {} })
      } else {
        await request(`/collaboration/${detail.id}/resolve`, { method: 'POST', data: { detail: '已完成' } })
      }
      wx.showToast({ title: '已处理', icon: 'success' })
      this.setData({ detail: null })
      await this.load()
    } catch (error) {
      wx.showModal({ title: '处理失败', content: (error as Error).message, showCancel: false })
    }
  },
  rejectItem() {
    const detail = this.data.detail
    if (!detail || detail.action_type !== 'approve') return
    wx.showModal({
      title: '驳回事项', content: '请填写驳回原因', editable: true, placeholderText: '驳回原因',
      success: async result => {
        if (!result.confirm) return
        const base = detail.object_type === 'work_record' ? '/work-record' : '/document'
        try {
          await request(`${base}/${detail.object_id}/reject`, { method: 'POST', data: detail.object_type === 'work_record' ? { reason: result.content || '小程序端驳回' } : { reason: result.content || '小程序端驳回' } })
          wx.showToast({ title: '已驳回', icon: 'success' })
          this.setData({ detail: null })
          await this.load()
        } catch (error) {
          wx.showModal({ title: '驳回失败', content: (error as Error).message, showCancel: false })
        }
      },
    })
  },
  onFilterChange(event: WechatMiniprogram.PickerChange) {
    const statusIndex = Number(event.detail.value)
    const value = this.data.statuses[statusIndex]
    this.setData({
      statusIndex,
      statusFilter: value === '全部' ? '' : value,
    })
    this.load()
  },
  async action(path: string, taskId: number, data?: WechatMiniprogram.IAnyObject) {
    if (this.data.actionId) return
    this.setData({ actionId: taskId })
    try {
      const result = await request<{ detail?: string }>(`/task/${taskId}${path}`, {
        method: 'POST',
        data,
      })
      wx.showToast({ title: result.detail || '操作成功', icon: 'success' })
      await this.load()
    } catch (error) {
      wx.showModal({ title: '操作失败', content: (error as Error).message, showCancel: false })
    } finally {
      this.setData({ actionId: 0 })
    }
  },
  accept(event: WechatMiniprogram.TouchEvent) {
    const taskId = Number(event.currentTarget.dataset.id)
    // 申请订阅消息授权（同步调用，符合微信交互规范）
    requestSubscribeMessage([
      NOTICE_TEMPLATE_KEYS.TASK_URGED,
      NOTICE_TEMPLATE_KEYS.DOCUMENT_APPROVED,
    ])
    this.action('/respond', taskId, { action: 'accept' })
  },
  reject(event: WechatMiniprogram.TouchEvent) {
    const taskId = Number(event.currentTarget.dataset.id)
    wx.showModal({
      title: '拒绝派单',
      content: '请填写拒绝原因',
      editable: true,
      placeholderText: '拒绝原因',
      success: (result) => {
        if (result.confirm) {
          this.action('/respond', taskId, {
            action: 'reject',
            reject_reason: result.content || '小程序端拒绝',
          })
        }
      },
    })
  },
  start(event: WechatMiniprogram.TouchEvent) {
    this.action('/start', Number(event.currentTarget.dataset.id))
  },
  complete(event: WechatMiniprogram.TouchEvent) {
    const taskId = Number(event.currentTarget.dataset.id)
    wx.showModal({
      title: '完成派单',
      content: '填写完成情况或回执',
      editable: true,
      placeholderText: '完成情况',
      success: (result) => {
        if (result.confirm) {
          this.action('/complete', taskId, {
            completion_remark: result.content || '已在小程序端完成',
          })
        }
      },
    })
  },
  capture(event: WechatMiniprogram.TouchEvent) {
    wx.setStorageSync('gdjl_capture_context', {
      businessType: 'task',
      businessId: Number(event.currentTarget.dataset.id),
    })
    wx.navigateTo({ url: '/pages/capture/index' })
  },
  onShareAppMessage(event: WechatMiniprogram.Page.IShareAppMessageOption) {
    const taskId = Number(event.target?.dataset.id || 0)
    const task = this.data.tasks.find((item) => item.id === taskId)
    return {
      title: task ? `监理派单：${task.title}` : '监理派单',
      path: task ? `/pages/todo/index?id=${task.id}` : '/pages/todo/index',
    }
  },
}))
