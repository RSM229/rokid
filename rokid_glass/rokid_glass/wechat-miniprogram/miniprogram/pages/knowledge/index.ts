import { request } from '../../services/request'
import { session } from '../../services/session'
import { config } from '../../config'
import { themed } from '../../utils/theme'

interface SearchItem {
  name?: string
  title?: string
  filename?: string
  category?: string
  snippet?: string
  source_file?: string
}

interface KgStats {
  total_entities: number
  total_relations: number
  entity_types: Record<string, number>
}

interface AcceptanceGuide {
  keyword?: string
  acceptance_type?: string
  title?: string
  steps?: string[]
  key_points?: string[]
  common_violations?: string[]
  regulation_basis?: string[]
  source?: string
  template_id?: number
  llm_error?: string
}

const ACCEPTANCE_SUGGESTIONS = [
  '地基与基础分部验收',
  '主体结构验收',
  '防水工程验收',
  '装饰装修验收',
  '机电安装验收',
  '钢结构验收',
  '消防工程验收',
  '室外工程验收',
]

const FALLBACK_CATEGORIES = [
  '省统表',
  '安全监理用表',
  '质量验收规范',
  '安全管理规范',
  '施工技术规范',
  '材料规范',
  '消防/给排水/人防规范',
  '地方专属规范',
]

