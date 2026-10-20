import { setPassword } from '../../services/auth'
import { session } from '../../services/session'
import { utf8ByteLength } from '../../utils/text'
import { themed } from '../../utils/theme'

/**
 * 登录凭据（账号安全 · 子目录）· 2026-10-12 晚
 *
 * 设置登录密码 + 密保问题入口。
 * 密码：微信一键登录的用户从未输过密码，设密后即可在电脑端（Web 管理后台）
 * 用「账号 + 密码」登录，共用同一账号与数据；已绑微信的账号免旧密码。
 */
Page(themed({
  data: {
    settingPwd: false,
  },

  onLoad() {
    if (!session.getToken()) {
      wx.reLaunch({ url: '/pages/login/index' })
      return
    }
  },

  setLoginPassword() {
    if (this.data.settingPwd) return
    wx.showModal({
      title: '设置登录密码',
      content: '设置后可在电脑端用「账号 + 密码」登录，数据与小程序同步。请输入新密码（至少 8 位）：',
      editable: true,
      placeholderText: '新密码（至少 8 位）',
      confirmText: '下一步',
      cancelText: '取消',
      success: (result) => {
        if (!result.confirm) return
        const pwd = String(result.content || '').trim()
        if (pwd.length < 8) {
          wx.showToast({ title: '密码至少 8 位', icon: 'none' })
          return
        }
        // bcrypt 上限 72 字节；此前直接把后端报错原样弹出，这里提前拦截给可读提示
        if (utf8ByteLength(pwd) > 72) {
          wx.showToast({ title: '密码过长：最多 72 字节（约 24 个汉字）', icon: 'none' })
          return
        }
        void this.doSetPassword(pwd)
      },
    })
  },

  async doSetPassword(newPassword: string) {
    this.setData({ settingPwd: true })
    try {
      const detail = await setPassword(newPassword)
      wx.showToast({ title: detail, icon: 'none', duration: 2500 })
    } catch (error) {
      wx.showToast({ title: (error as Error).message || '设置失败', icon: 'none' })
    } finally {
      this.setData({ settingPwd: false })
    }
  },

  /** 密保问题设置页（「忘记密码」自助重置的依据） */
  openSecurity() {
    wx.navigateTo({ url: '/pages/security/index' })
  },
}))
