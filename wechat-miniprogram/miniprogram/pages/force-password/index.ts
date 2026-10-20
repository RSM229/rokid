import { setPassword } from '../../services/auth'
import { session } from '../../services/session'
import { themed } from '../../utils/theme'

/**
 * 强制改密 —— 管理员把账号重置为一次性临时口令后，用户须先设置自己的密码。
 * 小程序端账号必已绑定微信，故本人的新密码设置免旧密码校验。
 */
Page(themed({
  data: {
    newPassword: '',
    confirmPassword: '',
    saving: false,
    error: '',
  },

  onLoad() {
    if (!session.getToken()) {
      wx.reLaunch({ url: '/pages/login/index' })
      return
    }
    const user = session.getUser()
    if (!user?.must_change_password || session.isForcePasswordSkipped()) {
      wx.reLaunch({ url: '/pages/agent/index' })
    }
  },

  onInput(event: WechatMiniprogram.Input) {
    const field = event.currentTarget.dataset.field as 'newPassword' | 'confirmPassword'
    this.setData({ [field]: event.detail.value, error: '' })
  },

  async submit() {
    if (this.data.saving) return
    if (this.data.newPassword.length < 8) return this.setData({ error: '新密码至少 8 位' })
    if (this.data.newPassword !== this.data.confirmPassword) {
      return this.setData({ error: '两次输入的密码不一致' })
    }
    this.setData({ saving: true, error: '' })
    try {
      await setPassword(this.data.newPassword)
      session.clearMustChangePassword()
      wx.showToast({ title: '密码设置成功', icon: 'success' })
      setTimeout(() => wx.reLaunch({ url: '/pages/agent/index' }), 600)
    } catch (error) {
      this.setData({ saving: false, error: (error as Error).message })
    }
  },

  logout() {
    session.clear()
    wx.reLaunch({ url: '/pages/login/index' })
  },

  /** 跳过改密：建议但不强制，之后可随时在「我的 → 账号安全」里设置 */
  skip() {
    const user = session.getUser()
    if (user) session.markForcePasswordSkipped(user.user_id)
    wx.reLaunch({ url: '/pages/agent/index' })
  },
}))
