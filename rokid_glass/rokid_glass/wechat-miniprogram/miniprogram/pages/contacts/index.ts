import { rtc, RtcContact, RtcCall, isActiveCall } from '../../services/rtc'
import { session } from '../../services/session'

Page({
  data: { contacts: [] as RtcContact[], active: null as RtcCall | null,
    error: '', loading: false, busy: false, configured: false, more: false },
  onShow() {
    if (!session.getToken()) { wx.reLaunch({ url: '/pages/login/index' }); return }
    void this.load()
  },
  async load() {
    this.setData({ loading: true, error: '' })
    try {
      const [status, contacts, calls] = await Promise.all([rtc.status(), rtc.users(), rtc.calls()])
      this.setData({ configured: status.configured, contacts, more: contacts.length === 100,
        active: calls.find(isActiveCall) || null,
        error: status.configured ? '' : '音视频服务尚未配置，请联系管理员' })
    } catch (e) { this.setData({ error: (e as Error).message }) }
    finally { this.setData({ loading: false }) }
  },
  async loadMore() {
    if (this.data.loading || !this.data.more) return
    this.setData({ loading: true })
    try {
      const next = await rtc.users(this.data.contacts.length)
      this.setData({ contacts: this.data.contacts.concat(next), more: next.length === 100 })
    } catch (e) { this.setData({ error: (e as Error).message }) }
    finally { this.setData({ loading: false }) }
  },
  // 音视频通话页（pages/call）已于 2026-09-30 移除，这里保留入口位但不发起通话。
  resume() {
    wx.showToast({ icon: 'none', title: '音视频通话已下线' })
  },
  call() {
    wx.showToast({ icon: 'none', title: '音视频通话已下线' })
  },
})
