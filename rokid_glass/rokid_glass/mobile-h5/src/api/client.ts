/**
 * 创建日期：2026-07-18
 * Axios 实例：baseURL '/api'，请求注入 Bearer token，响应 401 跳登录、统一错误提示
 */
import axios from 'axios'
import type { AxiosInstance, AxiosRequestConfig, AxiosResponse } from 'axios'
import { showFailToast } from 'vant'
import { queueRequest } from './offline'

/**
 * 响应拦截器会返回 response.data，这里同步修正实例类型，
 * 避免调用方误用 AxiosResponse。
 */
type DataAxiosInstance = Omit<AxiosInstance, 'get' | 'post' | 'put' | 'delete'> & {
  get<T = any>(url: string, config?: AxiosRequestConfig): Promise<T>
  post<T = any>(url: string, data?: unknown, config?: AxiosRequestConfig): Promise<T>
  put<T = any>(url: string, data?: unknown, config?: AxiosRequestConfig): Promise<T>
  delete<T = any>(url: string, config?: AxiosRequestConfig): Promise<T>
}

const http = axios.create({
  baseURL: '/api',
  timeout: 30000,
}) as DataAxiosInstance
let redirectingToLogin = false

// 请求拦截器：注入 Authorization
http.interceptors.request.use(async (config) => {
  const token = localStorage.getItem('token')
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  const method = (config.method || 'GET').toUpperCase()
  const url = config.url || ''
  const canQueue =
    !!token &&
    ['POST', 'PUT', 'DELETE'].includes(method) &&
    !url.startsWith('/auth/')
  if (canQueue && typeof navigator !== 'undefined' && !navigator.onLine) {
    await queueRequest(
      method as 'POST' | 'PUT' | 'DELETE',
      url,
      config.data,
    )
    config.adapter = async () =>
      ({
        data: {
          queued: true,
          offline: true,
          detail: '网络不可用，请求已保存，恢复联网后将自动同步',
        },
        status: 202,
        statusText: 'Accepted',
        headers: {},
        config,
      }) as AxiosResponse
  }
  return config
})

// 响应拦截器：返回 data，401 跳登录，统一错误提示
http.interceptors.response.use(
  (response) => response.data,
  (error) => {
    const status = error.response?.status
    const url = error.config?.url || ''

    if (status === 401 && !url.includes('/auth/login')) {
      localStorage.removeItem('token')
      localStorage.removeItem('mobileUser')
      localStorage.removeItem('mobilePermissions')
      // 使用 location 跳转，避免在拦截器中引入 router 造成循环依赖
      if (window.location.pathname !== '/login' && !redirectingToLogin) {
        redirectingToLogin = true
        window.location.replace('/login')
      }
    } else if (status >= 500) {
      showFailToast('服务器异常，请稍后重试')
    } else if (status !== 401 && status !== 403 && status !== 422) {
      const msg = error.response?.data?.detail || error.message || '请求失败'
      showFailToast(typeof msg === 'string' ? msg : '请求失败')
    }
    return Promise.reject(error)
  },
)

export default http
