import { request } from '../../services/request'
import { session } from '../../services/session'
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
  can_resolve?: boolean
  can_remind?: boolean
  remind_available_at?: string
  viewer_role?: 'sender' | 'recipient' | 'follower' | 'observer'
  cc_recipients?: Array<{ id: number; real_name: string; read_at?: string }>
  deadline?: string
  deadline_state?: 'none' | 'normal' | 'due_today' | 'overdue' | 'closed'
  deadline_label?: string
  days_until_due?: number | null
  source?: string
  resolution?: string
  completed_by_name?: string
  last_reminded_at?: string
  last_reminded_at_text?: string
  last_reminder_note?: string
  remind_available_text?: string
  is_read?: boolean
  created_at?: string
  created_at_text?: string
  completed_at?: string
  completed_at_text?: string
}

interface InboxCounts {
  action: number
  collaboration_action: number
  task_pending: number
  cc: number
  sent: number
  done: number
  tasks: number
}

interface InboxResponse {
  items: CollaborationItem[]
  tasks: TaskItem[]
  pending_count: number
  counts: InboxCounts
  has_more: boolean
  next_offset: number | null
}

type CompletionAction = 'approve' | 'reject' | 'process' | 'signoff'

interface CompletionResult {
  tone: 'success' | 'danger'
  eyebrow: string
  title: string
  description: string
  note: string
  itemTitle: string
  senderName: string
  statusLabel: string
  objectType: 'work_record' | 'document'
  objectId: number
  objectDeleted: boolean
}

interface CollaborationHistory {
  action: string
  action_label?: string
  actor_name?: string
  detail: string
  created_at: string
  created_at_text?: string
}

interface CollaborationDetail extends CollaborationItem {
  object?: Record<string, any>
  object_deleted?: boolean
  history?: CollaborationHistory[]
}

function displayTime(value?: string): string {
  const text = (value || '').trim()
  return text ? text.replace('T', ' ').slice(0, 16) : ''
}

const COLLAB_STATUS_VALUES: Record<string, string> = {
  全部: '',
  待处理: 'pending',
  进行中: 'pending',
  跟踪中: 'tracking',
  已完成: 'completed',
  已撤回: 'canceled',
  已转派: 'transferred',
}

function collabStatusOptions(view: string): string[] {
  if (view === 'done') return ['全部', '已完成', '已撤回', '已转派']
  if (view === 'cc') return ['全部', '跟踪中', '进行中', '已完成', '已撤回', '已转派']
  return ['全部', '待处理', '跟踪中', '已完成', '已撤回', '已转派']
}

