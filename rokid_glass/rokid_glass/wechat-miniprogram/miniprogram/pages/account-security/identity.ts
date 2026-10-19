import { request } from '../../services/request'
import { session } from '../../services/session'
import type { UserInfo } from '../../types/index'
import { themed } from '../../utils/theme'

/** 身份信息（账号安全 · 子目录）：只读展示账号资料，缓存先上屏、/auth/me 覆盖 */
Page(themed({
  data: {
    user: {} as UserInfo | Record<string, unknown>,
  },

  onLoad() {
    if (!session.getToken()) {
      wx.reLaunch({ url: '/pages/login/index' })
      return
    }
    this.setData({ user: session.getUser() || {} })
    void this.loadProfile()
  },

  async loadProfile() {
    try {
      const me = await request<Record<string, unknown>>('/auth/me')
      this.setData({ user: me })
    } catch {
      // 静默失败，使用缓存数据
    }
  },
}))
