import { addEvent, assignEvidence, candidateDates, confirmPlan, createExample, editEvent, editRequest, FORM_TYPES, mergeEvents, releaseEvidence, removeEvent, restorePlan, reviewIssues, SCENARIOS, splitEvidence, validatePlan, type ReviewPlan } from '../../services/work-review'
const KEY = 'work_review_prototype_v1'
function pageView(plan: ReviewPlan, selectedIds: string[] = []) {
  return {
    plan, scenarioLabel: SCENARIOS[plan.scenario], issues: reviewIssues(plan), confirmed: plan.confirmedRevision === plan.revision,
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
  data: { ...pageView(createExample()), scenarios: SCENARIOS, selectedIds: [] as string[], activeId: 'event-1', formEditorId: '', showPreview: false },
  onLoad() { const plan = restorePlan(wx.getStorageSync(KEY)); if (plan) this.setData({ ...pageView(plan), activeId: plan.events[0]?.id || '' }) },
  save(plan: ReviewPlan) {
    validatePlan(plan)
    const selectedIds = this.data.selectedIds.filter(id => plan.events.some(event => event.id === id))
    this.setData({ ...pageView(plan, selectedIds), selectedIds, showPreview: false })
    try { wx.setStorageSync(KEY, plan) } catch { wx.showToast({ title: '本地保存失败，当前编辑仍保留', icon: 'none' }) }
  },
  chooseScenario(e: WechatMiniprogram.PickerChange) {
    const scenario = Number(e.detail.value); if (scenario === this.data.plan.scenario) return
    wx.showModal({ title: '切换示例场景', content: '将替换当前本地草稿。', success: result => {
      if (!result.confirm) return
      this.setData({ selectedIds: [], formEditorId: '', activeId: 'event-1' }); this.save(createExample(scenario))
    } })
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
  confirm() { try { this.save(confirmPlan(this.data.plan)); this.setData({ showPreview: true }) } catch (error) { wx.showModal({ title: '分组信息待补充', content: (error as Error).message, showCancel: false }) } },
  closePreview() { this.setData({ showPreview: false }) },
  reset() { wx.showModal({ title: '重置当前示例', content: '将清除当前示例的编辑。', success: result => { if (result.confirm) { this.setData({ selectedIds: [], activeId: 'event-1', formEditorId: '' }); this.save(createExample(this.data.plan.scenario)) } } }) },
  previewImage(e: WechatMiniprogram.TouchEvent) {
    const plan = this.data.plan as ReviewPlan, photo = plan.evidence.find(item => item.id === e.currentTarget.dataset.id)
    if (photo) wx.previewImage({ current: photo.image, urls: plan.evidence.map(item => item.image) })
  },
})
