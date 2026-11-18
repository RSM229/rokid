import { request } from '../../services/request'
import { session } from '../../services/session'
import { config, absoluteMediaUrl } from '../../config'
import { getCached, CACHE_KEYS, invalidateCache } from '../../services/cache'
import { requireLogin } from '../../utils/guard'
import { themed } from '../../utils/theme'
import { ensurePrivacy } from '../../utils/privacy'

// 获取北京时间
function beijingNow(): Date {
  const now = new Date()
  const utc = now.getTime() + now.getTimezoneOffset() * 60000
  return new Date(utc + 8 * 3600000)
}

/** ISO 时间 → 「11月2日 15:32」，不再把 2026-11-02T15:32:10 原样丢给用户 */
function humanTime(iso?: string): string {
  if (!iso) return ''
  const m = /^(\d{4})-(\d{2})-(\d{2})[T\s](\d{2}):(\d{2})/.exec(iso)
  if (!m) return iso.replace('T', ' ').slice(0, 16)
  return `${Number(m[2])}月${Number(m[3])}日 ${m[4]}:${m[5]}`
}

/** 洗掉模型输出里的 Markdown 残留和「场景与活动：」这类机器标签，只留第一句人话 */
function cleanSummary(raw?: string): string {
  if (!raw) return ''
  let text = String(raw)
    .replace(/\r/g, '')
    .replace(/!\[([^\]]*)\]\([^)]*\)/g, '$1')
    .replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')
    .replace(/<[^>]+>/g, '')
    // 标题行/分隔线/列表符号是结构标记，不是现场描述，不能混进摘要
    .replace(/^\s*#{1,6}\s*[^\n]*$/gm, '')
    .replace(/^\s*[-*_]{3,}\s*$/gm, '')
    .replace(/^\s*[-•·]+\s*/gm, '')
    .replace(/[*_`~>]+/g, '')
    .replace(/(场景与活动|旁站情况|巡视情况|检查情况|现场情况|施工内容|工作内容|主要活动|活动描述|情况说明|描述|备注|内容|情况)\s*[:：]\s*/g, '')
    .replace(/\n+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
  // 只留第一句，避免把整段模型输出硬截在半句话
  const cut = text.search(/[。！？!?；;]/)
  if (cut >= 0 && cut < 90) text = text.slice(0, cut + 1)
  text = text.replace(/[…]+$/, '').trim()
  if (text.length > 68) text = text.slice(0, 68).replace(/[，,、\s]+$/, '') + '…'
  return text
}

/**
 * 结构化记录卡：把列表原始记录整理成「时间 / 部位+事项 / 项目·位置 / 清洗摘要 / 待补充」。
 * 纯前端派生，不改后端接口；内部编号 #78、ISO 时间、Markdown 正文不再直接露出。
 */
function decorateRecord(r: WorkRecordItem): WorkRecordItem {
  const part = (r.section || r.location || '').trim()
  const place = (r.location || '').trim()
  const project = (r.project_name || r.system_project_name || '').trim()
  const summary = cleanSummary(r.work_content)
  const partLabel = part || '未填写部位'

  // 事项：项目不一致是明确待办，优先当标题后半段；否则取清洗后首句的第一个短语
  let subject = r.project_mismatch ? '水印项目与系统项目不一致' : ''
  if (!subject) subject = summary ? summary.split(/[，,。！？]/)[0].slice(0, 18) : `${r.record_type || '工作'}记录`
  const titleText = subject.startsWith(partLabel) ? subject : `${partLabel} · ${subject}`

  // 位置若是项目名的截断重复（如「沙盛四路等4条道路工」），不再重复展示
  const looksDup = !!place && !!project && place.slice(0, 5) === project.slice(0, 5)
  const metaText = [project, !looksDup && place !== part ? place : ''].filter(Boolean).join(' / ')
  const summaryText = r.project_mismatch
    ? `现场核对发现${partLabel}水印项目与系统项目不一致，已记录待复核。`
    : (summary || `${r.record_type || '工作'}记录已建立，可继续补充现场情况。`)

  const missing: string[] = []
  if (!r.section) missing.push('施工部位')
  if (!r.location) missing.push('具体位置')
  if (!r.work_content) missing.push('工作内容')
  if (r.project_mismatch) missing.push('项目归属')

  const status = r.status || ''
  const statusTone = status.includes('草稿') ? 'draft'
    : /驳回|退回|不通过/.test(status) ? 'warn'
      : /待|审批|审核/.test(status) ? 'pending'
        : 'done'

  return {
    ...r,
    displayTime: humanTime(r.created_at),
    titleText,
    metaText,
    summaryText,
    missingCount: missing.length,
    missingHint: missing.join('、'),
    statusTone,
    reportDate: (r.created_at || '').slice(0, 10),
  }
}

interface DiaryDocItem {
  id: number
  title: string
  doc_type: string
  form_code?: string
  creator_id?: number
  creator_name?: string
  created_at?: string
}

function humanDiaryTime(createdAt?: string): string {
  if (!createdAt) return ''
  return createdAt.slice(0, 16).replace('T', ' ')
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
  // 结构化卡片展示字段（前端由原始记录派生，不落库、不改后端接口）
  displayTime?: string
  titleText?: string
  metaText?: string
  summaryText?: string
  missingCount?: number
  missingHint?: string
  statusTone?: string
  reportDate?: string
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
  acceptance_participants?: string
  acceptance_content?: string
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
  /** 后端按用户 xlsx「字段清单」(record_field_spec.FORMS) 生成的分组视图 */
  field_sections?: FieldSection[]
}

/** 工作记录详情页「按字段清单」渲染的字段视图（后端 work_record_field_view） */
interface FieldSection {
  title: string
  fields: {
    label: string
    value: string
    note: string
    required: boolean
    auto: boolean
    kind: string
    missing: boolean
    display_value: string
  }[]
}

interface DocMatchItem {
  record_id: number
  record_type: string
  form_code: string
  form_name: string
}

interface DocSheetResult {
  doc_id: number
  record_id: number
  record_type: string
  form_code: string
  form_name: string
  title: string
  status: string
  tone: string
}

interface MenuItem {
  key: string
  label: string
  danger?: boolean
}

interface ReportType {
  key: 'week' | 'month'
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
  { label: '待定', key: 'pending' },
]

const TIME_RANGES = [
  { label: '全部', key: 'all' },
  { label: '今天', key: 'today' },
  { label: '本周', key: 'week' },
  { label: '本月', key: 'month' },
]

const REPORT_TYPES: ReportType[] = [
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
    timeRange: 'today',
    timeRangeLabel: '今天',
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

    // 个人工作记录（监理日记）置顶卡（原型 N1-N4）
    diaryCardVisible: true,
    diaryLoading: false,
    diarySaving: false,
    diaryDocs: [] as DiaryDocItem[],
    showDiarySheet: false,
    diaryIsEdit: false,
    diarySelectedIds: [] as number[],
    diaryPickMap: {} as Record<string, boolean>,

    // 详情弹窗
    showDetail: false,
    navHeight: 88,
    detail: {} as DetailItem,
    detailType: 'record' as 'record' | 'hazard',
    detailPhotos: [] as string[],
    /** 后端返回 field_sections 时按用户字段清单渲染；否则退回旧手写区块 */
    showFieldSections: false,
    // 全屏图片预览（带「上一张/下一张」按钮，替代原生 wx.previewImage）
    imageViewerVisible: false,
    imageViewerUrls: [] as string[],
    imageViewerCurrent: 0,
    detailMediaEvidence: [] as MediaEvidence[],
    detailForms: [] as RecordFormOption[],
    selectedFormCode: '',
    loadingForms: false,
    /** 按需生成表单区块默认收起，点标题才展开（2026-11-12） */
    formChoiceExpanded: false,
    /** 文书生成弹层：先展示按项目性质+工作内容自动匹配的表式（原型 N5/N6） */
    showDocSheet: false,
    docSheetRecordIds: [] as number[],
    docSheetMatched: [] as DocMatchItem[],
    docSheetSubmitting: false,
    /** 生成 → 审批 → 归档 三段（原型：同页弹层内完成） */
    docSheetStep: 'gen' as 'gen' | 'approve' | 'archive',
    docSheetResults: [] as DocSheetResult[],
    docSheetArchived: [] as DocSheetResult[],
    docSheetActing: 0,
    docSheetNote: '',

    // 记录菜单
    showRecordMenu: false,
    recordMenuItems: MENU_ITEMS,
    currentRecordId: 0,
    currentRecordType: 'record' as 'record' | 'hazard',

    // 报告生成弹窗（原型「监理日志是项目级自动任务」：day 型移出，只留周报/月报）
    showReportModal: false,
    reportTypes: REPORT_TYPES,
    reportType: 'week' as 'week' | 'month',
    reportDate: '',
    useLlm: true,

    // 编辑弹窗
    showEditModal: false,
    editRecordId: 0,
    editForm: {} as Record<string, any>,
    acceptanceResults: ['合格', '不合格', '待整改'],
    savingEdit: false,

    // 待定记录改类型（2026-11-13）：后端 /work-record/{id}/set-type 的入口
    showTypeModal: false,
    typeOptions: ['巡视', '旁站', '验收', '见证'],
    selectedTypeValue: '',
    savingType: false,
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
    void ready.then(() => this.loadDiary())
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
        // 隐患接口不支持日期过滤，前端按与工作记录相同的范围过滤，避免「今天」混入历史隐患
        const hazardItems = hazards
          .filter((item) => {
            if (!range) return true
            const date = (item.created_at || '').slice(0, 10)
            return date >= range.start && date <= range.end
          })
          .map((item) => ({
            ...item,
            image_url: absoluteMediaUrl(item.image_path || ''),
            evidence_text: (item.evidence || []).join('；'),
            displayTime: humanTime(item.created_at),
          }))
        this.setData({ records: (result.records || []).map(decorateRecord), hazards: hazardItems })
      } else {
        this.setData({ records: (result.records || []).map(decorateRecord) })
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
    const range = this.computeRange()
    const items = hazards
      .filter((item) => {
        if (!range) return true
        const date = (item.created_at || '').slice(0, 10)
        return date >= range.start && date <= range.end
      })
      .map((item) => ({
        ...item,
        image_url: absoluteMediaUrl(item.image_path || ''),
        evidence_text: (item.evidence || []).join('；'),
        displayTime: humanTime(item.created_at),
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

  /** 置顶卡折叠（原型：置顶卡只是展示区，不占满屏） */
  toggleDiaryCard() {
    this.setData({ diaryCardVisible: !this.data.diaryCardVisible })
  },

  clearChecked() {
    const records = this.data.records.map((item) => (item.checked ? { ...item, checked: false } : item))
    const hazards = this.data.hazards.map((item) => (item.checked ? { ...item, checked: false } : item))
    this.setData({ records, hazards })
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

  /** 卡片「补填 N 项」：打开该记录编辑弹窗，加载完再提示缺哪几栏（避免被 loading 盖掉） */
  onCardFill(event: WechatMiniprogram.TouchEvent) {
    const { id, missing, count } = event.currentTarget.dataset
    const recordId = Number(id)
    if (!recordId) return
    void this.openEditModal(recordId, 'record').then(() => {
      const brief = String(missing || '部分字段').split('、').slice(0, 2).join('、')
      wx.showToast({ title: `待补 ${count || ''} 项：${brief}`, icon: 'none', duration: 2200 })
    })
  },

  // ========== 批量操作 ==========
  generateReport() {
    // 监理日志（day）已按原型移出本页（项目级自动任务），这里只开周报/月报
    const reportType = (this.data.reportType as string) === 'day' ? 'week' : this.data.reportType
    this.setData({ showReportModal: true, generatingReport: false, reportType: reportType as 'week' | 'month' })
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
      if (this.data.reportType === 'week' || this.data.reportType === 'month') {
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
      } else {
        // 兼容分支：历史数据里 reportType 仍可能是 day（监理日志属项目级自动任务，正常不会走到）
        result = await request<{ doc_id?: number }>(
          `/document/generate-daily-log?date=${this.data.reportDate}`,
          { method: 'POST', data: {}, timeout: 180000 },
        )
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

  // ========== 个人工作记录（监理日记）：手动生成 + 24 点兜底 ==========

  todayStr(): string {
    const today = beijingNow()
    const pad = (n: number) => String(n).padStart(2, '0')
    return `${today.getFullYear()}-${pad(today.getMonth() + 1)}-${pad(today.getDate())}`
  },

  async loadDiary() {
    this.setData({ diaryLoading: true })
    try {
      const result = await request<any>('/document/list', {
        method: 'GET',
        data: { doc_type: '监理日记', all_projects: false },
      })
      const list: DiaryDocItem[] = Array.isArray(result) ? result : (result?.documents || [])
      const today = this.todayStr()
      // 日记「仅个人可见」：后端已强制过滤，这里再按 creator_id 兜一道，
      // 防旧版后端/接口异常时他人日记串进置顶卡（2026-11-18）。
      const me = session.getUser()
      const docs = list.filter((d) => d.title === `监理日记_${today}` && (!me || !d.creator_id || d.creator_id === me.user_id))
      this.setData({ diaryDocs: docs })
    } catch (error) {
      console.warn('加载个人工作记录失败', error)
      this.setData({ diaryDocs: [] })
    } finally {
      this.setData({ diaryLoading: false })
    }
  },

  openDiarySheet(event?: any) {
    const fromIds = Array.isArray(event) ? event as number[] : null
    const ids = fromIds && fromIds.length
      ? fromIds
      : this.getSelectedItems().filter((item) => item.type === 'record').map((item) => item.id)
    const diaryPickMap: Record<string, boolean> = {}
    ids.forEach((id) => { diaryPickMap[String(id)] = true })
    this.setData({
      showDiarySheet: true,
      diaryIsEdit: this.data.diaryDocs.length > 0,
      diarySelectedIds: ids,
      diaryPickMap,
    })
  },

  closeDiarySheet() {
    this.setData({ showDiarySheet: false })
  },

  toggleDiaryPick(event: WechatMiniprogram.TouchEvent) {
    const id = Number(event.currentTarget.dataset.id)
    if (!id) return
    const picked = new Set(this.data.diarySelectedIds)
    const diaryPickMap: Record<string, boolean> = { ...this.data.diaryPickMap }
    if (picked.has(id)) { picked.delete(id); delete diaryPickMap[String(id)] }
    else { picked.add(id); diaryPickMap[String(id)] = true }
    this.setData({ diarySelectedIds: [...picked], diaryPickMap })
  },

  async confirmDiary(event?: WechatMiniprogram.TouchEvent) {
    // “按当前全部记录生成”（24 点兜底语义的手动触发）：不传 record_ids，后端汇总本人当日全部记录
    const autoAll = !!(event && event.currentTarget && event.currentTarget.dataset && event.currentTarget.dataset.auto)
    const ids = autoAll ? [] : this.data.diarySelectedIds
    if (!autoAll && !ids.length) {
      wx.showToast({ title: '请至少选择一条记录', icon: 'none' })
      return
    }
    this.setData({ diarySaving: true })
    wx.showLoading({ title: '生成中...', mask: true })
    try {
      const today = this.todayStr()
      const oldDocs = [...this.data.diaryDocs]
      const res = await request<{ doc_id?: number }>(`/document/generate-daily-diary?date=${today}`, {
        method: 'POST',
        data: autoAll ? {} : { record_ids: ids },
        timeout: 180000,
      })
      // 生成成功后再清理旧汇总：失败时不会先丢掉已生成的日记
      for (const doc of oldDocs) {
        if (res?.doc_id && doc.id === res.doc_id) continue
        try { await request(`/document/${doc.id}`, { method: 'DELETE' }) } catch (_) { /* ignore */ }
      }
      wx.hideLoading()
      wx.showToast({ title: (autoAll || !this.data.diaryIsEdit) ? '已生成并置顶' : '已更新', icon: 'success' })
      this.setData({ showDiarySheet: false, diarySelectedIds: [] })
      await this.loadDiary()
    } catch (error) {
      wx.hideLoading()
      wx.showToast({ title: (error as Error).message, icon: 'none' })
    } finally {
      this.setData({ diarySaving: false })
    }
  },

  openDiaryDoc(event: WechatMiniprogram.TouchEvent) {
    const id = Number(event.currentTarget.dataset.id)
    if (id) wx.navigateTo({ url: `/pages/documents/detail?id=${id}` })
  },

  // ========== 文书生成（多选 → 自动匹配表式） ==========

  async openDocSheet() {
    const ids = this.getSelectedItems().filter((item) => item.type === 'record').map((item) => item.id)
    if (!ids.length) {
      wx.showToast({ title: '请先勾选工作记录', icon: 'none' })
      return
    }
    this.setData({
      showDocSheet: true,
      docSheetRecordIds: ids,
      docSheetMatched: [],
      docSheetSubmitting: false,
      docSheetStep: 'gen',
      docSheetResults: [],
      docSheetArchived: [],
      docSheetActing: 0,
      docSheetNote: '',
    })
    wx.showLoading({ title: '匹配表式中…', mask: true })
    try {
      const matched: DocMatchItem[] = []
      for (const rid of ids) {
        try {
          const res = await request<{ forms?: { form_code: string; name: string }[] }>(`/form-docs/record/${rid}/forms`)
          const forms = res.forms || []
          const def = forms[0]
          const rec = this.data.records.find((r) => r.id === rid)
          matched.push({
            record_id: rid,
            record_type: rec?.record_type || '记录',
            form_code: def?.form_code || '',
            form_name: def?.name || '',
          })
        } catch (_) {
          matched.push({ record_id: rid, record_type: '记录', form_code: '', form_name: '' })
        }
      }
      this.setData({ docSheetMatched: matched })
    } finally {
      wx.hideLoading()
    }
  },

  closeDocSheet() {
    this.setData({ showDocSheet: false })
  },

  async confirmDocSheet() {
    const items = this.data.docSheetMatched.filter((m) => m.form_code)
    if (!items.length) {
      wx.showToast({ title: '没有可生成的表式', icon: 'none' })
      return
    }
    this.setData({ docSheetSubmitting: true })
    wx.showLoading({ title: '生成文书中…', mask: true })
    const results: DocSheetResult[] = []
    const fails: string[] = []
    try {
      // N5/N6（2026-11-18）：批量生成——同表式合并一张文书（栏内分条，
      // 时段取并集），异表式分表。原先逐条循环调 /generate 会一条一张表。
      const recordIds = items.map((m) => m.record_id)
      const res = await request<{
        results?: {
          doc_id: number; title: string; form_code: string; form_name?: string;
          record_ids?: number[]; record_type?: string; status?: string; merged?: boolean;
        }[]
        errors?: string[]
      }>('/form-docs/generate-batch', {
        method: 'POST',
        data: { record_ids: recordIds },
        timeout: 180000,
      })
      const recTypeOf = (rid: number) =>
        this.data.docSheetMatched.find((m) => m.record_id === rid)?.record_type || '记录'
      for (const r of res.results || []) {
        results.push({
          doc_id: r.doc_id,
          record_id: r.record_ids?.[0] || 0,
          record_type: r.record_type || (r.record_ids || []).map(recTypeOf).join('·') || '记录',
          form_code: r.form_code,
          form_name: r.form_name || '',
          title: r.title,
          status: r.status || '草稿',
          tone: this.docTone(r.status || '草稿'),
        })
      }
      for (const e of res.errors || []) fails.push(e)
      wx.hideLoading()
      this.setData({
        docSheetResults: results,
        docSheetArchived: results.filter((item) => item.tone === 'ok'),
        docSheetStep: results.length ? 'approve' : 'gen',
        docSheetNote: fails.length
          ? `已生成 ${results.length} 份，${fails.length} 份失败：${fails.slice(0, 2).join('；')}`
          : `已生成 ${results.length} 份文书（同类合并、异类分表）。审批状态只在系统里，成品签名栏/备注栏留空。`,
      })
      if (!results.length && fails.length) {
        wx.showModal({ title: '生成失败', content: fails.slice(0, 6).join('\n'), showCancel: false })
      } else {
        wx.showToast({ title: `已生成 ${results.length} 份`, icon: 'success' })
      }
      // 生成后不关闭弹层：审批/归档在同页分段继续（原型要求）
      this.setData({ selectMode: false, selectedCount: 0, allSelected: false })
      this.clearChecked()
      void this.load()
    } catch (error) {
      wx.hideLoading()
      // 整批失败：回退提示（保持弹层开着让用户重试/手选表式）
      wx.showToast({ title: (error as Error).message || '生成失败', icon: 'none' })
    } finally {
      wx.hideLoading()
      this.setData({ docSheetSubmitting: false })
    }
  },

  docTone(status: string): string {
    if (/驳回|退回/.test(status)) return 'warn'
    if (/待审批|待审核|审核中/.test(status)) return 'pending'
    if (/已归档|已审批|通过/.test(status)) return 'ok'
    return 'draft'
  },

  switchDocStep(event: WechatMiniprogram.TouchEvent) {
    const step = String(event.currentTarget.dataset.step || 'gen') as 'gen' | 'approve' | 'archive'
    if (step === 'approve' && !this.data.docSheetResults.length) {
      wx.showToast({ title: '请先生成文书', icon: 'none' })
      return
    }
    this.setData({ docSheetStep: step })
  },

  /** 刷新单份文书状态：优先用动作接口返回值，缺失时再查详情 */
  async refreshDocResult(docId: number, status?: string) {
    let nextStatus = status || ''
    let nextTitle = ''
    if (!nextStatus) {
      try {
        const res = await request<{ status?: string; title?: string }>(`/document/${docId}`)
        nextStatus = res.status || ''
        nextTitle = res.title || ''
      } catch (_) {
        // 状态刷新失败不阻断审批动作
        return
      }
    }
    const results = this.data.docSheetResults.map((item) =>
      item.doc_id === docId
        ? {
            ...item,
            status: nextStatus || item.status,
            title: nextTitle || item.title,
            tone: this.docTone(nextStatus || item.status),
          }
        : item,
    )
    this.setData({
      docSheetResults: results,
      docSheetArchived: results.filter((item) => item.tone === 'ok'),
    })
  },

  /** 提交审批：必签项未完成时后端返回 400，提示并引导去签认 */
  async submitDoc(event: WechatMiniprogram.TouchEvent) {
    const docId = Number(event.currentTarget.dataset.docId)
    if (!docId || this.data.docSheetActing) return
    this.setData({ docSheetActing: docId })
    wx.showLoading({ title: '提交中…', mask: true })
    try {
      const res = await request<{ status?: string }>(`/document/${docId}/submit`, { method: 'POST' })
      wx.hideLoading()
      await this.refreshDocResult(docId, res?.status)
      this.setData({ docSheetNote: '已提交。需总监签发的文书进入审批，其余直接归档。' })
    } catch (error) {
      wx.hideLoading()
      const message = (error as Error).message || ''
      if (/必签|签认/.test(message)) {
        const confirm = await wx.showModal({
          title: '还差必签项',
          content: message,
          confirmText: '去签认',
          cancelText: '稍后',
        })
        if (confirm.confirm) wx.navigateTo({ url: `/pages/documents/detail?id=${docId}` })
      } else {
        wx.showModal({ title: '提交失败', content: message, showCancel: false })
      }
    } finally {
      this.setData({ docSheetActing: 0 })
    }
  },

  async approveDoc(event: WechatMiniprogram.TouchEvent) {
    const docId = Number(event.currentTarget.dataset.docId)
    if (!docId || this.data.docSheetActing) return
    const confirm = await wx.showModal({
      title: '审批通过',
      content: '确认通过这份文书？通过后状态记为已审批，成品不写入审批字样。',
    })
    if (!confirm.confirm) return
    this.setData({ docSheetActing: docId })
    wx.showLoading({ title: '审批中…', mask: true })
    try {
      const res = await request<{ status?: string }>(`/document/${docId}/approve`, { method: 'POST' })
      wx.hideLoading()
      await this.refreshDocResult(docId, res?.status)
      this.setData({ docSheetNote: '已审批通过。' })
    } catch (error) {
      wx.hideLoading()
      wx.showModal({ title: '审批失败', content: (error as Error).message, showCancel: false })
    } finally {
      this.setData({ docSheetActing: 0 })
    }
  },

  async rejectDoc(event: WechatMiniprogram.TouchEvent) {
    const docId = Number(event.currentTarget.dataset.docId)
    if (!docId || this.data.docSheetActing) return
    const input = await wx.showModal({
      title: '退回文书',
      editable: true,
      placeholderText: '填写退回原因（可留空）',
    })
    if (!input.confirm) return
    this.setData({ docSheetActing: docId })
    wx.showLoading({ title: '退回中…', mask: true })
    try {
      const res = await request<{ status?: string }>(`/document/${docId}/reject`, {
        method: 'POST',
        data: { reason: (input.content || '').trim() },
      })
      wx.hideLoading()
      await this.refreshDocResult(docId, res?.status)
      this.setData({ docSheetNote: '已退回，审批状态只在系统里，成品不写审核字样。' })
    } catch (error) {
      wx.hideLoading()
      wx.showModal({ title: '退回失败', content: (error as Error).message, showCancel: false })
    } finally {
      this.setData({ docSheetActing: 0 })
    }
  },

  openDocDetail(event: WechatMiniprogram.TouchEvent) {
    const docId = Number(event.currentTarget.dataset.docId)
    if (docId) wx.navigateTo({ url: `/pages/documents/detail?id=${docId}` })
  },

  openDocArchive(event: WechatMiniprogram.TouchEvent) {
    const docId = Number(event.currentTarget.dataset.docId)
    if (!docId) return
    wx.navigateTo({ url: `/pages/documents/detail?id=${docId}` })
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

  /** 详情弹窗「生成文书」：记录 → 省统表文书并同步我的资料库；隐患 → 安全隐患整改通知单。
   *
   * 🔴 2026-11-17 改：不传 form_code，让后端 effective_record_form_code() 自动判断
   * 工程类型 + 记录类型 → 对应表式。用户无需手动选表。
   */
  async generateRecordDocument(event?: WechatMiniprogram.TouchEvent) {
    const record = this.data.detail
    if (!record?.id || this.data.generatingDoc) return
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
        // 🔴 不传 form_code，后端 effective_record_form_code() 自动判断表式
        const res = await request<{ doc_id: number; title: string }>('/form-docs/generate', {
          method: 'POST',
          data: { record_id: record.id },
          timeout: 60000,
        })
        docId = res.doc_id
        title = res.title
      }
      this.setData({ 'detail.generated_doc_id': docId })
      wx.hideLoading()
      wx.showModal({
        title: '文书已生成',
        content: `${title ? `《${title}》` : '文书'}已归档到「我的资料库 / 项目文件夹 / 工作记录与表单」，并可在资料库中查看、签认和导出。`,
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

  /** 「具体位置」旁的定位按钮：微信内置地图选点，取可读地址（省市区+街道门牌+POI 名）回填。
   *  用 chooseLocation 而非 getLocation —— 后者只给经纬度，监理记录要的是能直接看懂的地址文字。
   *  返回值 name=地点名、address=详细地址；两者拼接，缺一取其一。 */
  async chooseLocationForEdit() {
    const allowed = await ensurePrivacy('位置信息用于填写工作记录的具体位置，')
    if (!allowed) return
    wx.chooseLocation({
      success: (res) => {
        const name = (res.name || '').trim()
        const address = (res.address || '').trim()
        const readable = [name, address].filter(Boolean).filter((part, i, arr) => arr.indexOf(part) === i).join(' · ')
        if (!readable) {
          wx.showToast({ title: '未获取到地址，请手动填写', icon: 'none' })
          return
        }
        this.setData({ 'editForm.location': readable })
        wx.showToast({ title: '已填入具体位置', icon: 'success' })
      },
      fail: (err) => {
        // 用户主动取消不打扰；仅授权/系统失败才引导去设置
        const msg = String((err as { errMsg?: string })?.errMsg || '')
        if (/cancel/i.test(msg)) return
        wx.showModal({
          title: '无法获取位置',
          content: '请在设置中允许位置权限，也可直接在输入框手动填写具体位置。',
          confirmText: '去设置',
          success: (r) => { if (r.confirm) wx.openSetting({}) },
        })
      },
    })
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
            showFieldSections: false,
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
          showFieldSections: Array.isArray(result.field_sections) && result.field_sections.length > 0,
          formChoiceExpanded: false,
          detail: {
            ...result,
            inspection_time_text: inspectionTimeText,
            // 🔴 问题及处理情况只该是 problems（巡视备注可作弱兜底），
            // 绝不能落 work_content：那是施工内容/照片水印文字，
            // 拿它顶替会让「问题处理」显示成一段水印（2026-11-13 现场确认）。
            problem_text: result.problems || result.inspection_remark || '',
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
    this.setData({ showDetail: false, detail: {}, detailPhotos: [], detailMediaEvidence: [], detailForms: [], selectedFormCode: '', formChoiceExpanded: false, showFieldSections: false })
  },

  /** 待定记录改类型：后端 set-type 早已存在，这里补上小程序入口（2026-11-13）。
   *  「待定」不在 RECORD_DOC_TYPES 里，不改类型就选不到任何表式；
   *  对话侧另有纯后端快捷指令「改成巡视」，现网旧版本也能用。 */
  openTypeModal() {
    if (this.data.detailType !== 'record' || this.data.detail.record_type !== '待定') return
    this.setData({ showTypeModal: true, selectedTypeValue: '', savingType: false })
  },

  closeTypeModal() {
    if (this.data.savingType) return
    this.setData({ showTypeModal: false, selectedTypeValue: '' })
  },

  selectRecordType(event: WechatMiniprogram.TouchEvent) {
    const value = String(event.currentTarget.dataset.type || '')
    if (value) this.setData({ selectedTypeValue: value })
  },

  async confirmRecordType() {
    const id = Number(this.data.detail.id || 0)
    const recordType = this.data.selectedTypeValue
    if (!id || !recordType || this.data.savingType) return
    this.setData({ savingType: true })
    wx.showLoading({ title: '修改类型…', mask: true })
    try {
      await request<{ record_type: string }>(`/work-record/${id}/set-type`, {
        method: 'POST',
        data: { record_type: recordType },
      })
      wx.hideLoading()
      this.setData({ showTypeModal: false, selectedTypeValue: '', savingType: false })
      wx.showToast({ title: `已改为${recordType}`, icon: 'success' })
      // 详情标题/可用表式随类型变化，列表 tab 的归类也会变，两边都刷新
      await this.load()
      await this.openDetail({
        currentTarget: { dataset: { id, type: 'record' } },
      } as unknown as WechatMiniprogram.TouchEvent)
    } catch (error) {
      wx.hideLoading()
      this.setData({ savingType: false })
      wx.showToast({ title: (error as Error).message || '修改失败', icon: 'none' })
    }
  },

  openCollaboration() {
    if (this.data.detailType !== 'record' || !this.data.detail.id) return
    wx.navigateTo({ url: `/pages/todo/compose?type=work_record&id=${this.data.detail.id}` })
  },

  /** 按需生成表单：默认收起，点标题才展开表单列表 */
  toggleFormChoice() {
    this.setData({ formChoiceExpanded: !this.data.formChoiceExpanded })
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

  /** 详情弹窗里的现场照片：传详情全量照片，可左右切到上一张/下一张 */
  previewDetailPhoto(event: WechatMiniprogram.TouchEvent) {
    const url = String(event.currentTarget.dataset.url || '')
    this.openImageViewer(this.data.detailPhotos, url)
  },

  /** 列表卡片缩略图：以当前列表的隐患图片为全集，同样支持上一张/下一张 */
  preview(event: WechatMiniprogram.TouchEvent) {
    const url = String(event.currentTarget.dataset.url || '')
    const listUrls = (this.data.hazards || [])
      .map((item: HazardItem) => String(item.image_url || ''))
      .filter(Boolean)
    this.openImageViewer([...listUrls, url], url)
  },

  /** 打开带按钮的全屏预览（去重；current 定位到点击的那张） */
  openImageViewer(urls: string[], currentUrl: string) {
    const list = Array.from(new Set((urls || []).filter(Boolean)))
    if (!list.length) return
    this.setData({
      imageViewerVisible: true,
      imageViewerUrls: list,
      imageViewerCurrent: Math.max(0, list.indexOf(currentUrl)),
    })
  },

  onImageViewerClose() {
    this.setData({ imageViewerVisible: false, imageViewerUrls: [], imageViewerCurrent: 0 })
  },

  // ========== 通用 ==========
  /** 「全部」入口：选了巡视/验收等类型后再点它，直接回到全部列表，
   *  同时保持抽屉展开，方便接着换其它类型；已是全部时仍是展开/收起抽屉。 */
  toggleTypeDrawer() {
    if (this.data.selectedType !== 'all') {
      this.setData({
        selectedType: 'all',
        selectMode: false,
        selectedCount: 0,
        allSelected: false,
        showTypeDrawer: true,
      })
      this.load()
      return
    }
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