Page(themed({
  data: {
    mode: 'search' as 'search' | 'graph' | 'acceptance',
    // 知识检索
    keyword: '',
    categoryIndex: 0,
    categories: ['全部'] as string[],
    searching: false,
    searchResults: [] as SearchItem[],
    searchHint: '',
    // 知识图谱
    kgStats: {} as KgStats,
    kgEntityTypeCount: 0,
    // 验收流程指引
    acceptanceKeyword: '',
    acceptanceSuggestions: ACCEPTANCE_SUGGESTIONS,
    acceptanceSearching: false,
    acceptanceGuide: null as AcceptanceGuide | null,
    acceptanceHint: '',
  },

  async onLoad() {
    if (!session.getToken()) {
      wx.reLaunch({ url: '/pages/login/index' })
      return
    }
    await this.loadCategories()
    await this.loadDocs()
  },

  goMyKb() {
    wx.navigateTo({ url: '/pages/kb/index' })
  },

  onModeChange(event: WechatMiniprogram.TouchEvent) {
    const mode = event.currentTarget.dataset.mode as 'search' | 'graph' | 'acceptance'
    if (!['search', 'graph', 'acceptance'].includes(mode)) return
    this.setData({ mode })
    if (mode === 'graph') this.loadKgStats()
  },

  // ===== 知识检索 =====
  async loadCategories() {
    try {
      const result = await request<{ categories: Array<{ category: string; count: number }> }>('/knowledge/regulations/categories')
      const cats = (result.categories || []).map((item) => item.category)
      if (cats.length) {
        this.setData({ categories: ['全部', ...cats] })
      }
    } catch {
      this.setData({ categories: ['全部', ...FALLBACK_CATEGORIES] })
    }
  },

  onCategoryChange(event: WechatMiniprogram.PickerChange) {
    const index = Number(event.detail.value)
    this.setData({ categoryIndex: index })
    this.loadDocs(this.data.categories[index])
  },

  async loadDocs(category?: string) {
    const selected = category ?? this.data.categories[this.data.categoryIndex]
    const query = selected && selected !== '全部' ? `?category=${encodeURIComponent(selected)}` : ''
    this.setData({ searching: true, searchHint: '' })
    try {
      const result = await request<{ total: number; results: SearchItem[] }>(`/knowledge/regulations/list${query}`)
      const items = (result.results || []).map((item) => ({ ...item, snippet: item.snippet || '' }))
      this.setData({
        searchResults: items,
        searchHint: items.length ? `共 ${items.length} 份规范文档` : '该分类暂无文档',
      })
    } catch (error) {
      this.setData({ searchHint: `加载失败：${(error as Error).message}` })
    } finally {
      this.setData({ searching: false })
    }
  },

  onInput(event: WechatMiniprogram.Input) {
    const field = event.currentTarget.dataset.field as string
    this.setData({ [field]: event.detail.value })
  },

  async search() {
    const keyword = this.data.keyword.trim()
    if (!keyword) { await this.loadDocs(); return }
    this.setData({ searching: true, searchHint: '' })
    try {
      const selected = this.data.categories[this.data.categoryIndex]
      const params: string[] = [`keyword=${encodeURIComponent(keyword)}`]
      if (selected && selected !== '全部') params.push(`category=${encodeURIComponent(selected)}`)
      const result = await request<{ total: number; results: SearchItem[] }>(`/knowledge/regulations/search?${params.join('&')}`)
      const items = (result.results || []).map((item) => ({ ...item, snippet: item.snippet || '' }))
      this.setData({ searchResults: items, searchHint: items.length ? `共 ${result.total} 条结果` : '未找到相关规范' })
    } catch (error) {
      this.setData({ searchHint: `检索失败：${(error as Error).message}` })
    } finally {
      this.setData({ searching: false })
    }
  },

  openDoc(event: WechatMiniprogram.TouchEvent) {
    const filename = String(event.currentTarget.dataset.filename || '').trim()
    const category = String(event.currentTarget.dataset.category || '').trim()
    if (!filename) return
    wx.navigateTo({ url: `/pages/knowledge/detail?filename=${encodeURIComponent(filename)}&category=${encodeURIComponent(category)}` })
  },

  downloadFile(event: WechatMiniprogram.TouchEvent) {
    const filename = String(event.currentTarget.dataset.filename || '').trim()
    const category = String(event.currentTarget.dataset.category || '').trim()
    if (!filename) return
    const token = session.getToken()
    const url = `${config.apiBaseUrl}/knowledge/regulations/download/${encodeURIComponent(filename)}?category=${encodeURIComponent(category)}`
    wx.showLoading({ title: '下载中', mask: true })
    wx.downloadFile({
      url,
      header: token ? { Authorization: `Bearer ${token}` } : {},
      success(res) {
        wx.hideLoading()
        if (res.statusCode !== 200) { wx.showToast({ title: '下载失败', icon: 'none' }); return }
        const ext = (filename.split('.').pop() || '').toLowerCase()
        const fileType = ext === 'pdf' ? 'pdf' : ext === 'xlsx' || ext === 'xls' ? 'xls' : 'doc'
        wx.openDocument({ filePath: res.tempFilePath, fileType, showMenu: true, fail() { wx.showToast({ title: '无法打开', icon: 'none' }) } })
      },
      fail() { wx.hideLoading(); wx.showToast({ title: '下载失败', icon: 'none' }) },
    })
  },

  // ===== 知识图谱 =====
  async loadKgStats() {
    try {
      const stats = await request<KgStats>('/kg/stats')
      this.setData({
        kgStats: stats,
        kgEntityTypeCount: Object.keys(stats.entity_types || {}).length,
      })
    } catch { /* ignore */ }
  },

  // ===== 验收流程指引 =====
  onAcceptanceInput(event: WechatMiniprogram.Input) {
    this.setData({ acceptanceKeyword: event.detail.value })
  },

  useAcceptanceSuggestion(event: WechatMiniprogram.TouchEvent) {
    const keyword = String(event.currentTarget.dataset.keyword || '')
    this.setData({ acceptanceKeyword: keyword })
    this.searchAcceptance()
  },

  async searchAcceptance() {
    const keyword = this.data.acceptanceKeyword.trim()
    if (!keyword) { wx.showToast({ title: '请输入验收项目名称', icon: 'none' }); return }
    this.setData({ acceptanceSearching: true, acceptanceHint: '', acceptanceGuide: null })
    try {
      const guide = await request<AcceptanceGuide>('/knowledge/acceptance-guide', {
        method: 'POST',
        data: { keyword },
        timeout: 120000,
      })
      const sourceLabel = guide.source === 'llm' ? 'AI 智能生成'
        : guide.source === 'db' ? '数据库模板'
        : guide.source === 'builtin' ? '内置模板'
        : '默认指引'
      this.setData({
        acceptanceGuide: guide,
        acceptanceHint: `来源：${sourceLabel}${guide.llm_error ? '（LLM 回退）' : ''}`,
      })
    } catch (error) {
      this.setData({ acceptanceHint: `查询失败：${(error as Error).message}` })
    } finally {
      this.setData({ acceptanceSearching: false })
    }
  },

  copyText(event: WechatMiniprogram.TouchEvent) {
    const text = String(event.currentTarget.dataset.text || '').trim()
    if (!text) return
    wx.setClipboardData({ data: text })
  },
}))
