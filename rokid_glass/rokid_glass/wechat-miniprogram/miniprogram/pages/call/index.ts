import { rtc, RtcCall, RtcMedia, createRtcMedia, isActiveCall } from '../../services/rtc'
import { startRtcPolling } from '../../services/signaling'
import { session } from '../../services/session'

const statusText: Record<string, string> = { ringing: '等待接听', accepted: '正在连接',
  rejected: '对方已拒绝', ended: '通话已结束', timeout: '通话已超时' }
Page({
  data: { call: null as RtcCall | null, incoming: false, status: '加载通话', error: '',
    busy: false, muted: false, speaker: true, camera: true, connected: false,
    pusher: {} as Record<string, any>, playerList: [] as Array<Record<string, any>> },
  _media: null as RtcMedia | null,
  _stop: null as (() => void) | null,
  _alive: false, _joining: false, _generation: 0, _id: '', _lastGood: 0,
  onLoad(options: Record<string, string>) {
    this._alive = true
    this._id = options.id || ''
    this._lastGood = Date.now()
    if (!/^[a-f0-9]{32}$/.test(this._id)) { this.setData({ error: '通话不存在' }); return }
    this._stop = startRtcPolling(() => rtc.action(this._id, 'heartbeat'), call => {
      this._lastGood = Date.now()
      this.updateCall(call)
    }, error => {
      this.setData({ error: error.message })
      if (Date.now() - this._lastGood > 45000 || !session.getToken()) this.closeMedia()
    }, () => { if (this.data.call?.status === 'accepted') void this.reconnect() })
  },
  updateCall(call: RtcCall) {
    if (!this._alive) return
    const previous = this.data.call
    if (previous && call.version < previous.version) return
    this.setData({ call, incoming: call.callee_id === session.getUser()?.user_id,
      status: this.data.connected && call.status === 'accepted' ? '通话中' : statusText[call.status] })
    if (!previous) this.setData({ camera: call.media_type === 'video' })
    if (!isActiveCall(call)) { this.closeMedia(); this._stop?.(); this._stop = null }
    else if (call.status === 'accepted' && !this._media && !this._joining) void this.join()
  },
  async join() {
    if (this._joining || !this._alive || this.data.call?.status !== 'accepted') return
    this._joining = true
    const generation = ++this._generation
    try {
      // authorize 只是尽力预授权：对隐私类 scope 从不弹窗、未授权时必 fail，
      // 不能因为它中断入会。真正授权由媒体 enter 时微信官方弹窗完成，
      // 失败走下方 catch 的统一提示。
      await new Promise<void>((resolve) => wx.authorize({ scope: 'scope.record', complete: () => resolve() }))
      if (this.data.call.media_type === 'video') {
        await new Promise<void>((resolve) => wx.authorize({ scope: 'scope.camera', complete: () => resolve() }))
      }
      const ticket = await rtc.credentials(this._id)
      if (!this._alive || generation !== this._generation || this.data.call?.status !== 'accepted') return
      this._media = createRtcMedia(this, () => {
        this.setData({ connected: true, status: '通话中', error: '' })
        void rtc.action(this._id, 'joined').catch(() => {})
      }, message => { this.setData({ error: message, connected: false, status: '连接中断' }) })
      this._media.enter(ticket, this.data.camera, this.data.muted)
    } catch (e) {
      if (this._alive && generation === this._generation) {
        this.closeMedia()
        this.setData({ error: (e as Error).message || '无法使用摄像头或麦克风，请在设置中授权' })
      }
    } finally { this._joining = false }
  },
  closeMedia() {
    this._generation++
    this._media?.close()
    this._media = null
    this.setData({ connected: false })
  },
  async reconnect() {
    if (this._joining) return
    this.closeMedia()
    await this.join()
  },
  async act(event: WechatMiniprogram.BaseEvent) {
    if (this.data.busy) return
    this.setData({ busy: true, error: '' })
    const action = event.currentTarget.dataset.action as 'accept' | 'reject' | 'end'
    if (action === 'end') this.closeMedia()
    try { this.updateCall(await rtc.action(this._id, action)) }
    catch (e) { this.setData({ error: (e as Error).message }) }
    finally { this.setData({ busy: false }) }
  },
  mute() {
    const muted = !this.data.muted
    this._media?.configure({ enableMic: !muted })
    this.setData({ muted })
  },
  speaker() { this.setData({ speaker: !this.data.speaker }) },
  camera() {
    if (this.data.call?.media_type !== 'video') return
    const camera = !this.data.camera
    this._media?.configure({ enableCamera: camera })
    this.setData({ camera })
  },
  switchCamera() { this._media?.switchCamera() },
  settings() { wx.openSetting({}) },
  pusherState(e: unknown) { this._media?.event('pusherEventHandler', e) },
  pusherNet(e: unknown) { this._media?.event('pusherNetStatusHandler', e) },
  pusherError(e: unknown) { this._media?.event('pusherErrorHandler', e) },
  pusherVolume(e: unknown) { this._media?.event('pusherAudioVolumeNotify', e) },
  playerState(e: unknown) { this._media?.event('playerEventHandler', e) },
  playerNet(e: unknown) { this._media?.event('playerNetStatus', e) },
  playerVolume(e: unknown) { this._media?.event('playerAudioVolumeNotify', e) },
  playerFullscreen(e: unknown) { this._media?.event('playerFullscreenChange', e) },
  leave() {
    if (!this._alive) return
    this._alive = false
    this._stop?.(); this._stop = null
    this.closeMedia()
    if (this.data.call && isActiveCall(this.data.call)) {
      void rtc.action(this._id, 'end').catch(() => {}) // server lease handles network loss
    }
  },
  onHide() { this.leave() },
  onUnload() { this.leave() },
})
