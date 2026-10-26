import { request } from '../../services/request'
import { config, absoluteMediaUrl } from '../../config'
import { getCached, CACHE_KEYS, invalidateCache } from '../../services/cache'
import { requireLogin } from '../../utils/guard'
import { themed } from '../../utils/theme'

// 获取北京时间
function beijingNow(): Date {
  const now = new Date()
  const utc = now.getTime() + now.getTimezoneOffset() * 60000
  return new Date(utc + 8 * 3600000)
}

interface HazardItem {
  id: number
  status: string
  risk_status?: string
  risk_category?: string
  top1_risk?: string
  section?: string
  location?: string
  image_path?: string
  image_url?: string
  evidence?: string[]
  evidence_text?: string
  created_at?: string
  checked?: boolean
}

interface WorkRecordItem {
  id: number
  record_type: string
  status: string
  project_name?: string
  system_project_name?: string
  project_mismatch?: boolean
  section?: string
  location?: string
  work_content?: string
  created_at?: string
  checked?: boolean
}

interface RecordFormOption {
  form_code: string
  name: string
  form_number?: string
  has_template?: boolean
}

interface MediaEvidence {
  id: number
  url: string
  section?: string | null
  location?: string | null
  weather?: string | null
  capture_time?: string | null
  capture_time_source?: string | null
  watermark?: Record<string, string>
}

interface DetailItem {
  generated_doc_id?: number
  id?: number
  record_type?: string
  status?: string
  supervisor?: string
  project_name?: string
  business_project_name?: string
  system_project_name?: string
  watermark_project_name?: string
  project_mismatch?: boolean
  construction_unit?: string
  inspection_time_text?: string
  problem_text?: string
  record_date?: string
  worker_count?: number | null
  inspection_start_time?: string | null
  inspection_end_time?: string | null
  section?: string
  location?: string
  created_at?: string
  weather?: string
  work_content?: string
  inspection_remark?: string
  problems?: string
  treatment?: string
  acceptance_guide_ref?: string
  acceptance_result?: string
  acceptance_basis?: string
  station_project_name?: string
  station_content?: string
  station_conclusion?: string
  station_start_time?: string
  station_end_time?: string
  station_supervision_status?: string
  witness_material_name?: string
  witness_material_spec?: string
  witness_quantity?: string
  witness_unit?: string
  witness_cert_no?: string
  witness_accept_result?: string
  prov_fields?: Record<string, string>
  inspection_photos?: string[]
  witness_photos?: string[]
  top1_risk?: string
  risk_category?: string
  risk_status?: string
  evidence_text?: string
  suggestion?: string
  image_url?: string
  evidence?: string[]
  content_json?: Record<string, any>
  media_evidence?: MediaEvidence[]
}

interface MenuItem {
  key: string
  label: string
  danger?: boolean
}

interface ReportType {
  key: 'day' | 'week' | 'month'
  label: string
  desc: string
  icon: string
}

const TYPE_TABS = [
  { label: '巡视', key: 'inspection' },
  { label: '验收', key: 'acceptance' },
  { label: '旁站', key: 'station' },
  { label: '见证', key: 'witness' },
  { label: '隐患', key: 'hazard' },
]

const TIME_RANGES = [
  { label: '全部', key: 'all' },
  { label: '今天', key: 'today' },
  { label: '本周', key: 'week' },
  { label: '本月', key: 'month' },
]

const REPORT_TYPES: ReportType[] = [
  { key: 'day', label: '日志', desc: '生成当日监理日志', icon: '📋' },
  { key: 'week', label: '周报', desc: '生成本周监理周报', icon: '📊' },
  { key: 'month', label: '月报', desc: '生成本月监理月报', icon: '📈' },
]

const MENU_ITEMS: MenuItem[] = [
  { key: 'detail', label: '查看详情' },
  { key: 'edit', label: '编辑' },
  { key: 'share', label: '分享给好友' },
  { key: 'delete', label: '删除', danger: true },
]

