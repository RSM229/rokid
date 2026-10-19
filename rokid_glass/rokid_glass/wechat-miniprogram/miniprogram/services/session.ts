import type { UserInfo } from '../types/index'

const TOKEN_KEY = 'gdjl_access_token'
const USER_KEY = 'gdjl_user_info'
const BIND_TOKEN_KEY = 'gdjl_bind_token'

export const session = {
  getToken(): string {
    return wx.getStorageSync(TOKEN_KEY) || ''
  },
  getUser(): UserInfo | null {
    return wx.getStorageSync(USER_KEY) || null
  },
  save(token: string, user: UserInfo): void {
    wx.setStorageSync(TOKEN_KEY, token)
    wx.setStorageSync(USER_KEY, user)
  },
  clear(): void {
    wx.removeStorageSync(TOKEN_KEY)
    wx.removeStorageSync(USER_KEY)
    wx.removeStorageSync(BIND_TOKEN_KEY)
  },
  getBindToken(): string {
    return wx.getStorageSync(BIND_TOKEN_KEY) || ''
  },
  saveBindToken(token: string): void {
    wx.setStorageSync(BIND_TOKEN_KEY, token)
  },
  /** 改密 / 设置密码成功后解除「强制改密」标记 */
  clearMustChangePassword(): void {
    const user = session.getUser()
    if (user) {
      session.save(session.getToken(), { ...user, must_change_password: false })
    }
  },
}
