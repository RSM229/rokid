import { request } from './request'

export interface RtcCall {
  id: string
  caller_id: number
  callee_id: number
  media_type: 'audio' | 'video'
  status: 'ringing' | 'accepted' | 'rejected' | 'ended' | 'timeout'
  version: number
  deadline: number
  end_reason?: string
}
export interface RtcContact { id: number; real_name: string; role: string }
export interface RtcCredentials {
  sdkAppID: number; userID: string; userSig: string; privateMapKey: string
  strRoomID: string; scene: string; expires_at: number
}
export const rtc = {
  status: () => request<{ configured: boolean; code: string }>('/rtc/status'),
  users: (offset = 0) => request<RtcContact[]>(`/rtc/users?offset=${offset}&limit=100`),
  calls: () => request<RtcCall[]>('/rtc/calls', { timeout: 10000 }),
  create: (callee_id: number, media_type: 'audio' | 'video') => request<RtcCall>('/rtc/calls', {
    method: 'POST', data: { callee_id, media_type },
  }),
  get: (id: string) => request<RtcCall>(`/rtc/calls/${encodeURIComponent(id)}`),
  action: (id: string, action: 'accept' | 'reject' | 'end' | 'heartbeat' | 'joined') =>
    request<RtcCall>(`/rtc/calls/${encodeURIComponent(id)}/${action}`, { method: 'POST', timeout: 10000 }),
  credentials: (id: string) => request<RtcCredentials>(`/rtc/calls/${encodeURIComponent(id)}/credentials`, { method: 'POST' }),
}
export const isActiveCall = (call: RtcCall) => call.status === 'ringing' || call.status === 'accepted'

// SDK 1.1.15 is untyped. Keep its dynamic boundary here, never create media URLs ourselves.
type Attributes = Record<string, any>
type MediaPage = { setData: (data: Attributes, callback?: () => void) => void }
export function createRtcMedia(page: MediaPage, onJoined: () => void, onError: (message: string) => void) {
  let module: any
  try { module = require('trtc-wx-sdk') } catch (_) {
    throw new Error('音视频组件未安装：请在微信开发者工具执行「工具 → 构建 npm」后重新编译上传')
  }
  const TRTC = module.default || module
  const sdk = new TRTC(page)
  let closed = false
  const bindings: Array<[string, (event: any) => void]> = []
  const listen = (name: string, handler: (event: any) => void) => {
    const callback = (event: any) => { if (!closed) handler(event) }
    sdk.on(sdk.EVENT[name], callback)
    bindings.push([sdk.EVENT[name], callback])
  }
  listen('LOCAL_JOIN', onJoined)
  listen('ERROR', () => onError('音视频连接失败，请检查权限与网络后重试'))
  listen('KICKED_OUT', () => onError('已退出音视频房间，请重新连接'))
  for (const kind of ['AUDIO', 'VIDEO']) {
    for (const change of ['ADD', 'REMOVE']) {
      listen(`REMOTE_${kind}_${change}`, (event: any) => {
        const player = event.data.player
        page.setData({ playerList: sdk.setPlayerAttributes(player.id, {
          [kind === 'AUDIO' ? 'muteAudio' : 'muteVideo']: change === 'REMOVE',
        }) })
      })
    }
  }
  listen('REMOTE_USER_LEAVE', () => page.setData({ playerList: sdk.getPlayerList() }))
  sdk.createPusher({ enableMic: true, enableCamera: false })
  return {
    enter(ticket: RtcCredentials, video: boolean, muted: boolean) {
      const { sdkAppID, userID, userSig, privateMapKey, strRoomID, scene } = ticket
      page.setData({ pusher: sdk.enterRoom({ sdkAppID, userID, userSig, privateMapKey,
        strRoomID, scene, enableMic: !muted, enableCamera: video }) }, () => {
        if (!closed) sdk.getPusherInstance().start()
      })
    },
    configure(attributes: Attributes) { page.setData({ pusher: sdk.setPusherAttributes(attributes) }) },
    switchCamera() { sdk.getPusherInstance().switchCamera() },
    event(method: string, event: unknown) { if (!closed) sdk[method](event) },
    close() {
      if (closed) return
      closed = true
      for (const [name, callback] of bindings) sdk.off(name, callback)
      sdk.exitRoom()
      page.setData({ pusher: {}, playerList: [] })
    },
  }
}
export type RtcMedia = ReturnType<typeof createRtcMedia>
