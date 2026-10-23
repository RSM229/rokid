import { config } from '../config'
import { session } from './session'

interface RequestOptions {
  method?: 'GET' | 'POST' | 'PUT' | 'DELETE'
  data?: WechatMiniprogram.IAnyObject
  auth?: boolean
  timeout?: number
}

export class ApiRequestError extends Error {
  statusCode?: number
  networkError: boolean
  timedOut: boolean

  constructor(message: string, options: { statusCode?: number; networkError?: boolean; timedOut?: boolean } = {}) {
    super(message)
    this.name = 'ApiRequestError'
    this.statusCode = options.statusCode
    this.networkError = options.networkError === true
    this.timedOut = options.timedOut === true
  }
}

function errorMessage(response: WechatMiniprogram.RequestSuccessCallbackResult): string {
  const data = response.data as { detail?: string; message?: string } | string
  if (typeof data === 'string') return data
  const detail = data?.detail || data?.message
  // 本地联调经典坑：127.0.0.1:8000 被其他项目占用时，请求会命中对方
  // 应用的 404（FastAPI 默认响应体 {"detail":"Not Found"}）。原样抛出用户
  // 只看到一句看不懂的英文，这里换成带排查指引的中文提示。
  if (config.useLocalBackend && response.statusCode === 404 && detail === 'Not Found') {
    return `本地后端未响应（${config.apiBaseUrl}）。请确认已启动本地后端，且 8000 端口没有被其他项目占用。`
  }
  return detail || `请求失败（${response.statusCode}）`
}

export function request<T>(
  path: string,
  options: RequestOptions = {},
): Promise<T> {
  return new Promise((resolve, reject) => {
    const token = session.getToken()
    const header: Record<string, string> = { 'Content-Type': 'application/json' }
    if (options.auth !== false && token) {
      header.Authorization = `Bearer ${token}`
    }
    wx.request({
      url: `${config.apiBaseUrl}${path}`,
      method: options.method || 'GET',
      data: options.data || {},
      header,
      timeout: options.timeout || 30000,
      success(response) {
        if (response.statusCode >= 200 && response.statusCode < 300) {
          resolve(response.data as T)
          return
        }
        if (response.statusCode === 401 && options.auth !== false) {
          const pages = getCurrentPages()
          const current = pages[pages.length - 1] as WechatMiniprogram.Page.Instance<
            WechatMiniprogram.IAnyObject,
            WechatMiniprogram.IAnyObject
          > & { route?: string; options?: Record<string, string> }
          if (current?.route && current.route !== 'pages/login/index') {
            wx.setStorageSync('gdjl_pending_route', {
              route: current.route,
              options: current.options || {},
            })
          }
          session.clear()
          wx.reLaunch({ url: '/pages/login/index' })
        }
        reject(new ApiRequestError(errorMessage(response), { statusCode: response.statusCode }))
      },
      fail(error) {
        const raw = error.errMsg || ''
        const timedOut = /timeout/i.test(raw)
        if (config.useLocalBackend && /request:fail/i.test(raw)) {
          reject(
            new ApiRequestError(
              `连不上本地后端（${config.apiBaseUrl}）。请确认后端服务已启动，并在微信开发者工具中重新编译。`,
              { networkError: true, timedOut },
            ),
          )
          return
        }
        reject(new ApiRequestError(raw || '网络请求失败', { networkError: true, timedOut }))
      },
    })
  })
}
