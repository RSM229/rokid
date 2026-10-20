import { session } from '../services/session'

const PENDING_KEY = 'gdjl_pending_route'

/**
 * 页面前置登录校验（PRD 19.1：接收方进入分享链接后仍需登录并通过项目权限校验）。
 * 与 services/request.ts 的 401 兜底共用同一份 pending route，
 * 登录后 login 页会把用户送回原页面与原参数。
 *
 * @returns 已登录返回 true；未登录跳转登录页并返回 false
 */
export function requireLogin(route: string, options: Record<string, string> = {}): boolean {
  if (session.getToken()) return true
  wx.setStorageSync(PENDING_KEY, { route, options })
  wx.reLaunch({ url: '/pages/login/index' })
  return false
}