Page(themed({
  data: {
    tasks: [] as TaskItem[],
    items: [] as CollaborationItem[],
    view: 'action',
    views: [
      { key: 'action', label: '待我处理', count: 0 },
      { key: 'cc', label: '抄送跟踪', count: 0 },
      { key: 'sent', label: '我发出的', count: 0 },
      { key: 'done', label: '处理记录', count: 0 },
      { key: 'tasks', label: '派单任务', count: 0 },
    ],
    counts: {
      action: 0,
      collaboration_action: 0,
      task_pending: 0,
      cc: 0,
      sent: 0,
      done: 0,
      tasks: 0,
    } as InboxCounts,
    pendingCount: 0,
    hasMore: false,
    loadingMore: false,
    collabStatuses: ['全部', '待处理', '已完成', '已撤回', '已转派'],
    collabStatusIndex: 0,
    collabStatusFilter: '',
    focusItemId: 0,
    remindingId: 0,
    detail: null as CollaborationDetail | null,
    completion: null as CompletionResult | null,
    pendingCheckId: 0,
    loading: false,
    actionId: 0,
    collabActionId: 0,
    statusFilter: '',
    statuses: ['全部', '待接单', '已接单', '进行中', '已完成', '已拒单'],
    statusIndex: 0,
    focusId: 0,
  },
  onLoad(options: Record<string, string>) {
    if (!requireLogin('pages/todo/index', options)) return
    // 预加载订阅消息模板：用户可能直接进待办页（通知跳转），此时首页的预加载不会执行
    void preloadNoticeTemplates()
    const focusItemId = Number(options.item_id || 0)
    this.setData({
      focusId: Number(options.id || wx.getStorageSync('gdjl_focus_task') || 0),
      focusItemId,
    })
    wx.removeStorageSync('gdjl_focus_task')
    if (focusItemId) void this.openDetailById(focusItemId)
    this.load()
  },
  onShow() {
    const focusId = Number(wx.getStorageSync('gdjl_focus_task') || 0)
    if (focusId) {
      this.setData({ focusId })
      wx.removeStorageSync('gdjl_focus_task')
    }
    const checkId = this.data.pendingCheckId
    if (checkId) {
      this.setData({ pendingCheckId: 0 })
      void this.checkCompletedAfterBusiness(checkId)
    }
    this.load()
  },
  onPullDownRefresh() {
    this.load().finally(() => wx.stopPullDownRefresh())
  },
  async load(reset = true) {
    if (!reset) {
      if (this.data.loadingMore || !this.data.hasMore) return
      this.setData({ loadingMore: true })
    } else {
      this.setData({ loading: true, items: [], hasMore: false })
    }
    const offset = reset ? 0 : this.data.items.length
    const apiView = this.data.view === 'tasks' ? 'action' : this.data.view
    const statusQuery = this.data.collabStatusFilter
      ? `&status=${encodeURIComponent(this.data.collabStatusFilter)}`
      : ''
    const taskQuery = this.data.view === 'tasks'
      ? (this.data.statusFilter ? `?status=${encodeURIComponent(this.data.statusFilter)}` : '')
      : this.data.view === 'action' ? '?status=待处理' : ''
    const loadsTasks = this.data.view === 'action' || this.data.view === 'tasks'
    try {
      const [collaboration, taskResult] = await Promise.all([
        request<InboxResponse>(`/collaboration/inbox?view=${apiView}&limit=30&offset=${offset}${statusQuery}`),
        loadsTasks
          ? request<{ tasks: TaskItem[] }>(`/task/my-tasks${taskQuery}`)
          : Promise.resolve({ tasks: [] as TaskItem[] }),
      ])
      const tasks = [...(taskResult.tasks || [])]
      if (this.data.focusId) {
        tasks.sort((a, b) => Number(b.id === this.data.focusId) - Number(a.id === this.data.focusId))
      }
      const pageItems = (collaboration.items || []).map((item) => ({
        ...item,
        created_at_text: displayTime(item.created_at),
        completed_at_text: displayTime(item.completed_at),
        last_reminded_at_text: displayTime(item.last_reminded_at),
      }))
      const items = reset ? pageItems : this.data.items.concat(pageItems)
      const counts = collaboration.counts || {
        ...this.data.counts,
        action: collaboration.pending_count || 0,
        collaboration_action: collaboration.pending_count || 0,
      }
      const views = this.data.views.map((tab) => ({
        ...tab,
        count: counts[tab.key as keyof InboxCounts] || 0,
      }))
      this.setData({
        tasks,
        items,
        counts,
        views,
        pendingCount: counts.action || 0,
        hasMore: Boolean(collaboration.has_more),
        loading: false,
        loadingMore: false,
      })
    } catch (error) {
      this.setData({ loading: false, loadingMore: false })
      wx.showToast({ title: (error as Error).message || '待办加载失败', icon: 'none' })
    }
  },
  loadMore() {
    void this.load(false)
  },
  selectView(event: WechatMiniprogram.TouchEvent) {
    const view = String(event.currentTarget.dataset.view || 'action')
    if (view === this.data.view) return
    this.setData({
      view,
      items: [],
      hasMore: false,
      collabStatuses: collabStatusOptions(view),
      collabStatusIndex: 0,
      collabStatusFilter: '',
    })
    this.load()
  },
  onCollabStatusChange(event: WechatMiniprogram.PickerChange) {
    const index = Number(event.detail.value)
    const label = this.data.collabStatuses[index] || '全部'
    this.setData({
      collabStatusIndex: index,
      collabStatusFilter: COLLAB_STATUS_VALUES[label] || '',
      items: [],
      hasMore: false,
    }, () => this.load())
  },
  async checkCompletedAfterBusiness(id: number) {
    try {
      const detail = await request<CollaborationDetail>(`/collaboration/${id}`)
      if (detail.status !== 'completed') return
      const action: CompletionAction = detail.is_signoff
        ? 'signoff'
        : detail.action_type === 'approve' ? 'approve' : 'process'
      this.setData({ completion: this.buildCompletion(detail, action) })
    } catch {
      // The business page may have deleted the item; keep the inbox usable.
    }
  },

  buildCompletion(
    detail: CollaborationDetail,
    action: CompletionAction,
    note = '',
    reason = '',
  ): CompletionResult {
    const senderName = detail.sender_name || '发起人'
    const objectLabel = detail.object_type === 'work_record' ? '工作记录' : '工作文书'
    let title = '事项已完成'
    let description = `${senderName} 可在“我发出的”中看到完成状态；本次处理已保留在“处理记录”。`
    let statusLabel = '已完成'
    let tone: CompletionResult['tone'] = 'success'
    if (action === 'approve') {
      title = '审批已通过'
      description = `审批结果已生效，${senderName} 可在“我发出的”中查看；本次处理已保留在“处理记录”。`
      statusLabel = '已通过'
    } else if (action === 'reject') {
      title = '已驳回'
      description = `驳回原因已记录，${senderName} 修改后可重新提交；本次处理已保留在“处理记录”。`
      statusLabel = '已驳回'
      tone = 'danger'
    } else if (action === 'signoff') {
      title = '签认已完成'
      description = `签认已写入${objectLabel}，${senderName} 可在“我发出的”中查看；本次处理已保留在“处理记录”。`
      statusLabel = '已签认'
    }
    return {
      tone,
      eyebrow: '处理结果',
      title,
      description,
      note: reason ? `驳回原因：${reason}` : note ? `处理说明：${note}` : '',
      itemTitle: detail.title,
      senderName,
      statusLabel,
      objectType: detail.object_type,
      objectId: detail.object_id,
      objectDeleted: Boolean(detail.object_deleted),
    }
  },

  closeCompletion() {
    this.setData({ completion: null })
  },

  backToInbox() {
    this.setData({ completion: null })
    void this.load()
  },

  viewCompletionBusiness() {
    const completion = this.data.completion
    if (!completion) return
    if (completion.objectDeleted) {
      wx.showToast({ title: '关联业务对象已删除', icon: 'none' })
      return
    }
    this.setData({ completion: null })
    const url = completion.objectType === 'work_record'
      ? `/pages/work/index?id=${completion.objectId}&type=record`
      : `/pages/documents/detail?id=${completion.objectId}`
    wx.navigateTo({ url })
  },

  openDetail(event: WechatMiniprogram.TouchEvent) {
    void this.openDetailById(Number(event.currentTarget.dataset.id))
  },
  async openDetailById(id: number) {
    if (!id) return
    try {
      const detail = await request<NonNullable<typeof this.data.detail>>(`/collaboration/${id}`)
      detail.history = (detail.history || []).map((row) => ({
        ...row,
        created_at_text: displayTime(row.created_at),
      }))
      detail.cc_recipients = detail.cc_recipients || []
      detail.completed_at_text = displayTime(detail.completed_at)
      detail.last_reminded_at_text = displayTime(detail.last_reminded_at)
      detail.remind_available_text = displayTime(detail.remind_available_at).slice(11, 16)
      this.setData({ detail })
    } catch (error) {
      wx.showToast({ title: (error as Error).message, icon: 'none' })
    }
  },
  async remindItem() {
    const detail = this.data.detail
    if (!detail || this.data.remindingId) return
    this.setData({ remindingId: detail.id })
    try {
      const result = await request<{ detail: string; next_available_at?: string }>(
        `/collaboration/${detail.id}/remind`,
        { method: 'POST', data: { note: '请尽快处理' } },
      )
      wx.showToast({ title: result.detail || '已提醒', icon: 'success' })
      await this.openDetailById(detail.id)
    } catch (error) {
      wx.showModal({ title: '催办失败', content: (error as Error).message, showCancel: false })
    } finally {
      this.setData({ remindingId: 0 })
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
    const shouldCheck = detail.mode === 'action' && detail.status === 'pending' && !detail.object_deleted
    this.setData({ detail: null, pendingCheckId: shouldCheck ? detail.id : 0 })
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
    if (!detail || this.data.collabActionId) return
    const isApprove = detail.action_type === 'approve'
    let note = ''
    if (isApprove) {
      const confirm = await wx.showModal({
        title: '确认审批通过',
        content: `「${detail.title}」通过后将进入已审批状态，发起人会收到结果。`,
        confirmText: '通过',
        confirmColor: '#176b62',
      })
      if (!confirm.confirm) return
    } else {
      const result = await wx.showModal({
        title: '完成事项',
        content: '可填写处理说明，发起人会在处理记录中看到。',
        editable: true,
        placeholderText: '处理说明（选填）',
        confirmText: '完成',
        confirmColor: '#176b62',
      })
      if (!result.confirm) return
      note = (result.content || '').trim()
    }
    this.setData({ collabActionId: detail.id })
    try {
      if (isApprove) {
        const base = detail.object_type === 'work_record' ? '/work-record' : '/document'
        await request(`${base}/${detail.object_id}/approve`, { method: 'POST', data: {} })
      } else {
        await request(`/collaboration/${detail.id}/resolve`, {
          method: 'POST',
          data: { detail: note || '已完成' },
        })
      }
      const completion = this.buildCompletion(detail, isApprove ? 'approve' : 'process', note)
      this.setData({ detail: null, completion, collabActionId: 0 })
      await this.load()
    } catch (error) {
      this.setData({ collabActionId: 0 })
      wx.showModal({ title: '处理失败', content: (error as Error).message, showCancel: false })
    }
  },
  rejectItem() {
    const detail = this.data.detail
    if (!detail || detail.action_type !== 'approve' || this.data.collabActionId) return
    wx.showModal({
      title: '驳回事项',
      content: '请填写驳回原因，发起人会看到并修改后重新提交。',
      editable: true,
      placeholderText: '驳回原因（必填）',
      confirmText: '驳回',
      confirmColor: '#b73832',
      success: async result => {
        if (!result.confirm) return
        const reason = (result.content || '').trim()
        if (!reason) {
          wx.showToast({ title: '请填写驳回原因', icon: 'none' })
          return
        }
        this.setData({ collabActionId: detail.id })
        const base = detail.object_type === 'work_record' ? '/work-record' : '/document'
        try {
          await request(`${base}/${detail.object_id}/reject`, { method: 'POST', data: { reason } })
          const completion = this.buildCompletion(detail, 'reject', '', reason)
          this.setData({ detail: null, completion, collabActionId: 0 })
          await this.load()
        } catch (error) {
          this.setData({ collabActionId: 0 })
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
    // 现场采集页已下线：派单取证改为在工作台对话里拍照，走附件识别链路。
    // 把派单上下文写进 storage，供 agent 页拍照后关联任务（后端仍有 project/uploader 双闸）。
    wx.setStorageSync(`gdjl_capture_context_u${session.storageUid()}`, {
      businessType: 'task',
      businessId: Number(event.currentTarget.dataset.id),
    })
    wx.redirectTo({ url: '/pages/agent/index' })
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
