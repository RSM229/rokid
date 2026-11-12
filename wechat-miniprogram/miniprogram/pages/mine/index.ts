import { request } from '../../services/request'
import { session } from '../../services/session'
import { uploadQueue } from '../../services/uploadQueue'
import { getSubscribeStatus, NOTICE_TEMPLATE_KEYS } from '../../services/notice'
import type { UserInfo } from '../../types/index'
import { themed } from '../../utils/theme'

Page(themed({
  data: {
    user: {} as UserInfo | Record<string, unknown>,
    pendingUploads: [] as Array<{ id: string; label: string; createdAt: string; lastError?: string }>,
    retryingUploads: false,
    // 通知订阅进度：只用于本页「设置」入口的副提示（x/y），
    // 详细的订阅状态与授权操作已迁到二级页 pages/settings
    noticeTotal: Object.keys(NOTICE_TEMPLATE_KEYS).length,
    noticeSubscribed: 0,
    // 待补全草稿数量（聊天即操作的半成品，落库持久化、跨会话可见）
    draftCount: 0,
  },

  onShow() {
    if (!session.getToken()) {
      wx.reLaunch({ url: '/pages/login/index' })
      return
    }
    this.setData({ user: session.getUser() || {} })
    this.loadProfile()
    this.refreshUploads()
    this.loadDrafts()
    void this.loadNoticeStatus()
  },

  /** 拉取通知订阅进度，用于「设置」入口卡副提示（x/y）；订阅本身在二级页操作 */
  async loadNoticeStatus() {
    const status = await getSubscribeStatus()
    const subscribed = Object.values(status).filter(Boolean).length
    this.setData({ noticeSubscribed: subscribed })
  },

  refreshUploads() {
    const labels: Record<string, string> = {
      image: '照片',
      video: '视频',
      audio: '录音（请在对话页重试）',
      work: '工作记录',
    }
    this.setData({ pendingUploads: uploadQueue.list().map(item => ({
      id: item.id,
      label: labels[item.mediaType] || '待上传资料',
      createdAt: item.createdAt,
      lastError: item.lastError,
    })) })
  },

  async retryUploads() {
    if (this.data.retryingUploads) return
    this.setData({ retryingUploads: true })
    try {
      const result = await uploadQueue.retryAll(true)
      wx.showToast({ title: `成功${result.succeeded}，待处理${result.failed}`, icon: 'none' })
    } catch (error) {
      wx.showToast({ title: (error as Error).message, icon: 'none' })
    } finally {
      this.setData({ retryingUploads: false })
      this.refreshUploads()
    }
  },

  cancelUpload(event: WechatMiniprogram.TouchEvent) {
    const id = String(event.currentTarget.dataset.id || '')
    wx.showModal({
      title: '取消待上传资料',
      content: '取消后将移除这份本地待上传副本。已经提交到服务器的资料不会被删除。',
      success: async (result) => {
        if (!result.confirm) return
        await uploadQueue.remove(id)
        this.refreshUploads()
      },
    })
  },



  /** 项目看板：2026-10-08 从底部 tab 迁入本页，用 navigateTo 保证返回能回到「我的」 */
  openDashboard() {
    wx.navigateTo({ url: '/pages/dashboard/index' })
  },

  /** 加入项目：填写项目负责人发放的项目邀请码，加入项目并获取岗位 */
  openJoinProject() {
    wx.navigateTo({ url: '/pages/join-project/index' })
  },

  /**
   * 账号安全（二级页）：2026-10-12 从 settings 再拆出独立页。
   * 身份信息 / 微信绑定 / 登录凭据走这里；主题、通知等通用配置走设置。
   */
  openAccountSecurity() {
    wx.navigateTo({ url: '/pages/account-security/index' })
  },

  /**
   * 设置（二级页）：2026-10-12 拆出。
   * 通用设置（主题配色 / 眼镜端扫码登录）、通知与权限、关于整体迁入 pages/settings。
   * 用 navigateTo —— 「我的」是底栏页（页面栈恒 1 层），压栈后二级页的返回键能正常退回本页。
   */
  openSettings() {
    wx.navigateTo({ url: '/pages/settings/index' })
  },

  /**
   * 扫一扫登录网页端：扫 web 登录二维码 → 核对校验码 → 确认绑定。
   * 超管在小程序登录阶段已被拦截，所以这里天然只服务项目级/管理员账号。
   */
  async scanWebLogin() {
    let scanned: { result?: string }
    try {
      scanned = await new Promise<{ result?: string }>((resolve, reject) => {
        wx.scanCode({
          scanType: ['qrCode'],
          success: (res) => resolve(res as { result?: string }),
          fail: reject,
        })
      })
    } catch (error: any) {
      if (String(error?.errMsg || '').includes('cancel')) return
      wx.showToast({ title: '无法打开扫一扫', icon: 'none' })
      return
    }

    const raw = String(scanned.result || '').trim()
    const matched = /^GDJL-WEB-LOGIN:([A-Za-z0-9_-]{16,128}):([A-Z0-9]{4})$/.exec(raw)
    if (!matched) {
      wx.showToast({ title: '请扫描网页端登录二维码', icon: 'none' })
      return
    }
    const ticket = matched[1]
    const code = matched[2]

    try {
      const info = await request<{
        code: string
        client_label?: string
        ip_masked?: string
      }>('/auth/scan-login/lookup', { method: 'POST', data: { ticket } })

      const confirmed = await new Promise<boolean>((resolve) => {
        wx.showModal({
          title: '登录网页端',
          content: `校验码：${info.code}\n浏览器：${info.client_label || '未知'}${
            info.ip_masked ? `\n来自：${info.ip_masked}` : ''
          }\n\n确认是你本人在登录网页端吗？不是你本人请取消。`,
          confirmText: '确认登录',
          cancelText: '取消',
          success: (res) => resolve(Boolean(res.confirm)),
          fail: () => resolve(false),
        })
      })
      if (!confirmed) return

      await request<{ ok: boolean }>('/auth/scan-login/confirm', {
        method: 'POST',
        data: { ticket, code },
      })
      wx.showToast({ title: '网页端已登录', icon: 'success' })
    } catch (error: any) {
      wx.showToast({ title: error?.message || '扫码登录失败', icon: 'none' })
    }
  },

  /** AI 记忆：管理跨会话生效的用户工作规则 */
  openPreferences() {
    wx.navigateTo({ url: '/pages/preferences/index' })
  },
  /** 项目记忆：本项目总监维护、全项目共享的统一口径 */
  openProjectMemory() {
    wx.navigateTo({ url: '/pages/project-memory/index' })
  },

  async loadProfile() {
    try {
      const me = await request<Record<string, unknown>>('/auth/me')
      this.setData({ user: me })
    } catch {
      // 静默失败，使用缓存数据
    }
  },

  /** 拉取「待补全草稿」数量，用于「我的」页入口红点/计数 */
  async loadDrafts() {
    try {
      const res = await request<{ count: number; drafts: unknown[] }>('/agent/drafts')
      this.setData({ draftCount: res.count || 0 })
    } catch {
      this.setData({ draftCount: 0 })
    }
  },

  /** 进入「待补全草稿」列表页 */
  openDrafts() {
    wx.navigateTo({ url: '/pages/drafts/index' })
  },

  goLogin() {
    wx.reLaunch({ url: '/pages/login/index' })
  },


  logout() {
    wx.showModal({
      title: '确认退出',
      content: '退出后需要重新登录',
      confirmText: '退出',
      cancelText: '取消',
      success(res) {
        if (res.confirm) {
          session.clear()
          wx.reLaunch({ url: '/pages/login/index' })
        }
      },
    })
  },
}))
