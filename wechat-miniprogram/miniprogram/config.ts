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
// 必须是已备案的 HTTPS 域名。微信服务器域名**允许带端口**（官方文档：
// 「对于 https 域名，可以配置端口，如 https://myserver.com:8080，但配置后只能向
// https://myserver.com:8080 发起请求，向 myserver.com 或 :9091 请求会失败」）。
// 2026-11-12 起线上入口统一走标准 443（外网 :4432 端口映射已不可达），
// 因此后台「服务器域名」需配成不带端口的 https://www.baize.gdces.cn。
// 对应关系（三者须逐一在「小程序后台 - 开发管理 - 开发设置 - 服务器域名」配置）：
//   request / uploadFile / downloadFile 合法域名 -> https://www.baize.gdces.cn
//   socket 合法域名（wss）-> wss://www.baize.gdces.cn
// 注意：后台域名与代码里的 apiBaseUrl 必须完全一致，否则请求会被微信拦截。
const productionApiBaseUrl = trimTrailingSlash(
  ext.apiBaseUrl || 'https://www.baize.gdces.cn/api',
)
// 联调总开关：true=连本机后端（SQLite 隔离库，tmp/dev_local.py serve --port 8001），
// false=直连生产后端（生产 MySQL，真实账号与微信绑定关系）。
// 默认 false —— IDE 模拟器不注入 ext.json（getExtConfigSync 返回空），
// 只靠 ext.useLocalBackend 关不掉本地库，模拟器会静默连到本机 SQLite；
// 用这个常量一行切换，保证模拟器与真机预览口径一致。
// 本地端口注意：8000 常被本机其他项目（Docker 容器）占用，故本地后端跑 8001。
// 正式/体验版不受影响，始终走生产。
const PREFER_LOCAL_BACKEND = false
const useLocalBackend =
  currentEnv === 'develop' && PREFER_LOCAL_BACKEND && ext.useLocalBackend !== false
const apiBaseUrl = trimTrailingSlash(
  useLocalBackend
    ? ext.localApiBaseUrl || 'http://127.0.0.1:8001/api'
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
  // 模拟身份必须显式开启，且仅限本地后端联调时使用（铁律：mock 仅限本地）。
  // 当前 PREFER_LOCAL_BACKEND=false 时 useLocalBackend 恒 false，故直连生产时 mock 一律关闭。
  mockWechatLogin:
    useLocalBackend && currentEnv === 'develop' && ext.mockWechatLogin === true,
  mockWechatCode: ext.mockWechatCode || 'mock:demo-device',
}

export function absoluteMediaUrl(path: string): string {
  if (!path) return ''
  // 已是完整 URL（可能带着历史缓存的旧/缺失 token）：刷新受控路径上的 access_token，其余原样
  if (/^https?:\/\//.test(path)) return refreshMediaToken(path)
  const base = config.apiBaseUrl.replace(/\/api$/, '')
  const url = base + path
  // 🔴 生产 /uploads 是受控端点（b206ae3 安全加固：必须登录）。
  // 小程序 <image>/wx.previewImage 只能带 query、带不了 Authorization 头，
  // 否则图片被 401 挡掉 → 看不到图（2026-11-04 用户反馈「又看不到图片」）。
  // 这里给受控路径补上当前 access_token。懒读 storage 避免与 session.ts 循环依赖。
  if (path.startsWith('/uploads/') || path.startsWith('/exports/')) {
    const token = wx.getStorageSync(`gdjl_${storageScope}_access_token`) || ''
    if (token) return `${url}${url.includes('?') ? '&' : '?'}access_token=${encodeURIComponent(token)}`
  }
  return url
}

/** 历史缓存的图片 URL 可能带着旧 token（过期/重登后失效）：把受控路径上的
 *  access_token 参数刷新为当前 token，其余原样返回。 */
export function refreshMediaToken(url: string): string {
  if (!url) return ''
  if (!/^https?:\/\//.test(url)) return url
  const token = wx.getStorageSync(`gdjl_${storageScope}_access_token`) || ''
  const clean = url.replace(/[?&]access_token=[^&]*/g, '')
  if (!token || !(/^https?:\/\//.test(clean) && (clean.includes('/uploads/') || clean.includes('/exports/')))) {
    return clean
  }
  return `${clean}${clean.includes('?') ? '&' : '?'}access_token=${encodeURIComponent(token)}`
}
