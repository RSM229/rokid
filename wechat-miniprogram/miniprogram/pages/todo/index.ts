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

Page(themed({
  data: {
    tasks: [] as TaskItem[],
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
    if (this.data.tasks.length) this.load()
  },
  onPullDownRefresh() {
    this.load().finally(() => wx.stopPullDownRefresh())
  },
  async load() {
    this.setData({ loading: true })
    try {
      const query = this.data.statusFilter
        ? `?status=${encodeURIComponent(this.data.statusFilter)}`
        : ''
      const result = await request<{ tasks: TaskItem[] }>(`/task/my-tasks${query}`)
      const tasks = [...result.tasks]
      if (this.data.focusId) {
        tasks.sort((a, b) => Number(b.id === this.data.focusId) - Number(a.id === this.data.focusId))
      }
      this.setData({ tasks })
    } catch (error) {
      wx.showToast({ title: (error as Error).message, icon: 'none' })
    } finally {
      this.setData({ loading: false })
    }
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
