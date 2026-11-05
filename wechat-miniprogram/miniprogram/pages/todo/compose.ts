import { request } from '../../services/request'
import { session } from '../../services/session'
import { requireLogin } from '../../utils/guard'
import { roleLabel } from '../../utils/role'
import { themed } from '../../utils/theme'

interface Member {
  id: number
  real_name: string
  role: string
  role_label?: string
  initial?: string
  selected?: boolean
}

interface SignSlot {
  key: string
  label: string
  required: boolean
  completed: boolean
}

interface ObjectMeta {
  label: string
  value: string
}

interface ObjectSummary {
  type_label: string
  title: string
  status: string
  tone: 'neutral' | 'warning' | 'positive' | 'danger'
  meta: ObjectMeta[]
  subtitle: string
}

interface WorkRecordContext {
  id: number
  record_type?: string
  status?: string
  section?: string
  location?: string
  record_date?: string
  inspector?: string
  project_name?: string
}

interface DocumentContext {
  id: number
  title?: string
  doc_type?: string
  doc_number?: string
  status?: string
  updated_at?: string
}

interface TransferContext {
  id: number
  title: string
  description: string
  object_type: 'work_record' | 'document'
  object_id: number
  sender_name?: string
}

function safeText(value: unknown): string {
  return typeof value === 'string' ? value.trim() : ''
}

function safeDate(value: unknown): string {
  const text = safeText(value)
  return text.length >= 10 ? text.slice(0, 10) : text
}

function statusTone(status: string): ObjectSummary['tone'] {
  if (/驳回|拒绝|失败/.test(status)) return 'danger'
  if (/待|审核|审批|处理中/.test(status)) return 'warning'
  if (/通过|完成|归档|签认|有效/.test(status)) return 'positive'
  return 'neutral'
}

function initialOf(name: string): string {
  return name.trim().slice(0, 1) || '?'
}

function modeDescription(mode: string, signoff: boolean, isTransfer: boolean): string {
  if (isTransfer) return '转派后，新负责人会收到待办，原负责人不再承担此事项。'
  if (signoff) return '签认人完成指定签认位后，此事项才会从“待我处理”中关闭。'
  if (mode === 'cc') return '抄送人只查看进展，不承担处理责任，也不会计入待处理红点。'
  return '负责人完成处理后，此事项才会从“待我处理”中关闭。'
}

