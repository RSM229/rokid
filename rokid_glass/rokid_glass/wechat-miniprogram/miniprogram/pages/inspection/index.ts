import { absoluteMediaUrl } from '../../config'
import { request } from '../../services/request'
import { session } from '../../services/session'
import {
  UploadError,
  uploadInspectionCapture,
} from '../../services/upload'
import { themed } from '../../utils/theme'

interface ActiveSession {
  id: number
  status: string
  frequency_seconds: number
  started_at?: string
  total_captures: number
  total_hazards: number
}

interface HistoryItem {
  id: number
  status: string
  frequency_seconds: number
  started_at?: string
  stopped_at?: string
  total_captures: number
  total_hazards: number
}

interface CaptureResult {
  session_id: number
  total_captures: number
  total_hazards: number
  hazard_id?: number | null
  image_url?: string
  result?: {
    has_hazard?: boolean
    risk_status?: string
    risk_category?: string
    top1_risk?: string
    evidence?: string[]
  }
  instruction?: {
    speak_urgent?: string | null
    vibrate?: boolean
  }
}

const FREQUENCIES = [
  { label: '每 5 秒', value: 5 },
  { label: '每 10 秒', value: 10 },
  { label: '每 30 秒', value: 30 },
  { label: '每 60 秒', value: 60 },
]

