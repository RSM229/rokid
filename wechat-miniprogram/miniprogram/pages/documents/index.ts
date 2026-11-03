import { request } from '../../services/request'
import { session } from '../../services/session'
import { config, absoluteMediaUrl } from '../../config'
import { themed } from '../../utils/theme'

const RECT_DOC_TYPE = '安全隐患整改通知单'
const IMAGE_TYPE = 'image'

/** 统一类型 tab：文书分类、整改通知单/待审批、台账收进同一条筛选栏。
 * 数据仍各走各的（kind=doc 走 /document/list，kind=ledger 走 /ledger/list），渲染层合一。 */
interface TypeTab {
  key: string
  label: string
  kind: 'doc' | 'ledger'
  docType: string
  status: string
}

const TYPE_TABS: TypeTab[] = [
  { key: 'all', label: '全部文书', kind: 'doc', docType: '', status: '' },
  { key: 'xunshi', label: '巡视记录', kind: 'doc', docType: '巡视记录', status: '' },
  { key: 'pangzhan', label: '旁站记录', kind: 'doc', docType: '旁站记录', status: '' },
  { key: 'jianzheng', label: '见证记录', kind: 'doc', docType: '见证取样记录', status: '' },
  { key: 'yanshou', label: '验收记录', kind: 'doc', docType: '验收记录', status: '' },
  { key: 'daiding', label: '待定记录', kind: 'doc', docType: '', status: '' },
  { key: 'rect', label: '整改通知单', kind: 'doc', docType: RECT_DOC_TYPE, status: '' },
  { key: 'pending', label: '待审批', kind: 'doc', docType: '', status: '待审批' },
  { key: 'ledger', label: '台账', kind: 'ledger', docType: '', status: '' },
]

/**
 * 现场记录文书分类：与工作记录类型一一对应
 * docType 用后端 Document.doc_type 原值；为空表示「不属于其余四类的其他文书」
 */
const DOC_CATEGORIES = [
  { key: 'xunshi', label: '巡视记录', docType: '巡视记录' },
  { key: 'pangzhan', label: '旁站记录', docType: '旁站记录' },
  { key: 'jianzheng', label: '见证记录', docType: '见证取样记录' },
  { key: 'yanshou', label: '验收记录', docType: '验收记录' },
  { key: 'daiding', label: '待定记录', docType: '' },
]

const TYPED_DOC_TYPES = DOC_CATEGORIES.map((item) => item.docType).filter(Boolean)

/**
 * 系统内已知的文书类型全集。
 * 「待定记录」= 不属于任何已知类型的文书（而不是"除四类现场记录外的全部"，
 * 否则监理日志/周报/通知单这些与「待定」无关的文书会被错误地塞进待定分类）。
 */
const KNOWN_DOC_TYPES = [
  ...TYPED_DOC_TYPES,
  '监理日志',
  '监理周报',
  '监理月报',
  '安全隐患整改通知单',
  '安全隐患整改回复单',
  '监理工程师通知单（质量类）',
]

/** doc_type → 列表短徽标（草稿态显示「是什么」，比 DOC 角标更诚实） */
const SHORT_TYPE_BADGES: Record<string, string> = {
  '巡视记录': '巡视',
  '旁站记录': '旁站',
  '见证取样记录': '见证',
  '验收记录': '验收',
  '监理日志': '日志',
  '安全监理日志': '安全日志',
  '监理周报': '周报',
  '监理月报': '月报',
  '安全隐患整改通知单': '整改通知',
  '安全隐患整改回复单': '整改回复',
  '监理工程师通知单（质量类）': '通知单',
  '材料台账': '台账',
}

/** 列表条目装饰：图标统一类型短徽标（巡视/旁站/日志…），文件名按可用文件带 .pdf/.docx 后缀 */
function decorateDocBadge(item: Record<string, unknown>): Record<string, unknown> {
  const docType = String(item.doc_type || '')
  const hasPdf = Boolean(item.pdf_path)
  const badge = SHORT_TYPE_BADGES[docType] || docType.slice(0, 2)
  return { ...item, hasPdf, badge }
}