Page(themed({
  data: {
    objectType: 'work_record' as 'work_record' | 'document',
    objectId: 0,
    transferId: 0,
    isTransfer: false,
    object: null as ObjectSummary | null,
    objectLoading: true,
    objectError: '',
    originalDescription: '',
    currentSenderName: '',
    projectName: '',

    members: [] as Member[],
    membersLoading: true,
    membersError: '',
    selectedMemberId: 0,
    selectedMember: null as Member | null,
    ccMemberIds: [] as number[],
    ccCandidates: [] as Member[],
    ccSummary: '未选择',

    mode: 'action' as 'action' | 'cc',
    signoff: false,
    signSlots: [] as SignSlot[],
    signSlotIndex: -1,
    signSlotsError: '',

    title: '',
    description: '',
    deadline: '',
    validationError: '',
    modeHint: '',
    submitLabel: '发送处理事项',
    canSubmit: false,
    sheetType: '' as '' | 'member' | 'cc',
    submitting: false,

    // Stable for the lifetime of this compose page: a network retry returns the
    // same item instead of creating a duplicate responsibility.
    clientRequestId: '',
  },

  onLoad(options: Record<string, string>) {
    if (!requireLogin('pages/todo/compose', options)) return
    const transferId = Number(options.transfer || 0)
    const objectType = options.type === 'document' ? 'document' : 'work_record'
    const objectId = Number(options.id || 0)
    if (!objectId && !transferId) {
      wx.navigateBack()
      return
    }
    const clientRequestId = `todo-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`
    this.setData({
      objectType,
      objectId,
      transferId,
      isTransfer: Boolean(transferId),
      clientRequestId,
      projectName: session.getUser()?.project_name || '',
    })
    void this.initialize()
  },

  async initialize() {
    await Promise.all([this.loadMembers(), this.loadContext()])
  },

  async loadMembers() {
    this.setData({ membersLoading: true, membersError: '' })
    try {
      const currentUserId = session.getUser()?.user_id
      const rows = await request<Member[]>('/collaboration/members')
      const members = (rows || [])
        .filter((member) => member.id !== currentUserId)
        .map((member) => ({
          ...member,
          role_label: roleLabel(member.role),
          initial: initialOf(member.real_name),
        }))
      this.setData({ members, membersLoading: false }, () => this.refreshDerived())
    } catch (error) {
      this.setData({
        members: [],
        membersLoading: false,
        membersError: (error as Error).message || '项目成员加载失败',
      }, () => this.refreshDerived())
    }
  },

  retryMembers() {
    void this.loadMembers()
  },

  async loadContext() {
    this.setData({
      objectLoading: true,
      objectError: '',
      object: null,
      signSlots: [],
      signSlotsError: '',
      validationError: '',
    })
    try {
      let objectType = this.data.objectType
      let objectId = this.data.objectId
      if (this.data.isTransfer) {
        const item = await request<TransferContext>(`/collaboration/${this.data.transferId}`)
        objectType = item.object_type
        objectId = item.object_id
        this.setData({
          objectType,
          objectId,
          currentSenderName: item.sender_name || '',
          originalDescription: item.description || '',
          mode: 'action',
        })
      }

      const object = await this.fetchObjectSummary(objectType, objectId)
      let signSlots: SignSlot[] = []
      let signSlotsError = ''
      if (objectType === 'document' && !this.data.isTransfer) {
        try {
          signSlots = await this.fetchSignSlots(objectId)
        } catch {
          signSlotsError = '签认项暂时无法读取，仍可发起处理或抄送。'
        }
      }
      this.setData({
        object,
        objectLoading: false,
        signSlots,
        signSlotsError,
        signoff: this.data.signoff && signSlots.length > 0,
        signSlotIndex: signSlots.length ? this.data.signSlotIndex : -1,
        title: this.data.title.trim() || object.title,
      }, () => this.refreshDerived())
    } catch (error) {
      this.setData({
        object: null,
        objectLoading: false,
        objectError: (error as Error).message || '关联事项加载失败',
      }, () => this.refreshDerived())
    }
  },

  retryContext() {
    void this.loadContext()
  },

  async fetchObjectSummary(objectType: 'work_record' | 'document', objectId: number): Promise<ObjectSummary> {
    if (objectType === 'document') {
      const doc = await request<DocumentContext>(`/document/${objectId}`)
      const status = safeText(doc.status)
      const meta: ObjectMeta[] = []
      if (safeText(doc.doc_type)) meta.push({ label: '类型', value: safeText(doc.doc_type) })
      if (safeText(doc.doc_number)) meta.push({ label: '编号', value: safeText(doc.doc_number) })
      if (safeDate(doc.updated_at)) meta.push({ label: '更新', value: safeDate(doc.updated_at) })
      return {
        type_label: '工作文书',
        title: safeText(doc.title) || safeText(doc.doc_type) || '未命名文书',
        status,
        tone: statusTone(status),
        meta,
        subtitle: this.data.projectName,
      }
    }

    const record = await request<WorkRecordContext>(`/work-record/${objectId}`)
    const status = safeText(record.status)
    const recordType = safeText(record.record_type)
    const baseTitle = recordType
      ? (recordType.endsWith('记录') ? recordType : `${recordType}记录`)
      : '工作记录'
    const section = safeText(record.section)
    const meta: ObjectMeta[] = []
    if (safeDate(record.record_date)) meta.push({ label: '日期', value: safeDate(record.record_date) })
    if (safeText(record.location)) meta.push({ label: '地点', value: safeText(record.location) })
    if (safeText(record.inspector)) meta.push({ label: '人员', value: safeText(record.inspector) })
    return {
      type_label: '工作记录',
      title: section ? `${baseTitle} · ${section}` : baseTitle,
      status,
      tone: statusTone(status),
      meta,
      subtitle: safeText(record.project_name) || this.data.projectName,
    }
  },

  async fetchSignSlots(objectId: number): Promise<SignSlot[]> {
    const response = await request<{ slots: SignSlot[] }>(`/documents/${objectId}/signoffs`)
    return (response.slots || []).filter((slot) => slot.required && !slot.completed)
  },

  refreshDerived() {
    const members = this.data.members
    const selectedMember = members.find((member) => member.id === this.data.selectedMemberId) || null
    const validCcIds = this.data.ccMemberIds.filter((id) => id !== this.data.selectedMemberId)
    const ccSet = new Set(validCcIds)
    const ccCandidates = members
      .filter((member) => member.id !== this.data.selectedMemberId)
      .map((member) => ({ ...member, selected: ccSet.has(member.id) }))
    const selectedCcNames = members
      .filter((member) => ccSet.has(member.id))
      .map((member) => member.real_name)
    const ccSummary = selectedCcNames.length === 0
      ? '未选择'
      : selectedCcNames.length <= 2
        ? selectedCcNames.join('、')
        : `${selectedCcNames[0]}等 ${selectedCcNames.length} 人`
    const selectedSlot = this.data.signSlots[this.data.signSlotIndex]
    const hasObject = Boolean(this.data.object) && !this.data.objectLoading && !this.data.objectError
    const requiredFieldsReady = Boolean(selectedMember)
      && (this.data.isTransfer || Boolean(this.data.title.trim()))
      && (!this.data.signoff || Boolean(selectedSlot))
    const canSubmit = hasObject && requiredFieldsReady && !this.data.submitting
    const submitLabel = this.data.isTransfer
      ? '确认转派'
      : this.data.signoff
        ? '发送签认事项'
        : this.data.mode === 'cc'
          ? '发送抄送'
          : '发送处理事项'
    this.setData({
      selectedMember,
      ccMemberIds: validCcIds,
      ccCandidates,
      ccSummary,
      canSubmit,
      submitLabel,
      modeHint: modeDescription(this.data.mode, this.data.signoff, this.data.isTransfer),
    })
  },

  selectMode(event: WechatMiniprogram.TouchEvent) {
    const mode = String(event.currentTarget.dataset.mode || 'action')
    if (mode === 'signoff' && !this.data.signSlots.length) return
    this.setData({
      mode: mode === 'cc' ? 'cc' : 'action',
      signoff: mode === 'signoff',
      validationError: '',
    }, () => this.refreshDerived())
  },

  onSignSlotChange(event: WechatMiniprogram.PickerChange) {
    this.setData({ signSlotIndex: Number(event.detail.value), validationError: '' }, () => this.refreshDerived())
  },

  openMemberSheet() {
    if (this.data.membersLoading) {
      wx.showToast({ title: '正在读取项目成员', icon: 'none' })
      return
    }
    if (this.data.membersError) {
      wx.showToast({ title: '项目成员加载失败', icon: 'none' })
      return
    }
    if (!this.data.members.length) {
      wx.showToast({ title: '当前项目暂无其他成员', icon: 'none' })
      return
    }
    this.setData({ sheetType: 'member' })
  },

  openCcSheet() {
    if (this.data.membersLoading || this.data.membersError || !this.data.members.length) {
      this.openMemberSheet()
      return
    }
    this.setData({ sheetType: 'cc' })
  },

  closeSheet() {
    this.setData({ sheetType: '' })
  },

  chooseSheetMember(event: WechatMiniprogram.TouchEvent) {
    const id = Number(event.currentTarget.dataset.id || 0)
    if (!id) return
    if (this.data.sheetType === 'cc') {
      this.toggleCcMember(id)
      return
    }
    const member = this.data.members.find((row) => row.id === id)
    if (!member) return
    this.setData({
      selectedMemberId: id,
      sheetType: '',
      validationError: '',
    }, () => this.refreshDerived())
  },

  toggleCcMember(id: number) {
    if (id === this.data.selectedMemberId) {
      wx.showToast({ title: '负责人无需重复抄送', icon: 'none' })
      return
    }
    const next = new Set(this.data.ccMemberIds)
    if (next.has(id)) next.delete(id)
    else next.add(id)
    this.setData({ ccMemberIds: Array.from(next) }, () => this.refreshDerived())
  },

  clearCc() {
    this.setData({ ccMemberIds: [] }, () => this.refreshDerived())
  },

  onInput(event: WechatMiniprogram.Input) {
    const key = String(event.currentTarget.dataset.key || '')
    if (key !== 'title' && key !== 'description') return
    this.setData({ [key]: event.detail.value, validationError: '' } as Record<string, string>, () => this.refreshDerived())
  },

  onDeadlineChange(event: WechatMiniprogram.PickerChange) {
    this.setData({ deadline: String(event.detail.value) })
  },

  clearDeadline() {
    this.setData({ deadline: '' })
  },

  noop() {},

  async submit() {
    if (this.data.submitting) return
    const member = this.data.selectedMember
    let error = ''
    if (!this.data.object || this.data.objectError) error = '关联事项尚未加载完成，请稍后重试'
    else if (!member) error = this.data.isTransfer
      ? '请选择转派对象'
      : this.data.mode === 'cc'
        ? '请选择抄送对象'
        : '请选择负责人'
    else if (!this.data.isTransfer && !this.data.title.trim()) error = '请填写事项标题'
    else if (!this.data.isTransfer && this.data.signoff && this.data.signSlotIndex < 0) error = '请选择签认位置'
    if (error) {
      this.setData({ validationError: error })
      return
    }

    const selectedSlot = this.data.signSlots[this.data.signSlotIndex]
    this.setData({ submitting: true, validationError: '' }, () => this.refreshDerived())
    try {
      if (this.data.isTransfer) {
        await request(`/collaboration/${this.data.transferId}/transfer`, {
          method: 'POST',
          data: {
            recipient_id: member!.id,
            cc_recipient_ids: this.data.ccMemberIds.filter((id) => id !== member!.id),
            detail: this.data.description.trim(),
            client_request_id: this.data.clientRequestId,
          },
        })
      } else {
        await request('/collaboration', {
          method: 'POST',
          data: {
            object_type: this.data.objectType,
            object_id: this.data.objectId,
            recipient_id: member!.id,
            mode: this.data.mode,
            title: this.data.title.trim(),
            action_type: this.data.signoff ? 'signoff' : 'process',
            signoff_slot: this.data.signoff ? selectedSlot?.key : undefined,
            cc_recipient_ids: this.data.ccMemberIds.filter((id) => id !== member!.id),
            description: this.data.description.trim(),
            deadline: this.data.mode === 'action' && !this.data.signoff ? this.data.deadline || undefined : undefined,
            client_request_id: this.data.clientRequestId,
          },
        })
      }
      this.setData({ submitting: false })
      wx.showToast({
        title: this.data.isTransfer ? '已转派' : this.data.mode === 'cc' ? '已抄送' : '已发送',
        icon: 'success',
      })
      setTimeout(() => wx.navigateBack(), 550)
    } catch (submitError) {
      this.setData({
        submitting: false,
        validationError: (submitError as Error).message || '发送失败，请稍后重试',
      }, () => this.refreshDerived())
    }
  },
}))
