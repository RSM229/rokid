import { config } from '../config'
import { session } from './session'

interface RequestOptions {
  method?: 'GET' | 'POST' | 'PUT' | 'DELETE'
  data?: WechatMiniprogram.IAnyObject
  auth?: boolean
  timeout?: number
}

function errorMessage(response: WechatMiniprogram.RequestSuccessCallbackResult): string {
  const data = response.data as { detail?: string; message?: string } | string
  if (typeof data === 'string') return data
  return data?.detail || data?.message || `请求失败（${response.statusCode}）`
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
        reject(new Error(errorMessage(response)))
      },
      fail(error) {
        const raw = error.errMsg || ''
        // 本地联调最常见的失败：后端（本地桩 127.0.0.1:8000）没启动，
        // wx.request 直接抛 request:fail，用户只看到一行无从下手的报错。
        // 这里翻译成「怎么办」，只在开发/体验版（mock 开关打开）时提示。
        if (config.mockWechatLogin && /request:fail/i.test(raw)) {
          reject(
            new Error(
              '连不上本地联调服务（127.0.0.1:8000）。请先启动本地桩：双击 D:\\rokid\\start_mock_backend.bat，然后重新编译小程序',
            ),
          )
          return
        }
        reject(new Error(raw || '网络请求失败'))
      },
    })
  })
}
