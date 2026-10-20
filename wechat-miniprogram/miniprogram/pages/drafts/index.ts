import { request } from '../../services/request'
import { session } from '../../services/session'
import { themed } from '../../utils/theme'

interface DraftItem {
  id?: number
  tool: string
  label: string
  /** 可落到省统表时给出的 form_code，用于直接跳分步表单 */
  form_code?: string | null
  args: Record<string, string>
  missing: string[]
  updated_at: string
  /** 以下为前端预计算的展示字段 */
  summary?: string
  missingText?: string
  timeText?: string
}

/** 把已填参数拼成一句话摘要（取前 3 个非空值） */
function buildSummary(args: Record<string, string>): string {
  const vals = Object.values(args || {})
    .map(v => String(v || '').trim())
    .filter(Boolean)
  if (!vals.length) return '尚未填写内容'
  return vals.slice(0, 3).join(' · ')
}

/** ISO 时间 → 友好文本：今天 HH:mm / 昨天 HH:mm / MM-DD HH:mm */
function formatTime(iso: string): string {
  const d = new Date(iso)
  if (isNaN(d.getTime())) return ''
  const now = new Date()
  const pad = (n: number) => String(n).padStart(2, '0')
  const hm = `${pad(d.getHours())}:${pad(d.getMinutes())}`
  const sameDay = d.getFullYear() === now.getFullYear() && d.getMonth() === now.getMonth() && d.getDate() === now.getDate()
  if (sameDay) return `今天 ${hm}`
  const yest = new Date(now)
  yest.setDate(now.getDate() - 1)
  const isYest = d.getFullYear() === yest.getFullYear() && d.getMonth() === yest.getMonth() && d.getDate() === yest.getDate()
  if (isYest) return `昨天 ${hm}`
  return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${hm}`
}

Page(themed({
  data: {
    drafts: [] as DraftItem[],
    loading: false,
  },

  onShow() {
    if (!session.getToken()) {
      wx.reLaunch({ url: '/pages/login/index' })
      return
    }
    this.loadDrafts()
  },

  async loadDrafts() {
    this.setData({ loading: true })
    try {
      const res = await request<{ count: number; drafts: DraftItem[] }>('/agent/drafts')
      const drafts = (res.drafts || []).map(d => ({
        ...d,
        summary: buildSummary(d.args),
        missingText: (d.missing || []).join('、'),
        timeText: formatTime(d.updated_at),
      }))
      this.setData({ drafts })
    } catch {
      this.setData({ drafts: [] })
    } finally {
      this.setData({ loading: false })
    }
  },

  /** 继续填写：回到聊天页，agent 页 onShow 会拉起「待补全」提示横幅 */
  continueDraft() {
    wx.navigateTo({ url: '/pages/agent/index' })
  },

  /** 用表单填写：按字段表把已填值预填进分步表单，缺项逐项高亮 */
  useForm(event: WechatMiniprogram.TouchEvent) {
    const code = String(event.currentTarget.dataset.code || '')
    const id = Number(event.currentTarget.dataset.id || 0)
    if (!code) return
    const query = [`code=${code}`]
    if (id) query.push(`draft_id=${id}`)
    wx.navigateTo({ url: `/pages/record-form/index?${query.join('&')}` })
  },

  /** 放弃某条草稿：清掉该工具对应的待补全记录 */
  discardDraft(event: WechatMiniprogram.TouchEvent) {
    const tool = String(event.currentTarget.dataset.tool || '')
    if (!tool) return
    wx.showModal({
      title: '放弃该草稿',
      content: '放弃后这条未完成的记录将被清除，无法恢复。',
      confirmText: '放弃',
      success: async (r) => {
        if (!r.confirm) return
        try {
          await request('/agent/drafts/clear', { method: 'POST', data: { tool } })
          wx.showToast({ title: '已放弃', icon: 'none' })
          this.loadDrafts()
        } catch (e) {
          wx.showToast({ title: (e as Error).message, icon: 'none' })
        }
      },
    })
  },

  onPullDownRefresh() {
    void this.loadDrafts().then(() => wx.stopPullDownRefresh())
  },
}))
