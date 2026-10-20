import { request } from '../../services/request'
import { session } from '../../services/session'
import { config } from '../../config'
import { themed } from '../../utils/theme'

interface RegulationContent {
  name?: string
  filename?: string
  category?: string
  content?: string
  exists?: boolean
}

function normalizeParam(value: string): string {
  let out = value
  let prev = ''
  while (prev !== out) {
    prev = out
    try {
      out = decodeURIComponent(out)
    } catch {
      break
    }
  }
  return out
}

Page(themed({
  data: {
    loading: true,
    doc: null as RegulationContent | null,
    contentLines: [] as string[],
  },

  onLoad(options: Record<string, string>) {
    const filename = normalizeParam(String(options.filename || ''))
    const category = normalizeParam(String(options.category || '省统表'))
    if (!filename) {
      wx.showToast({ title: '缺少文档参数', icon: 'none' })
      wx.navigateBack()
      return
    }
    this.load(filename, category)
  },

  async load(filename: string, category: string) {
    this.setData({ loading: true })
    try {
      const doc = await request<RegulationContent>(
        `/knowledge/regulations/content?filename=${encodeURIComponent(filename)}&category=${encodeURIComponent(category)}`,
      )
      const contentLines = (doc.content || '')
        .split('\n')
        .map((line) => line || ' ')
      this.setData({ doc, contentLines, loading: false })
      this.openOriginal()
    } catch (error) {
      this.setData({ loading: false })
      wx.showModal({ title: '加载失败', content: (error as Error).message, showCancel: false })
    }
  },

  openOriginal() {
    const doc = this.data.doc
    if (!doc || !doc.filename) return
    const fileType = this.fileTypeOf(doc.filename)
    if (!fileType) return
    this.download()
  },

  download() {
    const doc = this.data.doc
    if (!doc || !doc.filename) return
    const token = session.getToken()
    const fileType = this.fileTypeOf(doc.filename)
    wx.showLoading({ title: '下载中', mask: true })
    wx.downloadFile({
      url: `${config.apiBaseUrl}/knowledge/regulations/download/${encodeURIComponent(doc.filename)}?category=${encodeURIComponent(doc.category || '省统表')}`,
      header: token ? { Authorization: `Bearer ${token}` } : {},
      success(res) {
        wx.hideLoading()
        if (res.statusCode !== 200) {
          wx.showToast({ title: '下载失败', icon: 'none' })
          return
        }
        wx.openDocument({
          filePath: res.tempFilePath,
          fileType,
          showMenu: true,
          fail() {
            wx.showToast({ title: '无法打开文件', icon: 'none' })
          },
        })
      },
      fail() {
        wx.hideLoading()
        wx.showToast({ title: '下载失败', icon: 'none' })
      },
    })
  },
  fileTypeOf(filename: string): 'doc' | 'xls' | 'pdf' | undefined {
    const ext = (filename.split('.').pop() || '').toLowerCase()
    if (ext === 'docx' || ext === 'doc') return 'doc'
    if (ext === 'xlsx' || ext === 'xls') return 'xls'
    if (ext === 'pdf') return 'pdf'
    return undefined
  },
}))