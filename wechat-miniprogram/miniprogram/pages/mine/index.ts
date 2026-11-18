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
    // 派单入口门控：仅持 task_assign 权限（总监/总代等）显示。
    // permissions 来自 /auth/me（get_current_user 合成的有效权限集），缓存兜底 session.getUser()
    canDispatch: false,
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

  /** /auth/me 返回后更新派单入口门控（loadProfile 成功路径里调用） */
  refreshCanDispatch() {
    const perms = (this.data.user as UserInfo).permissions || []
    this.setData({ canDispatch: perms.includes('task_assign') })
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

  /** AI 记忆：管理跨会话生效的用户工作规则 */
  openPreferences() {
    wx.navigateTo({ url: '/pages/preferences/index' })
  },

  /** 项目记忆：本项目总监维护、全项目共享的统一口径 */
  openProjectMemory() {
    wx.navigateTo({ url: '/pages/project-memory/index' })
  },

  /**
   * 派单入口（2026-11-18 方案②）：跳 agent 对话页并带 dispatch=1。
   * 页面加载后注入一条派单引导消息（AI 带用户口述派单），不弹独立表单——
   * 与 2026-11-16「卡片是对话入口、不跳表单页」的产品口径一致。
   */
  openDispatch() {
    wx.navigateTo({ url: '/pages/agent/index?dispatch=1' })
  },

  async loadProfile() {
    try {
      const me = await request<Record<string, unknown>>('/auth/me')
      this.setData({ user: me })
      this.refreshCanDispatch()
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
