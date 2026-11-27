import { request } from './request'

/**
 * 订阅消息模板 ID（与后端 settings.wechat_template_ids 对应）
 */
export const NOTICE_TEMPLATE_KEYS = {
  TASK_ASSIGNED: 'task_assigned',
  TASK_URGED: 'task_urged',
  DOCUMENT_APPROVED: 'document_approved',
  DOCUMENT_REJECTED: 'document_rejected',
  HAZARD_DETECTED: 'hazard_detected',
} as const

/**
 * 模板 ID 缓存（key -> tmplId）
 * 必须在用户点击事件之前预加载，因为 wx.requestSubscribeMessage
 * 要求在用户交互的同步上下文中调用，不能先做 async 请求。
 */
let _templateCache: Record<string, string> | null = null

/**
 * 订阅不可用的原因（供页面给出准确提示）：
 *   - 'not-loaded'：预加载尚未成功跑过（网络失败等），可稍后重试；
 *   - 'empty'：预加载成功但后端没有配置任何模板 ID —— 这不是「稍后再试」
 *     能解决的，需要管理员在微信公众平台申请订阅模板并配置到后端 env。
 */
export function noticeUnavailableReason(): 'not-loaded' | 'empty' | null {
  if (!_templateCache) return 'not-loaded'
  // 🔴 2026-11-26 修复：后端可能返回 5 个模板键但值全为空串（模板未在微信后台申请/
  // 未配置到 env），此时 Object.keys 非空但没有任何可用 tmplId。原实现只判 keys
  // 数量 → 返回 null → 页面落入「稍后重试」分支，误导用户反复重试。
  // 现在按「是否存在非空 tmplId」判定，全空视为 'empty'（需管理员配置，重试无用）。
  const hasId = Object.values(_templateCache).some((v) => Boolean(v))
  return hasId ? null : 'empty'
}

/**
 * 从后端预加载订阅消息模板 ID 并缓存
 * 应在 App.onLaunch 或首页 onLoad 时调用
 */
export async function preloadNoticeTemplates(): Promise<void> {
  try {
    const result = await request<{ templates: Record<string, string> }>('/wechat/notice/templates')
    _templateCache = result.templates || {}
  } catch {
    // 预加载失败保持 null：下次点击时按「未就绪，可重试」处理，
    // 而不是误判成「后端未配置模板」
    _templateCache = null
  }
}

/**
 * 向微信申请订阅消息授权（必须在用户点击事件回调中同步调用）
 *
 * 依赖 preloadNoticeTemplates 预缓存的模板 ID。
 * 如果未预加载，会跳过授权并返回 false。
 *
 * @param templateKeys 模板键名数组
 * @returns 是否有用户授权
 */
export function requestSubscribeMessage(templateKeys: string[]): boolean {
  if (!_templateCache) {
    console.warn('[notice] 模板 ID 未预加载，请先调用 preloadNoticeTemplates()')
    return false
  }

  const templateIds: string[] = []
  for (const key of templateKeys) {
    const tid = _templateCache[key]
    if (tid) templateIds.push(tid)
  }

  if (templateIds.length === 0) {
    console.warn('[notice] 未匹配到订阅消息模板 ID')
    return false
  }

  // wx.requestSubscribeMessage 必须在用户点击事件同步上下文中调用
  wx.requestSubscribeMessage({
    tmplIds: templateIds,
    success: (res) => {
      const subscribed: string[] = []
      for (const tmplId of templateIds) {
        if (res[tmplId] === 'accept') {
          subscribed.push(tmplId)
        }
      }
      if (subscribed.length > 0) {
        for (const tmplId of subscribed) {
          reportSubscribe(tmplId).catch(() => {})
        }
      }
    },
    fail: () => {},
  })

  return true
}

/**
 * 向后端报告订阅状态
 */
async function reportSubscribe(templateId: string): Promise<void> {
  await request('/wechat/notice/subscribe', {
    method: 'POST',
    data: { template_id: templateId },
  })
}

/**
 * 查询当前用户的订阅状态
 */
export async function getSubscribeStatus(templateId?: string): Promise<Record<string, boolean>> {
  try {
    const query = templateId ? `?template_id=${encodeURIComponent(templateId)}` : ''
    const result = await request<{ subscriptions: Array<{ template_id: string; is_subscribed: boolean }> }>(
      `/wechat/notice/status${query}`,
    )
    const status: Record<string, boolean> = {}
    if (result.subscriptions) {
      for (const sub of result.subscriptions) {
        status[sub.template_id] = sub.is_subscribed
      }
    }
    return status
  } catch {
    return {}
  }
}
