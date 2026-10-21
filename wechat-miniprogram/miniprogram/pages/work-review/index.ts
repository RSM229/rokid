import { addEvent, assignEvidence, candidateDates, confirmPlan, editEvent, editRequest, executeReviewPlan, fetchReviewPlan, FORM_TYPES, mergeEvents, releaseEvidence, removeEvent, reviewIssues, saveReviewPlan, confirmReviewPlan, splitEvidence, validatePlan, type ReviewPlan } from '../../services/work-review'
function emptyPlan(): ReviewPlan {
  return { schemaVersion: 1, scenario: 0, revision: 1, requestText: '', evidence: [], events: [], unassignedIds: [], confirmedRevision: null }
}
function pageView(plan: ReviewPlan, selectedIds: string[] = []) {
  return {
    plan, issues: reviewIssues(plan), confirmed: plan.confirmedRevision === plan.revision,
    executed: plan.status === 'executed' && plan.executedRevision === plan.revision,
    executionResult: plan.executionResult || { plan_id: 0, revision: plan.revision, created_count: 0, records: [] },
    canMerge: selectedIds.length >= 2,
    events: plan.events.map((event, index) => {
      const dates = candidateDates(plan, event)
      return { ...event, number: index + 1, selected: selectedIds.includes(event.id), formText: event.forms.length ? event.forms.join('、') : '表式待确定', dateClue: dates.length ? dates.join(' / ') : '无可确认的资料日期', dateConflict: dates.length > 1, suggestedDate: dates.length === 1 ? dates[0] : '', hasSuggestedDate: dates.length === 1 && !event.dateConfirmed, formOptions: FORM_TYPES.map(value => ({ value, checked: event.forms.includes(value) })), photos: plan.evidence.filter(photo => event.evidenceIds.includes(photo.id)) }
    }),
    unassigned: plan.evidence.filter(photo => plan.unassignedIds.includes(photo.id)),
    records: plan.events.flatMap((event, index) => event.forms.map(form => ({ id: event.id + '-' + form, eventNumber: index + 1, form, title: event.title, date: event.dateConfirmed ? event.date : '待确认', location: event.location || '待补充', content: event.content || '待补充现场工作情况', photoCount: event.evidenceIds.length }))),
  }
}
Page({
  saveQueue: Promise.resolve(),
  serverRevision: 0,
  data: { ...pageView(emptyPlan()), hasServerPlan: false, loadingPlan: true, selectedIds: [] as string[], activeId: '', formEditorId: '', showPreview: false, executing: false },
  async onLoad(options: Record<string, string>) {
    const planId = Number(options.planId || 0)
    if (!planId) { this.setData({ ...pageView(emptyPlan()), loadingPlan: false, hasServerPlan: false }); return }
    try { const plan = await fetchReviewPlan(planId); this.serverRevision = plan.serverRevision || plan.revision; this.setData({ ...pageView(plan), hasServerPlan: true, loadingPlan: false, activeId: plan.events[0]?.id || '' }) }
    catch (error) { this.setData({ loadingPlan: false }); wx.showModal({ title: '整理计划加载失败', content: (error as Error).message, showCancel: false }) }
  },
  async save(plan: ReviewPlan) {
    if (this.data.confirmed || this.data.executed) {
      wx.showToast({ title: '当前版本已确认，不能继续修改', icon: 'none' })
      return
    }
    validatePlan(plan)
    const selectedIds = this.data.selectedIds.filter(id => plan.events.some(event => event.id === id))
    this.setData({ ...pageView(plan, selectedIds), selectedIds, showPreview: false })
    if (!plan.serverId || !this.data.hasServerPlan) return
    const snapshot = plan
    this.saveQueue = this.saveQueue.then(async () => {
      if (!snapshot.serverId || snapshot.revision <= this.serverRevision) return
      try {
        const saved = await saveReviewPlan(snapshot, this.serverRevision)
        this.serverRevision = saved.serverRevision || saved.revision
        const latest = this.data.plan as ReviewPlan
        if (latest.revision === snapshot.revision) this.setData({ ...pageView(saved, this.data.selectedIds as string[]), hasServerPlan: true })
        else this.setData({ plan: { ...latest, revision: saved.revision + Math.max(0, latest.revision - snapshot.revision), serverRevision: saved.serverRevision } })
      } catch (error) { wx.showToast({ title: `保存失败：${(error as Error).message}`, icon: 'none' }) }
    })
    await this.saveQueue
  },
  updateRequest(e: WechatMiniprogram.Input) { if (e.detail.value !== this.data.plan.requestText) this.save(editRequest(this.data.plan, e.detail.value)) },
  updateText(e: WechatMiniprogram.Input) {
    const { id, field } = e.currentTarget.dataset as { id: string; field: string }
    if (['title', 'location', 'content'].includes(field) && this.data.plan.events.find(event => event.id === id)?.[field as 'title'] !== e.detail.value) this.save(editEvent(this.data.plan, id, { [field]: e.detail.value }))
  },
  updateDate(e: WechatMiniprogram.PickerChange) { this.save(editEvent(this.data.plan, String(e.currentTarget.dataset.id), { date: String(e.detail.value), dateConfirmed: true })) },
  adoptDate(e: WechatMiniprogram.TouchEvent) { this.save(editEvent(this.data.plan, String(e.currentTarget.dataset.id), { date: String(e.currentTarget.dataset.date), dateConfirmed: true })) },
  editForms(e: WechatMiniprogram.TouchEvent) { const id = String(e.currentTarget.dataset.id); this.setData({ formEditorId: this.data.formEditorId === id ? '' : id }) },
  changeForms(e: WechatMiniprogram.CheckboxGroupChange) { this.save(editEvent(this.data.plan, String(e.currentTarget.dataset.id), { forms: e.detail.value })) },
  toggleEvent(e: WechatMiniprogram.TouchEvent) {
    const id = String(e.currentTarget.dataset.id), selected = this.data.selectedIds as string[]
    const selectedIds = selected.includes(id) ? selected.filter(value => value !== id) : [...selected, id]
    this.setData({ ...pageView(this.data.plan, selectedIds), selectedIds })
  },
  expand(e: WechatMiniprogram.TouchEvent) { const id = String(e.currentTarget.dataset.id); this.setData({ activeId: this.data.activeId === id ? '' : id }) },
  merge() {
    if ((this.data.selectedIds as string[]).length < 2) { wx.showToast({ title: '先选择至少两个事项', icon: 'none' }); return }
    try { const id = 'event-' + Date.now(); const plan = mergeEvents(this.data.plan, this.data.selectedIds as string[], id); this.setData({ selectedIds: [], activeId: id, formEditorId: '' }); this.save(plan) }
    catch (error) { wx.showToast({ title: (error as Error).message, icon: 'none' }) }
  },
  split(e: WechatMiniprogram.TouchEvent) {
    try { const id = 'event-' + Date.now(); this.save(splitEvidence(this.data.plan, String(e.currentTarget.dataset.id), id)); this.setData({ activeId: id, formEditorId: '' }) }
    catch (error) { wx.showToast({ title: (error as Error).message, icon: 'none' }) }
  },
  release(e: WechatMiniprogram.TouchEvent) { this.save(releaseEvidence(this.data.plan, String(e.currentTarget.dataset.id))) },
  assign(e: WechatMiniprogram.TouchEvent) {
    const { evidence, event } = e.currentTarget.dataset as { evidence: string; event: string }
    this.save(assignEvidence(this.data.plan, evidence, event))
  },
  add() { const id = 'event-' + Date.now(); this.save(addEvent(this.data.plan, id)); this.setData({ activeId: id, formEditorId: '' }) },
  remove(e: WechatMiniprogram.TouchEvent) {
    const id = String(e.currentTarget.dataset.id)
    wx.showModal({ title: '移除事项', content: '关联图片将放回待分配区。', success: result => { if (result.confirm) this.save(removeEvent(this.data.plan, id)) } })
  },
  preview() { this.setData({ showPreview: true }) },
  async confirm() {
    try {
      await this.saveQueue
      const candidate = confirmPlan(this.data.plan)
      if (!candidate.serverId) throw new Error('当前不是服务端整理计划')
      if (candidate.serverRevision !== this.serverRevision) throw new Error('最新修改尚未保存，请稍后重试')
      const saved = await confirmReviewPlan(candidate, this.serverRevision)
      this.setData({ ...pageView(saved), showPreview: true, hasServerPlan: true })
      wx.showToast({ title: '整理计划已确认', icon: 'success' })
    } catch (error) { wx.showModal({ title: '分组信息待补充', content: (error as Error).message, showCancel: false }) }
  },
  async execute() {
    if (!this.data.confirmed || this.data.executed) return
    try {
      await this.saveQueue
      const candidate = this.data.plan as ReviewPlan
      if (!candidate.serverId || candidate.serverRevision !== this.serverRevision) throw new Error('最新修改尚未保存，请稍后重试')
      this.setData({ executing: true })
      const saved = await executeReviewPlan(candidate, this.serverRevision)
      this.setData({ ...pageView(saved), showPreview: true, hasServerPlan: true, executing: false })
      wx.showToast({ title: `已创建 ${saved.executionResult?.created_count || 0} 份记录`, icon: 'success' })
    } catch (error) {
      this.setData({ executing: false })
      wx.showModal({ title: '执行建档失败', content: (error as Error).message, showCancel: false })
    }
  },
  closePreview() { this.setData({ showPreview: false }) },
  reset() { wx.showToast({ title: '真实计划不支持重置为示例', icon: 'none' }) },
  previewImage(e: WechatMiniprogram.TouchEvent) {
    const plan = this.data.plan as ReviewPlan, photo = plan.evidence.find(item => item.id === e.currentTarget.dataset.id)
    if (photo) wx.previewImage({ current: photo.image, urls: plan.evidence.map(item => item.image) })
  },
})