interface LedgerTypeItem {
  key: string
  label: string
}

interface LedgerRecord {
  // 索引签名：让 LedgerRecord 可直接并入统一的 Record<string, unknown>[] 列表
  [key: string]: unknown
  id: number
  // 记录来源台账（决定点击后的跳转目标）
  _kind?: 'image' | 'hazard' | 'doc'
  // 影像台账
  source?: string
  original_path?: string
  watermarked_path?: string
  taken_at?: string
  // 隐患台账
  status?: string
  risk_status?: string
  risk_category?: string
  top1_risk?: string
  image_path?: string
  rectification_image_path?: string
  // 文书类台账
  title?: string
  doc_number?: string
  pdf_path?: string
  // 公共字段
  section?: string
  location?: string
  weather?: string
  hazard_id?: number | null
  remark?: string
  created_at?: string
  image_url?: string
}

Page(themed({
  data: {
    // 统一列表：文书与台账记录共用一个渲染数组（方案A 合二为一）
    items: [] as Record<string, unknown>[],
    _allList: [] as Record<string, unknown>[],
    typeTabs: TYPE_TABS,
    activeType: 'all',
    source: 'doc' as 'doc' | 'ledger',
    activeTabLabel: '全部文书',
    loading: false,
    empty: false,
    keyword: '',
    // 台账
    ledgerTypes: [] as LedgerTypeItem[],
    ledgerIndex: 0,
    showFilter: false,
    ledgerKeyword: '',
    ledgerSection: '',
    ledgerStartDate: '',
    ledgerEndDate: '',
    activeFilterCount: 0,
    exporting: false,
    exportFormat: 'excel' as 'excel' | 'pdf',
    page: 1,
    pageSize: 30,
    hasMore: false,
    loadingMore: false,
    // 多选模式（右上角「选择」进入）：点条目=勾选，底部批量删除/提交审批
    selectMode: false,
    selected: {} as Record<number, boolean>,
    selectedCount: 0,
    allSelected: false,
    // 签认 / 用章弹窗（长按「签认」触发，逻辑与 detail 页一致）
    showSign: false,
    signDocId: 0,
    signDocTitle: '',
    signSlots: [] as { key: string; label: string }[],
    signSlotIndex: 0,
    signatures: [] as { id: number; name: string }[],
    signSignatureIndex: 0,
    signPassword: '',
    signing: false,
    projectSealId: 0,
  },

  /** 切换统一类型 tab：同步 source / 标签，并清掉跨源残留状态 */
  applyType(key: string) {
    const tab = TYPE_TABS.find((item) => item.key === key) || TYPE_TABS[0]
    this.setData({
      activeType: tab.key,
      source: tab.kind,
      activeTabLabel: tab.label,
      keyword: '',
      ledgerKeyword: '',
      selectMode: false,
      selected: {},
      selectedCount: 0,
      allSelected: false,
    })
  },

  onLoad(options: { tab?: string; type?: string }) {
    if (options && options.tab === 'ledger') {
      this.applyType('ledger')
      return
    }
    // 从侧边栏「工作文书」子项进来：type = xunshi/pangzhan/jianzheng/yanshou/daiding
    if (options && options.type) {
      const tab = TYPE_TABS.find((item) => item.key === options.type)
      if (tab) this.applyType(tab.key)
    }
  },

  onShow() {
    if (!session.getToken()) {
      wx.reLaunch({ url: '/pages/login/index' })
      return
    }
    if (this.data.source === 'ledger') {
      this.ensureLedger()
    } else {
      this.loadDocs()
    }
  },

  onPullDownRefresh() {
    const task = this.data.source === 'ledger' ? this.reloadLedger() : this.loadDocs()
    task.finally(() => wx.stopPullDownRefresh())
  },

  onReachBottom() {
    if (this.data.source === 'ledger') this.loadMoreLedger()
  },

  /** 统一类型 tab 点击：文书分类 / 整改通知单 / 待审批 / 台账 同一条栏 */
  selectType(event: WechatMiniprogram.TouchEvent) {
    const key = String(event.currentTarget.dataset.key || '')
    if (key === this.data.activeType) return
    this.applyType(key)
    if (this.data.source === 'ledger') {
      this.ensureLedger()
    } else {
      this.loadDocs()
    }
  },

  // ==================== 文书列表 ====================

  onKeywordInput(event: WechatMiniprogram.Input) {
    this.setData({ keyword: event.detail.value })
    // 文书=本地即时过滤；台账=等服务端，键盘「搜索」确认后再请求
    if (this.data.source === 'doc') this.applyLocalFilter()
  },

  /** 台账模式：搜索框确认后按关键词向服务端检索 */
  onSearchConfirm() {
    if (this.data.source !== 'ledger') return
    this.setData({ ledgerKeyword: this.data.keyword.trim() })
    this.reloadLedger()
  },

  applyLocalFilter() {
    const keyword = this.data.keyword.trim().toLowerCase()
    const base = this.data._allList as Record<string, unknown>[]
    if (!keyword) {
      this.setData({ items: base, empty: base.length === 0 })
      return
    }
    const items = base.filter((item) =>
      [item.title, item.doc_type, item.status, item.section, item.location]
        .filter((v) => typeof v === 'string')
        .some((v) => (v as string).toLowerCase().includes(keyword)),
    )
    this.setData({ items, empty: items.length === 0 })
  },

  async loadDocs() {
    this.setData({ loading: true })
    try {
      const tab = TYPE_TABS.find((item) => item.key === this.data.activeType)
      let params = ''
      if (tab?.docType) {
        // 巡视/旁站/见证/验收/整改通知单：后端按 doc_type 精确过滤
        params = `?doc_type=${encodeURIComponent(tab.docType)}`
      } else if (tab?.status) {
        // 待审批：后端按 status 过滤
        params = `?status=${encodeURIComponent(tab.status)}`
      }
      const result = await request<unknown[] | { items: unknown[] }>(`/document/list${params}`)
      let docs = (Array.isArray(result) ? result : (result.items || [])) as Record<string, unknown>[]
      // 待定记录：不归属任何已知文书类型的文书（待定工作记录本身按设计不生成文书，
      // 所以此分类通常为空 —— 监理日志/周报/通知单等有自己归宿的文书不再混进来）
      if (this.data.activeType === 'daiding') {
        docs = docs.filter((item) => !KNOWN_DOC_TYPES.includes(String(item.doc_type || '')))
      }
      const items = docs.map(decorateDocBadge)
      this.setData({ _allList: items })
      this.applyLocalFilter()
    } catch {
      this.setData({ items: [], _allList: [], empty: true })
    } finally {
      this.setData({ loading: false })
    }
  },

  /** 「详情」按钮：台账记录按来源路由（隐患→隐患页，影像→预览），文书→文书详情 */
  openDetail(event: WechatMiniprogram.TouchEvent) {
    const id = Number(event.currentTarget.dataset.id || 0)
    if (!id) return
    const item = this.data.items.find((row) => Number(row.id) === id)
    if (item && item._ledger) {
      this.routeLedgerRecord(item as unknown as LedgerRecord)
      return
    }
    wx.navigateTo({ url: `/pages/documents/detail?id=${id}` })
  },

  /** 列表文件行：直接打开文书内容——有 PDF 直开 PDF，否则导出 Word 打开 */
  openDocFile(event: WechatMiniprogram.TouchEvent) {
    const id = Number(event.currentTarget.dataset.id || 0)
    if (!id) return
    this.openDocFileById(id)
  },

  openDocFileById(id: number) {
    const doc = this.data.items.find((item) => Number(item.id) === id)
    if (doc && doc.pdf_path) this.openPdfDoc(id)
    else this.openWordDoc(id)
  },

  openPdfDoc(id: number) {
    wx.showLoading({ title: '下载中', mask: true })
    wx.downloadFile({
      url: `${config.apiBaseUrl}/document/${id}/download-pdf`,
      header: { Authorization: `Bearer ${session.getToken()}` },
      success(res) {
        wx.hideLoading()
        if (res.statusCode === 200) {
          wx.openDocument({ filePath: res.tempFilePath, fileType: 'pdf', showMenu: true })
        } else {
          wx.showToast({ title: 'PDF下载失败', icon: 'none' })
        }
      },
      fail() { wx.hideLoading(); wx.showToast({ title: 'PDF下载失败', icon: 'none' }) },
    })
  },

  openWordDoc(id: number) {
    wx.showLoading({ title: '生成Word中', mask: true })
    request(`/document/${id}/export-docx`).then(() => {
      wx.downloadFile({
        url: `${config.apiBaseUrl}/document/${id}/download-docx`,
        header: { Authorization: `Bearer ${session.getToken()}` },
        success(res) {
          wx.hideLoading()
          if (res.statusCode === 200) {
            wx.openDocument({ filePath: res.tempFilePath, fileType: 'docx', showMenu: true })
          } else {
            wx.showToast({ title: 'Word下载失败', icon: 'none' })
          }
        },
        fail() { wx.hideLoading(); wx.showToast({ title: 'Word下载失败', icon: 'none' }) },
      })
    }).catch((error) => { wx.hideLoading(); wx.showToast({ title: (error as Error).message, icon: 'none' }) })
  },

  // ==================== 多选 / 长按操作 ====================

  /** 右上角「选择」：进入/退出多选模式 */
  toggleSelectMode() {
    const selectMode = !this.data.selectMode
    this.setData({ selectMode, selected: {}, selectedCount: 0, allSelected: false })
  },

  /** 行卡点击：多选模式=勾选；台账记录=按来源路由；文书=直接打开文件内容 */
  onRowTap(event: WechatMiniprogram.TouchEvent) {
    const id = Number(event.currentTarget.dataset.id || 0)
    if (!id) return
    const item = this.data.items.find((row) => Number(row.id) === id)
    if (item && item._ledger) {
      if (!this.data.selectMode) this.routeLedgerRecord(item as unknown as LedgerRecord, event)
      return
    }
    if (!this.data.selectMode) {
      this.openDocFile(event)
      return
    }
    const selected = { ...this.data.selected }
    if (selected[id]) delete selected[id]
    else selected[id] = true
    const count = Object.keys(selected).length
    this.setData({ selected, selectedCount: count, allSelected: count === this.data.items.length && count > 0 })
  },

  selectAll() {
    if (this.data.allSelected) {
      this.setData({ selected: {}, selectedCount: 0, allSelected: false })
      return
    }
    const selected: Record<number, boolean> = {}
    this.data.items.forEach((item) => { selected[Number(item.id)] = true })
    this.setData({ selected, selectedCount: this.data.items.length, allSelected: true })
  },

  /** 长按文件：按当前状态给操作菜单（打开 / 签认 / 提交审批 / 删除 / 详情）；台账记录不响应 */
  onItemLongPress(event: WechatMiniprogram.TouchEvent) {
    if (this.data.selectMode) return
    const id = Number(event.currentTarget.dataset.id || 0)
    if (!id) return
    const doc = this.data.items.find((item) => Number(item.id) === id)
    if (!doc || doc._ledger) return
    const status = String(doc?.status || '')
    const actions: string[] = ['打开文件']
    const handlers: Array<() => void> = [() => this.openDocFileById(id)]
    if (status !== '已归档') {
      actions.push('签认 / 用章')
      handlers.push(() => void this.openSignFor(id))
    }
    if (status === '草稿') {
      actions.push('提交审批')
      handlers.push(() => this.submitDoc(id))
    }
    actions.push('删除文书')
    handlers.push(() => this.deleteDoc(id))
    actions.push('更多（详情页）')
    handlers.push(() => wx.navigateTo({ url: `/pages/documents/detail?id=${id}` }))
    wx.showActionSheet({
      itemList: actions,
      success: (res) => {
        const fn = handlers[res.tapIndex]
        if (fn) fn()
      },
    })
  },

  deleteDoc(id: number) {
    wx.showModal({
      title: '删除文书',
      content: '删除后不可恢复，确认删除该文书？',
      success: async (res) => {
        if (!res.confirm) return
        try {
          await request(`/document/${id}`, { method: 'DELETE' })
          wx.showToast({ title: '已删除', icon: 'success' })
          this.loadDocs()
        } catch (error) {
          wx.showModal({ title: '删除失败', content: (error as Error).message, showCancel: false })
        }
      },
    })
  },

  submitDoc(id: number) {
    wx.showModal({
      title: '提交审批',
      content: '确认提交该文书进入审批流程？',
      success: async (res) => {
        if (!res.confirm) return
        try {
          await request(`/document/${id}/submit`, { method: 'POST' })
          wx.showToast({ title: '已提交', icon: 'success' })
          this.loadDocs()
        } catch (error) {
          wx.showModal({ title: '提交失败', content: (error as Error).message, showCancel: false })
        }
      },
    })
  },

  async batchDelete() {
    const ids = Object.keys(this.data.selected).map(Number)
    if (!ids.length) return
    wx.showModal({
      title: '批量删除',
      content: `将删除选中的 ${ids.length} 份文书，删除后不可恢复。`,
      success: async (res) => {
        if (!res.confirm) return
        wx.showLoading({ title: '删除中…', mask: true })
        let ok = 0
        const failed: string[] = []
        for (const id of ids) {
          try {
            await request(`/document/${id}`, { method: 'DELETE' })
            ok++
          } catch (error) {
            failed.push(`#${id}：${(error as Error).message}`)
          }
        }
        wx.hideLoading()
        this.setData({ selected: {}, selectedCount: 0, allSelected: false })
        wx.showToast({ title: `已删除 ${ok} 份`, icon: 'none' })
        if (failed.length) wx.showModal({ title: '部分删除失败', content: failed.join('\n'), showCancel: false })
        this.loadDocs()
      },
    })
  },

  async batchSubmit() {
    const ids = Object.keys(this.data.selected).map(Number)
    if (!ids.length) return
    wx.showModal({
      title: '批量提交审批',
      content: `将提交选中的 ${ids.length} 份文书进入审批流程。`,
      success: async (res) => {
        if (!res.confirm) return
        wx.showLoading({ title: '提交中…', mask: true })
        let ok = 0
        const failed: string[] = []
        for (const id of ids) {
          try {
            await request(`/document/${id}/submit`, { method: 'POST' })
            ok++
          } catch (error) {
            failed.push(`#${id}：${(error as Error).message}`)
          }
        }
        wx.hideLoading()
        this.setData({ selected: {}, selectedCount: 0, allSelected: false })
        wx.showToast({ title: `已提交 ${ok} 份`, icon: 'none' })
        if (failed.length) wx.showModal({ title: '部分提交失败', content: failed.join('\n'), showCancel: false })
        this.loadDocs()
      },
    })
  },

  // ---- 签认 / 用章（长按「签认」触发） ----

  async openSignFor(id: number) {
    const doc = this.data.items.find((item) => Number(item.id) === id)
    try {
      const [signoffs, signatures] = await Promise.all([
        request<{ slots: { key: string; label: string }[]; history: unknown[]; project_seal_signature_id?: number }>(`/documents/${id}/signoffs`),
        request<{ id: number; name: string }[]>('/signatures/me'),
      ])
      const choices = [...signatures]
      const sealId = signoffs.project_seal_signature_id || 0
      if (sealId && !choices.some((s) => s.id === sealId)) choices.push({ id: sealId, name: '本项目机构章（需授权密码）' })
      this.setData({
        showSign: true,
        signDocId: id,
        signDocTitle: String(doc?.title || doc?.doc_type || `文书 #${id}`),
        signSlots: signoffs.slots || [],
        signatures: choices,
        signSlotIndex: 0,
        signSignatureIndex: 0,
        signPassword: '',
        projectSealId: sealId,
      })
    } catch (error) {
      wx.showToast({ title: (error as Error).message, icon: 'none' })
    }
  },

  closeSign() { this.setData({ showSign: false, signPassword: '' }) },
  onSignSlot(event: WechatMiniprogram.PickerChange) { this.setData({ signSlotIndex: Number(event.detail.value) }) },
  onSignSignature(event: WechatMiniprogram.PickerChange) { this.setData({ signSignatureIndex: Number(event.detail.value) }) },
  onSignPassword(event: WechatMiniprogram.Input) { this.setData({ signPassword: event.detail.value }) },

  async confirmSign() {
    if (this.data.signing) return
    const id = this.data.signDocId
    const slot = this.data.signSlots[this.data.signSlotIndex]
    const sig = this.data.signatures[this.data.signSignatureIndex]
    if (!id || !slot || !sig || !this.data.signPassword) {
      wx.showToast({ title: '请选择签认位置和签章并输入密码', icon: 'none' })
      return
    }
    this.setData({ signing: true })
    try {
      await request(`/documents/${id}/sign`, { method: 'POST', data: { slot: slot.key, signature_id: sig.id, password: this.data.signPassword } })
      this.closeSign()
      wx.showToast({ title: '签认已完成', icon: 'success' })
      this.loadDocs()
    } catch (error) {
      wx.showModal({ title: '签认失败', content: (error as Error).message, showCancel: false })
    } finally {
      this.setData({ signing: false, signPassword: '' })
    }
  },

  uploadPersonalSignature() {
    if (!this.data.signPassword || this.data.signPassword.length < 6) {
      wx.showToast({ title: '先输入至少6位签章密码', icon: 'none' })
      return
    }
    const id = this.data.signDocId
    wx.chooseMessageFile({
      count: 1,
      type: 'file',
      extension: ['png'],
      success: (res) => {
        const file = res.tempFiles[0]
        if (!file) return
        wx.uploadFile({
          url: `${config.apiBaseUrl}/signatures/upload`,
          filePath: file.path,
          name: 'file',
          header: { Authorization: `Bearer ${session.getToken()}` },
          formData: { name: '个人签名', password: this.data.signPassword },
          success: async (response) => {
            try {
              const value = JSON.parse(response.data) as { detail?: string }
              if (response.statusCode !== 200) throw new Error(typeof value.detail === 'string' ? value.detail : '签名上传失败')
              wx.showToast({ title: '个人签名已保存', icon: 'success' })
              await this.openSignFor(id)
            } catch (error) {
              wx.showToast({ title: (error as Error).message, icon: 'none' })
            }
          },
          fail: () => wx.showToast({ title: '上传失败，请重试', icon: 'none' }),
        })
      },
    })
  },

  // ==================== 台账 ====================

  ensureLedger() {
    if (!this.data.ledgerTypes.length) {
      this.loadTypes()
    } else {
      this.reloadLedger()
    }
  },

  async loadTypes() {
    try {
      const result = await request<{ types: Record<string, string> }>('/ledger/types')
      const types = Object.entries(result.types || {}).map(([key, label]) => ({ key, label }))
      if (!types.length) return
      this.setData({ ledgerTypes: types })
      this.reloadLedger()
    } catch (error) {
      wx.showToast({ title: `加载台账类型失败：${(error as Error).message}`, icon: 'none' })
    }
  },

  selectLedgerType(event: WechatMiniprogram.TouchEvent) {
    const index = Number(event.currentTarget.dataset.index)
    if (index === this.data.ledgerIndex) return
    this.setData({ ledgerIndex: index, items: [], page: 1, hasMore: false })
    this.reloadLedger()
  },

  async reloadLedger() {
    this.setData({ loading: true })
    try {
      const records = await this.fetchLedgerPage(1)
      this.setData({
        items: records,
        empty: records.length === 0,
        hasMore: records.length === this.data.pageSize,
        page: 1,
      })
    } catch (error) {
      wx.showToast({ title: (error as Error).message, icon: 'none' })
    } finally {
      this.setData({ loading: false })
    }
  },

  async loadMoreLedger() {
    if (this.data.loading || this.data.loadingMore || !this.data.hasMore) return
    this.setData({ loadingMore: true })
    try {
      const records = await this.fetchLedgerPage(this.data.page + 1)
      this.setData({
        items: [...this.data.items, ...records],
        hasMore: records.length === this.data.pageSize,
        page: this.data.page + 1,
      })
    } catch (error) {
      wx.showToast({ title: (error as Error).message, icon: 'none' })
    } finally {
      this.setData({ loadingMore: false })
    }
  },

  buildLedgerQuery(page: number): string {
    const type = this.data.ledgerTypes[this.data.ledgerIndex]?.key
    const params: string[] = [`ledger_type=${encodeURIComponent(type)}`]
    if (this.data.ledgerStartDate) params.push(`start_date=${encodeURIComponent(this.data.ledgerStartDate)}`)
    if (this.data.ledgerEndDate) params.push(`end_date=${encodeURIComponent(this.data.ledgerEndDate)}`)
    if (this.data.ledgerSection) params.push(`section=${encodeURIComponent(this.data.ledgerSection)}`)
    if (this.data.ledgerKeyword) params.push(`keyword=${encodeURIComponent(this.data.ledgerKeyword)}`)
    params.push(`page=${page}`)
    params.push(`page_size=${this.data.pageSize}`)
    return params.join('&')
  },

  async fetchLedgerPage(page: number): Promise<LedgerRecord[]> {
    const type = this.data.ledgerTypes[this.data.ledgerIndex]?.key
    const result = await request<{ records: LedgerRecord[]; total: number }>(
      `/ledger/list?${this.buildLedgerQuery(page)}`,
    )
    const kind: LedgerRecord['_kind'] =
      type === 'hazard' ? 'hazard' : type === IMAGE_TYPE ? 'image' : 'doc'
    const items = (result.records || []).map((item) => ({
      ...item,
      _kind: kind,
      _ledger: true,
      badge: kind === 'hazard' ? '隐患' : kind === IMAGE_TYPE ? '影像' : '台账',
      title: item.title
        || (kind === 'hazard' ? item.top1_risk || item.risk_category || '隐患记录' : `台账记录 #${item.id}`),
      image_url: absoluteMediaUrl(item.watermarked_path || item.image_path || ''),
    }))
    if (type === IMAGE_TYPE) {
      return items.map((item) => ({ ...item, created_at: item.taken_at }))
    }
    return items
  },

  // ---- 台账筛选 ----
  openFilter() {
    this.setData({ showFilter: true })
  },

  closeFilter() {
    this.setData({ showFilter: false })
  },

  onLedgerInput(event: WechatMiniprogram.Input) {
    const field = event.currentTarget.dataset.field as string
    this.setData({ [field]: event.detail.value })
  },

  onStartDateChange(event: WechatMiniprogram.PickerChange) {
    this.setData({ ledgerStartDate: String(event.detail.value) })
  },

  onEndDateChange(event: WechatMiniprogram.PickerChange) {
    this.setData({ ledgerEndDate: String(event.detail.value) })
  },

  applyLedgerFilter() {
    const activeFilterCount = [
      this.data.ledgerStartDate,
      this.data.ledgerEndDate,
      this.data.ledgerSection,
      this.data.ledgerKeyword,
    ].filter(Boolean).length
    this.setData({ showFilter: false, activeFilterCount })
    this.reloadLedger()
  },

  resetLedgerFilter() {
    this.setData({
      ledgerKeyword: '',
      ledgerSection: '',
      ledgerStartDate: '',
      ledgerEndDate: '',
      activeFilterCount: 0,
      keyword: '',
    })
  },

  // ---- 台账导出 ----
  async exportLedger(event: WechatMiniprogram.TouchEvent) {
    const format = String(event.currentTarget.dataset.format || 'excel') as 'excel' | 'pdf'
    if (this.data.exporting) return
    const type = this.data.ledgerTypes[this.data.ledgerIndex]?.key
    if (!type) return
    this.setData({ exporting: true, exportFormat: format })
    wx.showLoading({ title: '正在导出', mask: true })
    try {
      const params: string[] = [`ledger_type=${encodeURIComponent(type)}`, `format=${format}`]
      if (this.data.ledgerStartDate) params.push(`start_date=${encodeURIComponent(this.data.ledgerStartDate)}`)
      if (this.data.ledgerEndDate) params.push(`end_date=${encodeURIComponent(this.data.ledgerEndDate)}`)
      if (this.data.ledgerSection) params.push(`section=${encodeURIComponent(this.data.ledgerSection)}`)
      const result = await request<{ filename: string; total: number; download_url?: string }>(
        `/ledger/export?${params.join('&')}`,
      )
      wx.hideLoading()
      await this.downloadLedger(result.filename)
    } catch (error) {
      wx.hideLoading()
      wx.showModal({ title: '导出失败', content: (error as Error).message, showCancel: false })
    } finally {
      this.setData({ exporting: false })
    }
  },

  downloadLedger(filename: string) {
    const token = session.getToken()
    wx.showLoading({ title: '下载中', mask: true })
    wx.downloadFile({
      url: `${config.apiBaseUrl}/ledger/download/${encodeURIComponent(filename)}`,
      header: token ? { Authorization: `Bearer ${token}` } : {},
      success: (res) => {
        wx.hideLoading()
        if (res.statusCode !== 200) {
          wx.showToast({ title: '下载失败', icon: 'none' })
          return
        }
        const ext = (filename.split('.').pop() || '').toLowerCase()
        const fileType = ext === 'pdf' ? 'pdf' : 'xlsx'
        wx.openDocument({
          filePath: res.tempFilePath,
          fileType,
          showMenu: true,
          fail: () => wx.showToast({ title: '无法打开文件', icon: 'none' }),
        })
      },
      fail: () => {
        wx.hideLoading()
        wx.showToast({ title: '下载失败', icon: 'none' })
      },
    })
  },

  // ---- 台账列表点击 ----

  /** 台账记录路由：隐患→隐患详情；影像→预览；文书类→文书详情。
   * 隐患/影像的 id 与文书 id 是两套独立序列，走文书详情会串号，必须分流。 */
  routeLedgerRecord(record: LedgerRecord, event?: WechatMiniprogram.TouchEvent) {
    const id = Number(record.id)
    if (record._kind === 'hazard') {
      wx.navigateTo({ url: `/pages/hazards/index?id=${id}` })
      return
    }
    if (record.image_url) {
      if (event) {
        this.preview(event)
      } else {
        wx.previewImage({ urls: [record.image_url] })
      }
      return
    }
    if (record._kind === 'image') {
      wx.showToast({ title: '该影像暂无图片', icon: 'none' })
      return
    }
    wx.navigateTo({ url: `/pages/documents/detail?id=${id}` })
  },

  /** 带图台账卡片点击 */
  onCardTap(event: WechatMiniprogram.TouchEvent) {
    const id = Number(event.currentTarget.dataset.id || 0)
    if (!id) return
    const record = this.data.items.find((item) => Number(item.id) === id)
    if (!record) return
    this.routeLedgerRecord(record as unknown as LedgerRecord, event)
  },

  preview(event: WechatMiniprogram.TouchEvent) {
    const current = String(event.currentTarget.dataset.url || '')
    const urls = this.data.items.map((item) => String(item.image_url || '')).filter(Boolean)
    if (!urls.length) return
    wx.previewImage({ current: current || urls[0], urls })
  },

  openDoc(event: WechatMiniprogram.TouchEvent) {
    const id = Number(event.currentTarget.dataset.id || 0)
    if (!id) return
    wx.navigateTo({ url: `/pages/documents/detail?id=${id}` })
  },

  openHazard(event: WechatMiniprogram.TouchEvent) {
    const id = Number(event.currentTarget.dataset.id || 0)
    if (!id) return
    wx.navigateTo({ url: `/pages/hazards/index?id=${id}` })
  },
}))