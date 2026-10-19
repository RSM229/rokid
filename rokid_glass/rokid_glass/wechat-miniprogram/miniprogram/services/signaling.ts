import { rtc, RtcCall } from './rtc'
import { session } from './session'

/** Non-overlapping authenticated polling with network recovery and bounded backoff. */
export function startRtcPolling<T>(read: () => Promise<T>, update: (value: T) => void,
  onError: (error: Error) => void = () => {}, onReconnect: () => void = () => {}): () => void {
  let stopped = false
  let busy = false
  let failures = 0
  let timer: ReturnType<typeof setTimeout> | undefined
  const tick = async () => {
    if (stopped || busy) return
    if (!session.getToken()) { stop(); return }
    busy = true
    try {
      const value = await read()
      if (!stopped) { update(value); failures = 0 }
    } catch (error) {
      if (!stopped) { failures++; onError(error instanceof Error ? error : new Error('连接中断')) }
    } finally {
      busy = false
      // 前台 2s 轮询；弱网退避上限压到 8s，避免来电延迟过大。
      // PRD 19.3 描述的 WebSocket 在线信令需后端在 /api/ws 广播 call_session，
      // 当前后端 rtc 路由未推送，故前端保留轮询作为等效降级。
      if (!stopped) timer = setTimeout(tick, Math.min(8000, 2000 * 2 ** Math.min(failures, 3)))
    }
  }
  const network = (state: WechatMiniprogram.OnNetworkStatusChangeListenerResult) => {
    if (stopped) return
    if (state.isConnected) {
      failures = 0
      if (timer) clearTimeout(timer)
      onReconnect()
      void tick()
    } else onError(new Error('网络已断开，正在等待重新连接'))
  }
  function stop() {
    stopped = true
    if (timer) clearTimeout(timer)
    // wx.onNetworkStatusChange 与 wx.offNetworkStatusChange 的回调类型定义不一致，
    // 监听器签名更具体，无法直接赋给 off 的宽类型，这里显式转换。
    wx.offNetworkStatusChange(
      network as unknown as WechatMiniprogram.OffNetworkStatusChangeCallback,
    )
  }
  wx.onNetworkStatusChange(network)
  void tick()
  return stop
}

let stopIncoming: (() => void) | undefined
let navigating = false
/** app.onShow: startIncomingCallPolling(); app.onHide/logout: stopIncomingCallPolling(). */
export function startIncomingCallPolling(onIncoming?: (call: RtcCall) => void): void {
  if (stopIncoming || !session.getToken()) return
  stopIncoming = startRtcPolling(rtc.calls, calls => {
    const me = session.getUser()?.user_id
    const call = calls.find(item => item.callee_id === me && item.status === 'ringing')
    const pages = getCurrentPages()
    if (!call || navigating || pages[pages.length - 1]?.route === 'pages/call/index') return
    if (onIncoming) { onIncoming(call); return }
    navigating = true
    wx.vibrateShort({ type: 'medium' })
    wx.navigateTo({ url: `/pages/call/index?id=${encodeURIComponent(call.id)}`,
      complete: () => { navigating = false } })
  })
}
export function stopIncomingCallPolling(): void {
  stopIncoming?.()
  stopIncoming = undefined
}