Page(themed({
  data: {
    session: null as ActiveSession | null,
    frequencyIndex: 1,
    frequencies: FREQUENCIES,
    history: [] as HistoryItem[],
    loading: false,
    busy: false,
    // 摄像头
    cameraActive: false,
    cameraReady: false,
    cameraPosition: 'back' as 'back' | 'front',
    uploading: false,
    section: '',
    location: '',
    weather: '',
    result: null as CaptureResult | null,
  },
  onLoad() {
    if (!session.getToken()) {
      wx.reLaunch({ url: '/pages/login/index' })
      return
    }
    this.load()
  },
  onShow() {
    if (session.getToken()) this.load()
  },
  onPullDownRefresh() {
    this.load().finally(() => wx.stopPullDownRefresh())
  },
  onHide() {
    this.setData({ cameraActive: false, cameraReady: false })
  },
  async load() {
    this.setData({ loading: true })
    try {
      const [active, history] = await Promise.all([
        request<{ session: ActiveSession | null }>('/inspection/active'),
        request<{ items: HistoryItem[] }>('/inspection/history?limit=20'),
      ])
      this.setData({
        session: active.session,
        history: history.items || [],
      })
    } catch (error) {
      wx.showToast({ title: (error as Error).message, icon: 'none' })
    } finally {
      this.setData({ loading: false })
    }
  },
  onFrequencyChange(event: WechatMiniprogram.PickerChange) {
    this.setData({ frequencyIndex: Number(event.detail.value) })
  },
  async start() {
    if (this.data.busy || this.data.session) return
    this.setData({ busy: true })
    const frequency = this.data.frequencies[this.data.frequencyIndex].value
    try {
      const result = await request<{ session_id: number; status: string }>('/inspection/start', {
        method: 'POST',
        data: { frequency_seconds: frequency },
      })
      this.setData({
        session: {
          id: result.session_id,
          status: result.status,
          frequency_seconds: frequency,
          total_captures: 0,
          total_hazards: 0,
        },
        result: null,
      })
      wx.showToast({ title: '巡检已开始', icon: 'success' })
      await this.load()
    } catch (error) {
      wx.showModal({ title: '启动失败', content: (error as Error).message, showCancel: false })
    } finally {
      this.setData({ busy: false })
    }
  },
  async stop() {
    const sessionId = this.data.session?.id
    if (!sessionId || this.data.busy) return
    this.setData({ busy: true })
    try {
      await request(`/inspection/${sessionId}/stop`, { method: 'POST' })
      this.setData({ session: null, result: null })
      wx.showToast({ title: '巡检已结束', icon: 'success' })
      await this.load()
    } catch (error) {
      wx.showModal({ title: '结束失败', content: (error as Error).message, showCancel: false })
    } finally {
      this.setData({ busy: false })
    }
  },
  activateCamera() {
    wx.authorize({
      scope: 'scope.camera',
      success: () => this.setData({ cameraActive: true }),
      fail: () => {
        wx.showModal({
          title: '需要摄像头权限',
          content: '摄像头仅在您主动拍照识别时使用。您也可在相册中选择现场照片。',
          confirmText: '去设置',
          success: (result) => {
            if (result.confirm) wx.openSetting({})
          },
        })
      },
    })
  },
  onCameraReady() {
    this.setData({ cameraReady: true })
  },
  onCameraError(event: WechatMiniprogram.CustomEvent) {
    const detail = event.detail as { errMsg?: string }
    wx.showModal({
      title: '无法使用摄像头',
      content: detail.errMsg || '请在系统设置中允许小程序使用摄像头',
      showCancel: false,
    })
  },
  onInput(event: WechatMiniprogram.Input) {
    const field = event.currentTarget.dataset.field as string
    this.setData({ [field]: event.detail.value })
  },
  takePhoto() {
    if (!this.data.session) {
      wx.showToast({ title: '请先开始巡检', icon: 'none' })
      return
    }
    if (!this.data.cameraActive) {
      this.activateCamera()
      return
    }
    if (!this.data.cameraReady || this.data.uploading) return
    wx.createCameraContext().takePhoto({
      quality: 'high',
      success: (result) => this.uploadCapture(result.tempImagePath),
      fail: (error) => wx.showToast({ title: error.errMsg || '拍照失败', icon: 'none' }),
    })
  },
  chooseFromAlbum() {
    if (!this.data.session) {
      wx.showToast({ title: '请先开始巡检', icon: 'none' })
      return
    }
    wx.chooseMedia({
      count: 1,
      mediaType: ['image'],
      sourceType: ['album', 'camera'],
      camera: 'back',
      success: (result) => {
        const file = result.tempFiles[0]
        if (file) this.uploadCapture(file.tempFilePath)
      },
    })
  },
  async uploadCapture(filePath: string) {
    const sessionId = this.data.session?.id
    if (!sessionId || this.data.uploading) return
    this.setData({ uploading: true })
    try {
      const result = await uploadInspectionCapture<CaptureResult>(sessionId, filePath, {
        section: this.data.section,
        location: this.data.location,
        weather: this.data.weather,
      })
      this.setData({
        result: {
          ...result,
          image_url: result.image_url ? absoluteMediaUrl(result.image_url) : undefined,
        },
        session: {
          ...(this.data.session as ActiveSession),
          total_captures: result.total_captures,
          total_hazards: result.total_hazards,
        },
      })
      if (result.instruction?.vibrate || result.hazard_id) {
        wx.vibrateShort({ type: 'medium' })
      }
      if (result.instruction?.speak_urgent) {
        wx.showToast({ title: '发现隐患！', icon: 'none' })
      } else if (result.result?.risk_status) {
        wx.showToast({
          title: result.result.risk_status === '存在隐患' ? '识别完成，请查看' : '识别完成',
          icon: 'none',
        })
      }
    } catch (error) {
      if (!(error instanceof UploadError) || error.retryable) {
        wx.showModal({
          title: '识别失败',
          content: `${(error as Error).message}\n可稍后重新拍摄或从相册选择照片。`,
          showCancel: false,
        })
      } else {
        wx.showModal({
          title: '识别失败',
          content: error.message,
          showCancel: false,
        })
      }
    } finally {
      this.setData({ uploading: false })
    }
  },
  viewHazard() {
    const hazardId = this.data.result?.hazard_id
    if (!hazardId) return
    wx.navigateTo({ url: `/pages/hazards/index?id=${hazardId}` })
  },
  previewImage() {
    const url = this.data.result?.image_url
    if (!url) return
    wx.previewImage({ current: absoluteMediaUrl(url), urls: [absoluteMediaUrl(url)] })
  },
  switchCamera() {
    this.setData({
      cameraPosition: this.data.cameraPosition === 'back' ? 'front' : 'back',
    })
  },
}))