Page(themed({
  data: {
    records: [] as WorkRecordItem[],
    hazards: [] as HazardItem[],
    typeTabs: TYPE_TABS,
    timeRanges: TIME_RANGES,
    selectedType: 'all',
    timeRange: 'all',
    timeRangeLabel: '全部',
    loading: false,
    showTimeSheet: false,
    showTypeDrawer: false,

    // 多选模式（勾选状态落在每条记录的 checked 字段上；
    // 早期版本把状态存 selectedIds 数组，WXML 里 .includes() 不生效且 checkbox
    // 脱离 checkbox-group 的 bindchange 不触发，导致选中后操作栏永远不出现）
    selectMode: false,
    selectedCount: 0,
    allSelected: false,

    // 批量操作
    generatingReport: false,
    generatingDoc: false,
    deletingBatch: false,
    submittingBatch: false,

    // 详情弹窗
    showDetail: false,
    navHeight: 88,
    detail: {} as DetailItem,
    detailType: 'record' as 'record' | 'hazard',
    detailPhotos: [] as string[],
    detailMediaEvidence: [] as MediaEvidence[],
    detailForms: [] as RecordFormOption[],
    selectedFormCode: '',
    loadingForms: false,

    // 记录菜单
    showRecordMenu: false,
    recordMenuItems: MENU_ITEMS,
    currentRecordId: 0,
    currentRecordType: 'record' as 'record' | 'hazard',

    // 报告生成弹窗
    showReportModal: false,
    reportTypes: REPORT_TYPES,
    reportType: 'day' as 'day' | 'week' | 'month',
    reportDate: '',
    useLlm: true,

    // 编辑弹窗
    showEditModal: false,
    editRecordId: 0,
    editForm: {} as Record<string, any>,
    acceptanceResults: ['合格', '不合格', '待整改'],
    savingEdit: false,
  },

  onLoad(options: Record<string, string>) {
    if (!requireLogin('pages/work/index', options)) return
    // 详情弹层 top 必须用 px 且在导航栏之下：80rpx(≈41px) 是固定值，
    // 小于「状态栏+44px」的导航栏总高，弹层标题栏会被导航栏整个盖住（真机实测）
    const win = wx.getWindowInfo()
    this.setData({ navHeight: (win.statusBarHeight || 20) + 44 })
    // 分享卡片进入：/pages/work/index?id=xx&type=record|hazard，直接进入对应详情
    const sharedId = Number(options.id || 0)
    const sharedType = options.type === 'hazard' ? 'hazard' : 'record'
    if (sharedId) {
      this.data.selectedType = sharedType === 'hazard' ? '隐患' : 'all'
    } else if (options?.type) {
      const tab = TYPE_TABS.find((item) => item.key === options.type || item.label === options.type)
      if (tab) {
        this.data.selectedType = tab.label
      } else if (options.type === 'all') {
        this.data.selectedType = 'all'
      }
    }
    // 默认报告日期为今天
    const today = beijingNow()
    const pad = (n: number) => String(n).padStart(2, '0')
    this.data.reportDate = `${today.getFullYear()}-${pad(today.getMonth() + 1)}-${pad(today.getDate())}`
    this.setData({ selectedType: this.data.selectedType, reportDate: this.data.reportDate })
    const ready = this.load()
    if (sharedId) {
      void ready.then(() => {
        this.openDetail({
          currentTarget: { dataset: { id: sharedId, type: sharedType } },
        } as unknown as WechatMiniprogram.TouchEvent)
      })
    }
  },

  onPullDownRefresh() {
    this.load().finally(() => wx.stopPullDownRefresh())
  },

  async load() {
    this.setData({ loading: true })
    try {
      if (this.data.selectedType === '隐患') {
        await this.loadHazards()
        return
      }
      
      const params: string[] = []
      if (this.data.selectedType && this.data.selectedType !== 'all') {
        params.push(`record_type=${encodeURIComponent(this.data.selectedType)}`)
      }
      const range = this.computeRange()
      if (range) {
        params.push(`start_date=${range.start}`)
        params.push(`end_date=${range.end}`)
      }
      const query = params.length ? `?${params.join('&')}` : ''
      const result = await request<{ records: WorkRecordItem[] }>(`/work-record/list${query}`)
      
      if (this.data.selectedType === 'all') {
        const hazards = await getCached<HazardItem[]>(
          CACHE_KEYS.HAZARD_LIST,
          () => request<HazardItem[]>('/hazard/list'),
        )
        const hazardItems = hazards.map((item) => ({
          ...item,
          image_url: absoluteMediaUrl(item.image_path || ''),
          evidence_text: (item.evidence || []).join('；'),
        }))
        this.setData({ records: result.records, hazards: hazardItems })
      } else {
        this.setData({ records: result.records })
      }
    } catch (error) {
      wx.showToast({ title: (error as Error).message, icon: 'none' })
    } finally {
      this.setData({ loading: false })
    }
  },

  async loadHazards() {
    const hazards = await getCached<HazardItem[]>(
      CACHE_KEYS.HAZARD_LIST,
      () => request<HazardItem[]>('/hazard/list'),
    )
    const items = hazards.map((item) => ({
      ...item,
      image_url: absoluteMediaUrl(item.image_path || ''),
      evidence_text: (item.evidence || []).join('；'),
    }))
    this.setData({ records: [], hazards: items })
  },

  // ========== 多选模式 ==========
  toggleSelectMode() {
    this.setData({ selectMode: !this.data.selectMode, selectedCount: 0, allSelected: false })
    // 退出多选时清掉勾选痕迹
    if (!this.data.selectMode) {
      this.clearChecked()
    }
  },

  clearChecked() {
    const records = this.data.records.map((item) => (item.checked ? { ...item, checked: false } : item))
    const hazards = this.data.hazards.map((item) => (item.checked ? { ...item, checked: false } : item))
    this.setData({ records, hazards })
  },

  createRecord() {
    wx.showActionSheet({ itemList: ['巡视记录', '旁站记录', '验收记录', '平行检查', '省统表录入'], success: (res) => {
      if (res.tapIndex === 4) {
        wx.navigateTo({ url: '/pages/record-form/choose' })
        return
      }
      if (res.tapIndex === 3) wx.navigateTo({ url: '/pages/parallel/index' })
      else wx.navigateTo({ url: `/pages/agent/index?createRecord=${['inspection', 'station', 'acceptance'][res.tapIndex]}` })
    } })
  },

  /** 点击卡片/复选框切换勾选（不依赖 checkbox 的 change 事件） */
  toggleSelectById(type: string, id: number) {
    if (type === 'hazard') {
      const hazards = this.data.hazards.map((item) =>
        item.id === id ? { ...item, checked: !item.checked } : item,
      )
      this.setData({ hazards })
    } else {
      const records = this.data.records.map((item) =>
        item.id === id ? { ...item, checked: !item.checked } : item,
      )
      this.setData({ records })
    }
    this.syncSelection()
  },

  syncSelection() {
    const all = [...this.data.records, ...this.data.hazards]
    const selectedCount = all.filter((item) => item.checked).length
    const allSelected = all.length > 0 && selectedCount === all.length
    this.setData({ selectedCount, allSelected })
  },

  toggleSelectAll() {
    const target = !this.data.allSelected
    const records = this.data.records.map((item) => ({ ...item, checked: target }))
    const hazards = this.data.hazards.map((item) => ({ ...item, checked: target }))
    this.setData({ records, hazards })
    this.syncSelection()
  },

  /** 收集选中项（带类型，隐患与工作记录的 id 是两套序列，操作必须分流） */
  getSelectedItems(): { id: number; type: 'record' | 'hazard'; status?: string }[] {
    const out: { id: number; type: 'record' | 'hazard'; status?: string }[] = []
    this.data.records.forEach((item) => {
      if (item.checked) out.push({ id: item.id, type: 'record', status: item.status })
    })
    this.data.hazards.forEach((item) => {
      if (item.checked) out.push({ id: item.id, type: 'hazard', status: item.status })
    })
    return out
  },

  onCardTap(event: WechatMiniprogram.TouchEvent) {
    if (this.data.selectMode) {
      // 多选模式下点卡片 = 切换勾选
      const { type, id } = event.currentTarget.dataset
      this.toggleSelectById(String(type), Number(id))
      return
    }
    const { type, id } = event.currentTarget.dataset
    this.openDetail({ currentTarget: { dataset: { type, id } } } as any)
  },

  // ========== 批量操作 ==========
  generateReport() {
    this.setData({ showReportModal: true, generatingReport: false })
  },

  async batchSubmit() {
    const items = this.getSelectedItems()
    const records = items.filter((item) => item.type === 'record')
    const hazardCount = items.length - records.length
    if (!records.length) {
      wx.showToast({ title: hazardCount ? '隐患不支持提交审批' : '请先勾选记录', icon: 'none' })
      return
    }
    const confirm = await wx.showModal({
      title: '批量提交审批',
      content:
        `将选中的 ${records.length} 条工作记录提交审批` +
        (hazardCount ? `（另 ${hazardCount} 条隐患不参与）` : '') +
        '，仅「草稿/进行中/已驳回」状态可提交。',
    })
    if (!confirm.confirm) return

    this.setData({ submittingBatch: true })
    wx.showLoading({ title: '提交中…', mask: true })
    let ok = 0
    const fails: string[] = []
    try {
      for (const item of records) {
        try {
          await request(`/work-record/${item.id}/submit`, { method: 'POST', data: {} })
          ok += 1
        } catch (error) {
          fails.push(`#${item.id} ${(error as Error).message}`)
        }
      }
      wx.hideLoading()
      if (fails.length) {
        wx.showModal({
          title: `已提交 ${ok} 条，${fails.length} 条失败`,
          content: fails.slice(0, 6).join('\n'),
          showCancel: false,
        })
      } else {
        wx.showToast({ title: `已提交 ${ok} 条`, icon: 'success' })
      }
      this.setData({ selectMode: false, selectedCount: 0, allSelected: false })
      this.load()
    } finally {
      wx.hideLoading()
      this.setData({ submittingBatch: false })
    }
  },

  async batchDelete() {
    const items = this.getSelectedItems()
    if (!items.length) return
    const recordCount = items.filter((item) => item.type === 'record').length
    const hazardCount = items.length - recordCount
    const desc =
      `确定删除选中的 ${items.length} 条记录吗？` +
      (hazardCount ? `（工作记录 ${recordCount} 条、隐患 ${hazardCount} 条）` : '') +
      '此操作不可撤销。'
    const confirm = await wx.showModal({
      title: '确认删除',
      content: desc,
      confirmColor: '#c53030',
    })
    if (!confirm.confirm) return

    this.setData({ deletingBatch: true })
    wx.showLoading({ title: '删除中…', mask: true })
    let ok = 0
    const fails: string[] = []
    try {
      for (const item of items) {
        try {
          if (item.type === 'hazard') {
            await request(`/hazard/${item.id}`, { method: 'DELETE' })
          } else {
            await request(`/work-record/${item.id}`, { method: 'DELETE' })
          }
          ok += 1
        } catch (error) {
          fails.push(`#${item.id} ${(error as Error).message}`)
        }
      }
      invalidateCache(CACHE_KEYS.HAZARD_LIST)
      wx.hideLoading()
      if (fails.length) {
        wx.showModal({
          title: `已删除 ${ok} 条，${fails.length} 条失败`,
          content: fails.slice(0, 6).join('\n'),
          showCancel: false,
        })
      } else {
        wx.showToast({ title: '删除成功', icon: 'success' })
      }
      this.setData({ selectMode: false, selectedCount: 0, allSelected: false })
      this.load()
    } finally {
      wx.hideLoading()
      this.setData({ deletingBatch: false })
    }
  },

  // ========== 报告生成 ==========
  closeReportModal() {
    this.setData({ showReportModal: false })
  },

  selectReportType(event: WechatMiniprogram.TouchEvent) {
    const type = event.currentTarget.dataset.type
    this.setData({ reportType: type })
  },

  onReportDateChange(event: WechatMiniprogram.Input) {
    this.setData({ reportDate: event.detail.value })
  },

  onLlmChange(event: WechatMiniprogram.TouchEvent) {
    this.setData({ useLlm: event.detail.value.length > 0 })
  },

  async confirmGenerateReport() {
    if (!this.data.reportDate) {
      wx.showToast({ title: '请选择日期', icon: 'none' })
      return
    }
    this.setData({ generatingReport: true })
    wx.showLoading({ title: '生成报告中...' })
    try {
      let result: { doc_id?: number }
      const recordIds = this.getSelectedItems()
        .filter((item) => item.type === 'record')
        .map((item) => item.id)
      if (this.data.reportType === 'day') {
        // 监理日志：后端按日期聚合当日工作记录与隐患生成
        result = await request<{ doc_id?: number }>(
          `/document/generate-daily-log?date=${this.data.reportDate}`,
          { method: 'POST', data: {}, timeout: 180000 },
        )
      } else {
        // 周报/月报：LLM 生成文书，勾选的记录作为参考材料
        const typeLabel = this.data.reportType === 'week' ? '监理周报' : '监理月报'
        result = await request<{ doc_id?: number }>('/document/generate-with-llm', {
          method: 'POST',
          data: {
            doc_type: typeLabel,
            title: `${typeLabel} ${this.data.reportDate}`,
            extra_context: this.data.useLlm
              ? `选中的工作记录ID: ${recordIds.join(', ') || '（未勾选，按日期范围生成）'}；截至日期: ${this.data.reportDate}`
              : '',
          },
          timeout: 180000,
        })
      }
      wx.hideLoading()
      wx.showToast({ title: '报告生成成功', icon: 'success' })
      this.closeReportModal()
      // 可以跳转到文档列表或详情
      if (result.doc_id) {
        wx.navigateTo({ url: `/pages/documents/detail?id=${result.doc_id}` })
      }
    } catch (error) {
      wx.hideLoading()
      wx.showToast({ title: (error as Error).message, icon: 'none' })
    } finally {
      this.setData({ generatingReport: false })
    }
  },

  // ========== 记录菜单 ==========
  openRecordMenu(event: WechatMiniprogram.TouchEvent) {
    const stoppable = event as unknown as { stopPropagation?: () => void }
    stoppable.stopPropagation?.()
    const { type, id } = event.currentTarget.dataset
    this.setData({
      showRecordMenu: true,
      currentRecordId: id,
      currentRecordType: type,
    })
  },

  closeRecordMenu() {
    this.setData({ showRecordMenu: false, currentRecordId: 0 })
  },

  async onMenuAction(event: WechatMiniprogram.TouchEvent) {
    const action = event.currentTarget.dataset.action
    const { currentRecordId, currentRecordType } = this.data
    this.closeRecordMenu()

    if (action === 'detail') {
      this.openDetail({ currentTarget: { dataset: { type: currentRecordType, id: currentRecordId } } } as any)
    } else if (action === 'edit') {
      this.openEditModal(currentRecordId, currentRecordType)
    } else if (action === 'share') {
      this.shareRecord(currentRecordId, currentRecordType)
    } else if (action === 'delete') {
      this.deleteRecord(currentRecordId, currentRecordType)
    }
  },

  // ========== 单条记录操作 ==========
  async openEditModal(recordId: number, type: 'record' | 'hazard') {
    if (type === 'hazard') {
      wx.showToast({ title: '隐患记录暂不支持编辑', icon: 'none' })
      return
    }
    wx.showLoading({ title: '加载中...' })
    try {
      const result = await request<DetailItem>(`/work-record/${recordId}`)
      this.setData({
        showEditModal: true,
        editRecordId: recordId,
        editForm: {
          ...result,
          record_type: result.record_type,
        },
      })
    } catch (error) {
      wx.showToast({ title: (error as Error).message, icon: 'none' })
    } finally {
      wx.hideLoading()
    }
  },

  /** 详情弹窗「编辑修改」：关掉详情后复用记录编辑弹窗 */
  editDetailRecord() {
    const detail = this.data.detail
    if (!detail?.id) return
    this.closeDetail()
    this.openEditModal(detail.id, this.data.detailType)
  },

  /** 详情弹窗「查看文书」：跳到工作文书详情（导出 PDF/Word、签认都在那里） */
  openRecordDocument() {
    const id = this.data.detail?.generated_doc_id
    if (id) wx.navigateTo({ url: `/pages/documents/detail?id=${id}` })
    else wx.showToast({ title: '请先生成该记录的文书', icon: 'none' })
  },

  /** 详情弹窗「生成文书」：记录 → 省统表文书并归档「工作文书」；隐患 → 安全隐患整改通知单 */
  async generateRecordDocument(event?: WechatMiniprogram.TouchEvent) {
    const record = this.data.detail
    if (!record?.id || this.data.generatingDoc) return
    const formCode = String(event?.currentTarget?.dataset?.formCode || '')
    if (this.data.detailType === 'record' && !formCode && !record.generated_doc_id) {
      wx.showToast({ title: '请先选择要生成的表单', icon: 'none' })
      return
    }
    this.setData({ generatingDoc: true })
    wx.showLoading({ title: '生成文书...', mask: true })
    try {
      let docId: number
      let title = ''
      if (this.data.detailType === 'hazard') {
        // 隐患：幂等生成/取回「安全隐患整改通知单」（GDAQ30308）
        const res = await request<{ doc_id: number }>(`/hazard/${record.id}/generate-rectification`, {
          method: 'POST',
          timeout: 60000,
        })
        docId = res.doc_id
      } else {
        const res = await request<{ doc_id: number; title: string }>('/form-docs/generate', {
          method: 'POST',
          data: { record_id: record.id, form_code: formCode || undefined },
          timeout: 60000,
        })
        docId = res.doc_id
        title = res.title
      }
      this.setData({ 'detail.generated_doc_id': docId })
      wx.hideLoading()
      wx.showModal({
        title: '文书已生成',
        content: `${title ? `《${title}》` : '文书'}已存入侧边栏「工作文书」对应分类，可查看、签认并导出 PDF / Word。`,
        showCancel: false,
      })
    } catch (error) {
      wx.hideLoading()
      wx.showToast({ title: (error as Error).message, icon: 'none' })
    } finally {
      this.setData({ generatingDoc: false })
    }
  },

  closeEditModal() {
    this.setData({ showEditModal: false, editRecordId: 0, editForm: {} })
  },

  onEditInput(event: WechatMiniprogram.Input) {
    const field = event.currentTarget.dataset.field
    const editForm = { ...this.data.editForm }
    editForm[field] = event.detail.value
    this.setData({ editForm })
  },

  onEditDateChange(event: WechatMiniprogram.PickerChange) {
    this.setData({ 'editForm.record_date': String(event.detail.value) })
  },

  onEditDateTimeChange(event: WechatMiniprogram.CustomEvent<{ value: string }>) {
    const field = String(event.currentTarget.dataset.field || '')
    if (!['inspection_start_time', 'inspection_end_time', 'station_start_time', 'station_end_time'].includes(field)) return
    this.setData({ [`editForm.${field}`]: event.detail.value })
  },

  onAcceptanceResultChange(event: WechatMiniprogram.PickerChange) {
    this.setData({ 'editForm.acceptance_result': this.data.acceptanceResults[Number(event.detail.value)] })
  },

  async saveEditRecord() {
    if (!this.data.editRecordId) return
    this.setData({ savingEdit: true })
    wx.showLoading({ title: '保存中...' })
    try {
      const { editRecordId, editForm } = this.data
      await request(`/work-record/${editRecordId}`, {
        method: 'PUT',
        data: Object.fromEntries(Object.entries(editForm).map(([key, value]) => {
          if (key === 'worker_count') return [key, value === '' || value === null ? null : Number(value)]
          if (key.endsWith('_time') || key === 'record_date') return [key, value || null]
          return [key, value]
        })),
      })
      wx.hideLoading()
      wx.showToast({ title: '保存成功', icon: 'success' })
      this.closeEditModal()
      this.load()
    } catch (error) {
      wx.hideLoading()
      wx.showToast({ title: (error as Error).message, icon: 'none' })
    } finally {
      this.setData({ savingEdit: false })
    }
  },

  async deleteRecord(recordId: number, type: 'record' | 'hazard') {
    const confirm = await wx.showModal({
      title: '确认删除',
      content: '确定删除这条记录吗？此操作不可撤销。',
      confirmColor: '#c53030',
    })
    if (!confirm.confirm) return

    wx.showLoading({ title: '删除中...' })
    try {
      if (type === 'hazard') {
        await request(`/hazard/${recordId}`, { method: 'DELETE' })
        invalidateCache(CACHE_KEYS.HAZARD_LIST)
      } else {
        await request(`/work-record/${recordId}`, { method: 'DELETE' })
      }
      wx.showToast({ title: '删除成功', icon: 'success' })
      this.load()
    } catch (error) {
      wx.showToast({ title: (error as Error).message, icon: 'none' })
    } finally {
      wx.hideLoading()
    }
  },

  async shareRecord(recordId: number, type: 'record' | 'hazard') {
    try {
      let detail: any
      if (type === 'hazard') {
        const hazards = await getCached<HazardItem[]>(
          CACHE_KEYS.HAZARD_LIST,
          () => request<HazardItem[]>('/hazard/list'),
        )
        detail = hazards.find((h) => h.id === recordId)
      } else {
        detail = await request<DetailItem>(`/work-record/${recordId}`)
      }
      if (!detail) {
        wx.showToast({ title: '记录不存在', icon: 'none' })
        return
      }
      // 设置分享内容
      const title = `${detail.record_type || '隐患'}记录 #${recordId}`
      const desc = detail.work_content || detail.evidence_text || detail.section + ' ' + detail.location || ''
      const path = `/pages/work/index?id=${recordId}&type=${type}`
      
      // 触发微信分享
      wx.showShareMenu({
        withShareTicket: true,
        menus: ['shareAppMessage', 'shareTimeline']
      })
      wx.showToast({ title: '请点击右上角转发', icon: 'none' })
      
      // 保存分享数据供 onShareAppMessage 使用
      wx.setStorageSync('share_record_data', {
        title,
        desc,
        path,
        recordId,
        type,
      })
    } catch (error) {
      wx.showToast({ title: (error as Error).message, icon: 'none' })
    }
  },

  // ========== 详情 ==========
  async openDetail(event: WechatMiniprogram.TouchEvent) {
    const { type, id } = event.currentTarget.dataset
    wx.showLoading({ title: '加载详情…' })
    try {
      if (type === 'hazard') {
        const hazard = this.data.hazards.find((h) => h.id === id)
        if (hazard) {
          this.setData({
            showDetail: true,
            detailType: 'hazard',
            detail: {
              ...hazard,
              created_at: hazard.created_at?.replace('T', ' ').slice(0, 16),
            },
            detailPhotos: hazard.image_url ? [hazard.image_url] : [],
          })
        }
      } else {
        const result = await request<DetailItem>(`/work-record/${id}`)
        const photos: string[] = []
        if (result.inspection_photos) {
          result.inspection_photos.forEach((p: string) => {
            if (p) photos.push(absoluteMediaUrl(p))
          })
        }
        if (result.witness_photos) {
          result.witness_photos.forEach((p: string) => {
            if (p) photos.push(absoluteMediaUrl(p))
          })
        }
        const fmtTime = (v?: string | null) => (v ? v.replace('T', ' ').slice(0, 16) : '')
        const inspectionTimeText = (result.inspection_start_time || result.inspection_end_time)
          ? [fmtTime(result.inspection_start_time), fmtTime(result.inspection_end_time)].filter(Boolean).join(' 至 ')
          : (result.record_date || '')
        this.setData({
          showDetail: true,
          detailType: 'record',
          detail: {
            ...result,
            inspection_time_text: inspectionTimeText,
            problem_text: result.problems || result.inspection_remark || result.work_content || '',
            created_at: result.created_at?.replace('T', ' ').slice(0, 16),
          },
          detailPhotos: photos,
          detailMediaEvidence: (result.media_evidence || []).map(item => ({ ...item, url: absoluteMediaUrl(item.url) })),
        })
        void this.loadRecordForms(Number(id))
      }
    } catch (error) {
      wx.showToast({ title: (error as Error).message, icon: 'none' })
    } finally {
      wx.hideLoading()
    }
  },

  closeDetail() {
    this.setData({ showDetail: false, detail: {}, detailPhotos: [], detailMediaEvidence: [], detailForms: [], selectedFormCode: '' })
  },

  selectRecordForm(event: WechatMiniprogram.TouchEvent) {
    const formCode = String(event.currentTarget.dataset.formCode || '')
    if (!formCode) return
    this.setData({ selectedFormCode: this.data.selectedFormCode === formCode ? '' : formCode })
  },

  async loadRecordForms(recordId: number) {
    this.setData({ loadingForms: true, detailForms: [], selectedFormCode: '' })
    try {
      const result = await request<{ forms?: RecordFormOption[] }>(`/form-docs/record/${recordId}/forms`)
      this.setData({ detailForms: result.forms || [] })
    } catch (error) {
      console.warn('加载可用表单失败', error)
      this.setData({ detailForms: [] })
    } finally {
      this.setData({ loadingForms: false })
    }
  },

  previewDetailPhoto(event: WechatMiniprogram.TouchEvent) {
    const url = event.currentTarget.dataset.url
    wx.previewImage({ urls: this.data.detailPhotos, current: url })
  },

  preview(event: WechatMiniprogram.TouchEvent) {
    const url = event.currentTarget.dataset.url
    wx.previewImage({ urls: [url], current: url })
  },

  // ========== 通用 ==========
  toggleTypeDrawer() {
    this.setData({ showTypeDrawer: !this.data.showTypeDrawer })
  },

  selectType(event: WechatMiniprogram.TouchEvent) {
    const type = event.currentTarget.dataset.type
    this.setData({ selectedType: type, selectMode: false, selectedCount: 0, allSelected: false, showTypeDrawer: false })
    this.load()
  },

  openTimeSheet() {
    this.setData({ showTimeSheet: true })
  },

  closeTimeSheet() {
    this.setData({ showTimeSheet: false })
  },

  selectTimeRange(event: WechatMiniprogram.TouchEvent) {
    const key = event.currentTarget.dataset.key
    const item = TIME_RANGES.find((r) => r.key === key)
    if (!item) return
    this.setData({ timeRange: key, timeRangeLabel: item.label, showTimeSheet: false })
    this.load()
  },

  computeRange(): { start: string; end: string } | null {
    if (this.data.timeRange === 'all') return null
    const now = beijingNow()
    const pad = (n: number) => String(n).padStart(2, '0')
    const fmt = (d: Date) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
    let start = beijingNow()
    if (this.data.timeRange === 'today') {
      start = new Date(now.getFullYear(), now.getMonth(), now.getDate())
    } else if (this.data.timeRange === 'week') {
      const day = now.getDay() || 7
      start = new Date(now.getFullYear(), now.getMonth(), now.getDate() - day + 1)
    } else if (this.data.timeRange === 'month') {
      start = new Date(now.getFullYear(), now.getMonth(), 1)
    }
    return { start: fmt(start), end: fmt(now) }
  },

  // 分享回调
  onShareAppMessage() {
    const shareData = wx.getStorageSync('share_record_data')
    if (shareData) {
      // 用后即清，避免下一次分享读到上一条记录的标题与路径
      wx.removeStorageSync('share_record_data')
      return {
        title: shareData.title,
        path: shareData.path,
        imageUrl: '', // 可以添加封面图
      }
    }
    return {
      title: '监理通 - 工作记录',
      path: '/pages/work/index',
    }
  },

  onShareTimeline() {
    const shareData = wx.getStorageSync('share_record_data')
    if (shareData) {
      return {
        title: shareData.title,
        query: `id=${shareData.recordId}&type=${shareData.type}`,
        imageUrl: '',
      }
    }
    return {
      title: '监理通 - 工作记录',
      query: '',
    }
  },
}))
