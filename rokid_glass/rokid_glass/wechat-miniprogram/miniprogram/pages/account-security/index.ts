import { session } from '../../services/session'
import { getWechatBindingStatus } from '../../services/auth'
import type { UserInfo } from '../../types/index'
import { themed } from '../../utils/theme'

/**
 * 账号安全（目录页）· 2026-10-12 晚重构
 *
 * 用户要求本页只放三个目录（身份信息 / 微信绑定 / 登录凭据），
 * 具体内容在各子页：identity / binding / credentials。
 * 本页仅拉绑定状态给「微信绑定」行做摘要徽标；user 用登录缓存即可
 * （摘要里只显示用户名/手机号有无，精确值在身份信息子页实时拉）。
 */
Page(themed({
  data: {
    user: {} as UserInfo | Record<string, unknown>,
    wxBound: null as boolean | null,
  },

  onLoad() {
    if (!session.getToken()) {
      wx.reLaunch({ url: '/pages/login/index' })
      return
    }
  },

  onShow() {
    // 从子页（解绑 / 设密码）返回时刷新摘要
    this.setData({ user: session.getUser() || {} })
    void this.loadWechatBinding()
  },

  /** 拉取微信绑定状态：只用于本页目录行的摘要徽标，完整信息与操作在 binding 子页 */
  async loadWechatBinding() {
    try {
      const status = await getWechatBindingStatus()
      this.setData({ wxBound: !!status.bound })
    } catch {
      this.setData({ wxBound: null })
    }
  },

  openIdentity() {
    wx.navigateTo({ url: '/pages/account-security/identity' })
  },

  openBinding() {
    wx.navigateTo({ url: '/pages/account-security/binding' })
  },

  openCredentials() {
    wx.navigateTo({ url: '/pages/account-security/credentials' })
  },
}))
