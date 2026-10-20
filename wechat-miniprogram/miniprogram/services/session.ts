import type { UserInfo } from '../types/index'

const TOKEN_KEY = 'gdjl_access_token'
const USER_KEY = 'gdjl_user_info'
const BIND_TOKEN_KEY = 'gdjl_bind_token'
/** 「稍后设置密码」的跳过标记：记当前 user_id，本次登录会话内不再拦改密页 */
const SKIP_FORCE_PWD_KEY = 'gdjl_force_pwd_skipped_user'

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
    // 退出登录即视为会话结束：下次登录重新提示（管理员若又重置了密码也能及时看到）
    wx.removeStorageSync(SKIP_FORCE_PWD_KEY)
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
    wx.removeStorageSync(SKIP_FORCE_PWD_KEY)
  },
  /** 用户在改密页选择「稍后设置」——本次登录不再拦截，可随时去账号安全页设置 */
  markForcePasswordSkipped(userId: number | string): void {
    wx.setStorageSync(SKIP_FORCE_PWD_KEY, String(userId))
  },
  isForcePasswordSkipped(): boolean {
    const user = session.getUser()
    if (!user) return false
    return String(wx.getStorageSync(SKIP_FORCE_PWD_KEY) || '') === String(user.user_id)
  },
}
