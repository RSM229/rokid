import { request } from '../../services/request'
import { session } from '../../services/session'
import { requireLogin } from '../../utils/guard'
import { themed } from '../../utils/theme'

interface Member { id: number; real_name: string; role: string }
interface SignSlot { key: string; label: string; required: boolean; completed: boolean }

Page(themed({
  data: {
    objectType: 'work_record' as 'work_record' | 'document',
    objectId: 0,
    transferId: 0,
    isTransfer: false,
    objectTitle: '',
    members: [] as Member[],
    memberIndex: -1,
    ccMemberIds: [] as number[],
    mode: 'action' as 'action' | 'cc',
    signoff: false,
    signSlots: [] as SignSlot[],
    signSlotIndex: -1,
    title: '',
    description: '',
    deadline: '',
    // Stable for the lifetime of this compose page: a network retry returns the
    // same item instead of creating a duplicate responsibility.
    clientRequestId: '',
    submitting: false,
  },
  onLoad(options: Record<string, string>) {
    if (!requireLogin('pages/todo/compose', options)) return
    const transferId = Number(options.transfer || 0)
    const objectType = options.type === 'document' ? 'document' : 'work_record'
    const objectId = Number(options.id || 0)
    if (!objectId && !transferId) { wx.navigateBack(); return }
    const title = decodeURIComponent(options.title || '')
    const clientRequestId = transferId
      ? ''
      : `todo-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`
    this.setData({ objectType, objectId, transferId, isTransfer: Boolean(transferId), objectTitle: title, title, clientRequestId })
    void this.loadMembers()
    if (objectType === 'document') void this.loadSignSlots(objectId)
    if (transferId) void this.loadTransfer(transferId)
  },
  async loadMembers() {
    try {
      const currentUserId = session.getUser()?.user_id
      const members = await request<Member[]>('/collaboration/members')
      this.setData({ members: members.filter((member) => member.id !== currentUserId) })
    }
    catch (error) { wx.showToast({ title: (error as Error).message, icon: 'none' }) }
  },
  async loadTransfer(id: number) {
    try {
      const item = await request<{ title: string; description: string; object_type: 'work_record' | 'document'; object_id: number }>(`/collaboration/${id}`)
      this.setData({ objectTitle: item.title, title: item.title, description: item.description, objectType: item.object_type, objectId: item.object_id })
    } catch (error) { wx.showModal({ title: '加载失败', content: (error as Error).message, showCancel: false }) }
  },
  async loadSignSlots(id: number) {
    try {
      const response = await request<{ slots: SignSlot[] }>(`/documents/${id}/signoffs`)
      this.setData({ signSlots: response.slots.filter((slot) => slot.required && !slot.completed) })
    } catch { /* Ordinary processing and CC remain available if signoff metadata is unavailable. */ }
  },
  onMemberChange(event: WechatMiniprogram.PickerChange) { this.setData({ memberIndex: Number(event.detail.value) }) },
  onCCChange(event: WechatMiniprogram.CheckboxGroupChange) {
    this.setData({ ccMemberIds: event.detail.value.map((value) => Number(value)) })
  },
  onModeChange(event: WechatMiniprogram.RadioGroupChange) {
    if (event.detail.value === 'signoff') this.setData({ mode: 'action', signoff: true })
    else this.setData({ mode: event.detail.value as 'action' | 'cc', signoff: false })
  },
  onSignSlotChange(event: WechatMiniprogram.PickerChange) { this.setData({ signSlotIndex: Number(event.detail.value) }) },
  onInput(event: WechatMiniprogram.Input) {
    const key = String(event.currentTarget.dataset.key || '')
    if (key === 'title' || key === 'description' || key === 'deadline') this.setData({ [key]: event.detail.value } as Record<string, string>)
  },
  onDeadlineChange(event: WechatMiniprogram.PickerChange) { this.setData({ deadline: String(event.detail.value) }) },
  async submit() {
    const member = this.data.members[this.data.memberIndex]
    if (!member) { wx.showToast({ title: '请选择项目成员', icon: 'none' }); return }
    if (this.data.signoff && this.data.signSlotIndex < 0) { wx.showToast({ title: '请选择必签位置', icon: 'none' }); return }
    if (!this.data.title.trim()) { wx.showToast({ title: '请填写事项标题', icon: 'none' }); return }
    this.setData({ submitting: true })
    try {
      if (this.data.isTransfer) {
        await request(`/collaboration/${this.data.transferId}/transfer`, { method: 'POST', data: { recipient_id: member.id, detail: this.data.description.trim() } })
      } else {
        await request('/collaboration', { method: 'POST', data: {
          object_type: this.data.objectType, object_id: this.data.objectId,
          recipient_id: member.id, mode: this.data.mode, title: this.data.title.trim(),
          action_type: this.data.signoff ? 'signoff' : 'process',
          signoff_slot: this.data.signoff ? this.data.signSlots[this.data.signSlotIndex]?.key : undefined,
          cc_recipient_ids: this.data.ccMemberIds.filter((id) => id !== member.id),
          description: this.data.description.trim(), deadline: this.data.deadline || undefined,
          client_request_id: this.data.clientRequestId,
        } })
      }
      wx.showToast({ title: this.data.isTransfer ? '已转派' : this.data.mode === 'cc' ? '已抄送' : '已发送', icon: 'success' })
      setTimeout(() => wx.navigateBack(), 350)
    } catch (error) { wx.showModal({ title: '发送失败', content: (error as Error).message, showCancel: false }) }
    finally { this.setData({ submitting: false }) }
  },
}))
