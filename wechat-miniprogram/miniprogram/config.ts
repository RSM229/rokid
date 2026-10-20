interface ExtRuntimeConfig {
  apiBaseUrl?: string
  localApiBaseUrl?: string
  useLocalBackend?: boolean
  mockWechatLogin?: boolean
  mockWechatCode?: string
}

function externalConfig(): ExtRuntimeConfig {
  try {
    return (wx.getExtConfigSync?.() || {}) as ExtRuntimeConfig
  } catch {
    return {}
  }
}

function trimTrailingSlash(value: string): string {
  return value.replace(/\/+$/, '')
}

/** 当前运行环境：develop / trial / release，用于隔离开发环境与正式环境开关 */
function envVersion(): 'develop' | 'trial' | 'release' {
  try {
    return wx.getAccountInfoSync()?.miniProgram?.envVersion || 'release'
  } catch {
    return 'release'
  }
}

const ext = externalConfig()
const currentEnv = envVersion()
const isRelease = currentEnv === 'release'
// 必须是已备案的 HTTPS 域名。微信服务器域名**允许带端口**（官方文档：
// 「对于 https 域名，可以配置端口，如 https://myserver.com:8080，但配置后只能向
// https://myserver.com:8080 发起请求，向 myserver.com 或 :9091 请求会失败」），
// 因此服务器只开放 4432 时，后台必须按「带端口」原样配置，不能只填不带端口的域名。
// 对应关系（三者须逐一在「小程序后台 - 开发管理 - 开发设置 - 服务器域名」配置）：
//   request / uploadFile / downloadFile 合法域名 -> https://www.baize.gdces.cn:4432
//   socket 合法域名（wss）-> wss://www.baize.gdces.cn  （wss 域名免配端口，默认放行该域名下所有端口）
// 注意：一旦后台配了带端口域名，代码里的 apiBaseUrl 就必须带完全相同的端口，两者不一致会被拦截。
const productionApiBaseUrl = trimTrailingSlash(
  ext.apiBaseUrl || 'https://www.baize.gdces.cn:4432/api',
)
// The WeChat IDE's local simulator often has no injected ext config. Default
// develop builds to loopback so a fresh checkout cannot silently hit production.
const useLocalBackend = currentEnv === 'develop' && ext.useLocalBackend !== false
const apiBaseUrl = trimTrailingSlash(
  useLocalBackend
    ? ext.localApiBaseUrl || 'http://127.0.0.1:8000/api'
    : productionApiBaseUrl,
)
const storageScope = encodeURIComponent(apiBaseUrl)

/**
 * 部署环境优先从微信第三方平台 ext.json / ext 配置读取地址。
 * 这样开发、体验、正式版本可以使用各自域名，无需改动并重新合并源码。
 */
export const config = {
  apiBaseUrl,
  storageScope,
  useLocalBackend,
  // 正式版强制关闭 mock 登录，确保线上一定走真实 wx.login；
  // 模拟身份必须显式开启，体验版默认走真实微信登录。
  mockWechatLogin:
    !useLocalBackend && !isRelease && currentEnv === 'develop' && ext.mockWechatLogin === true,
  mockWechatCode: ext.mockWechatCode || 'mock:demo-device',
}

export function absoluteMediaUrl(path: string): string {
  if (!path) return ''
  if (/^https?:\/\//.test(path)) return path
  return config.apiBaseUrl.replace(/\/api$/, '') + path
}
