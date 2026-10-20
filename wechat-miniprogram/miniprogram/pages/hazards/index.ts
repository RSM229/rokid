import { absoluteMediaUrl } from '../../config'
import { request } from '../../services/request'
import { getCached, CACHE_KEYS, invalidateCache } from '../../services/cache'
import { requireLogin } from '../../utils/guard'
import { themed } from '../../utils/theme'

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
  rectification_image_path?: string
  rectification_image_url?: string
  evidence?: string[]
  recheck_suggestion?: string[]
  evidence_text?: string
  created_at?: string
  expanded?: boolean
  detailLoading?: boolean
  detail?: HazardDetail
}

interface HazardMediaItem {
  id: number
  media_type: string
  path?: string
  url?: string
  created_at?: string
}

interface HazardDetail {
  top1_risk?: string
  risk_status?: string
  risk_category?: string
  evidence?: string[]
  recheck_suggestion?: string[]
  remark?: string
  rectification_doc_id?: number | null
  rectification_doc_title?: string | null
  rectification_doc_status?: string | null
  deadline?: string | null
  rectified_at?: string | null
  recheck_passed?: boolean | null
  recheck_remark?: string | null
  recheck_at?: string | null
  media?: HazardMediaItem[]
}

Page(themed({
  data: {
    hazards: [] as HazardItem[],
    loading: false,
    focusId: 0,
    actionId: 0,
    showDeadline: false,
    rectDeadline: '',
    rectHazardId: 0,
  },
  onLoad(options: Record<string, string>) {
    if (!requireLogin('pages/hazards/index', options)) return
    this.setData({ focusId: Number(options.id || 0) })
    this.load()
  },

  // PRD 19.1：隐患/整改任务可生成分享卡片，接收方进入后仍需登录
  onShareAppMessage(event: WechatMiniprogram.Page.IShareAppMessageOption) {
    const hazardId = Number(event.target?.dataset.id || this.data.focusId || 0)
    const hazard = this.data.hazards.find((item) => item.id === hazardId)
    const title = hazard
      ? `隐患整改：${hazard.top1_risk || hazard.risk_category || '#' + hazardId}`
      : '监理隐患整改'
    return {
      title,
      path: hazardId ? `/pages/hazards/index?id=${hazardId}` : '/pages/hazards/index',
    }
  },
  onPullDownRefresh() {
    this.load().finally(() => wx.stopPullDownRefresh())
  },
  async load() {
    this.setData({ loading: true })
    try {
      const hazards = await getCached<HazardItem[]>(
        CACHE_KEYS.HAZARD_LIST,
        () => request<HazardItem[]>('/hazard/list'),
      )
      const items = hazards.map((item) => ({
        ...item,
        expanded: false,
        image_url: absoluteMediaUrl(item.image_path || ''),
        rectification_image_url: absoluteMediaUrl(item.rectification_image_path || ''),
        evidence_text: (item.evidence || []).join('；'),
      }))
      if (this.data.focusId) {
        items.sort((a, b) => Number(b.id === this.data.focusId) - Number(a.id === this.data.focusId))
      }
      this.setData({ hazards: items })
    } catch (error) {
      wx.showToast({ title: (error as Error).message, icon: 'none' })
    } finally {
      this.setData({ loading: false })
    }
  },
  preview(event: WechatMiniprogram.TouchEvent) {
    const current = event.currentTarget.dataset.url as string
    const id = Number(event.currentTarget.dataset.id || 0)
    const item = this.data.hazards.find((hazard) => hazard.id === id)
    const urls = [
      item?.image_url,
      item?.rectification_image_url,
      ...(item?.detail?.media || []).map((media) => media.url || ''),
    ].filter((url): url is string => Boolean(url))
    if (current) wx.previewImage({ current, urls })
  },
  async toggleDetail(event: WechatMiniprogram.TouchEvent) {
    const id = Number(event.currentTarget.dataset.id)
    const current = this.data.hazards.find((item) => item.id === id)
    if (!current) return
    if (current.expanded) {
      this.setData({
        hazards: this.data.hazards.map((item) =>
          item.id === id ? { ...item, expanded: false } : item,
        ),
      })
      return
    }
    this.setData({
      hazards: this.data.hazards.map((item) =>
        item.id === id ? { ...item, expanded: true, detailLoading: true } : item,
      ),
    })
    try {
      const res = await request<HazardDetail>(`/hazard/${id}`)
      const detail: HazardDetail = {
        ...res,
        media: (res.media || []).map((media) => ({
          ...media,
          url: absoluteMediaUrl(media.path || ''),
        })),
      }
      this.setData({
        hazards: this.data.hazards.map((item) =>
          item.id === id ? { ...item, detail, detailLoading: false } : item,
        ),
      })
    } catch (error) {
      this.setData({
        hazards: this.data.hazards.map((item) =>
          item.id === id ? { ...item, expanded: false, detailLoading: false } : item,
        ),
      })
      wx.showToast({ title: (error as Error).message, icon: 'none' })
    }
  },
  openDoc(event: WechatMiniprogram.TouchEvent) {
    const docId = Number(event.currentTarget.dataset.id)
    if (docId) wx.navigateTo({ url: `/pages/documents/detail?id=${docId}` })
  },
  async createRectification(event: WechatMiniprogram.TouchEvent) {
    const hazardId = Number(event.currentTarget.dataset.id)
    if (this.data.actionId || this.data.showDeadline) return
    const d = new Date(Date.now() + 7 * 86400000)
    const pad = (n: number) => (n < 10 ? `0${n}` : String(n))
    this.setData({
      rectHazardId: hazardId,
      rectDeadline: `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`,
      showDeadline: true,
    })
  },
  closeDeadline() {
    this.setData({ showDeadline: false })
  },
  onDeadlineChange(event: WechatMiniprogram.PickerChange) {
    this.setData({ rectDeadline: String(event.detail.value) })
  },
  async confirmRect() {
    const hazardId = this.data.rectHazardId
    if (!hazardId || this.data.actionId) return
    this.setData({ actionId: hazardId, showDeadline: false })
    try {
      const query = this.data.rectDeadline ? `?deadline=${this.data.rectDeadline}` : ''
      const res = await request<{ doc_id?: number }>(
        `/hazard/${hazardId}/create-rectification-doc${query}`,
        { method: 'POST' },
      )
      invalidateCache(CACHE_KEYS.HAZARD_LIST)
      wx.showToast({ title: '整改通知单已生成', icon: 'success' })
      await this.load()
      if (res.doc_id) wx.navigateTo({ url: `/pages/documents/detail?id=${res.doc_id}` })
    } catch (error) {
      wx.showModal({ title: '生成失败', content: (error as Error).message, showCancel: false })
    } finally {
      this.setData({ actionId: 0 })
    }
  },
  recheck(event: WechatMiniprogram.TouchEvent) {
    const hazardId = Number(event.currentTarget.dataset.id)
    const passed = event.currentTarget.dataset.passed === true
      || event.currentTarget.dataset.passed === 'true'
    wx.showModal({
      title: passed ? '复查合格' : '驳回整改',
      content: passed ? '填写复查意见（可选）' : '请填写驳回原因',
      editable: true,
      placeholderText: passed ? '复查意见' : '驳回原因',
      success: async (result) => {
        if (!result.confirm || this.data.actionId) return
        this.setData({ actionId: hazardId })
        try {
          await request(`/hazard/${hazardId}/recheck`, {
            method: 'POST',
            data: { passed, remark: result.content || '' },
          })
          invalidateCache(CACHE_KEYS.HAZARD_LIST)
          wx.showToast({ title: passed ? '隐患已闭环' : '已驳回整改', icon: 'success' })
          await this.load()
        } catch (error) {
          wx.showModal({ title: '复查失败', content: (error as Error).message, showCancel: false })
        } finally {
          this.setData({ actionId: 0 })
        }
      },
    })
  },
}))
