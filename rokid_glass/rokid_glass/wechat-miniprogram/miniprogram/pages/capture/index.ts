import { absoluteMediaUrl } from '../../config'
import {
  UploadError,
  createWorkRecordOther,
  uploadImage,
  uploadVideo,
  uploadWorkRecordPhoto,
} from '../../services/upload'
import { uploadQueue } from '../../services/uploadQueue'
import { ensurePrivacy } from '../../utils/privacy'
import { createIdempotencyKey } from '../../utils/idempotency'
import { VideoSegment, inspectVideo, mergeSegments, MAX_SEGMENTS, MAX_VIDEO_SECONDS } from './video'
import { requireLogin } from '../../utils/guard'
import { themed } from '../../utils/theme'

interface MatchedRegulation {
  title?: string
  content_snippet?: string
  category?: string
  relevance_score?: number
  chunk_id?: string
}

interface DetectionResult {
  has_hazard?: boolean
  confidence?: number
  hazard_type?: string
  description?: string
  suggestion?: string
  scene_related?: string
  risk_status?: string
  risk_category?: string
  top1_risk?: string
  evidence?: string[]
  uncertainty?: string
  recheck_suggestion?: string[]
  matched_regulations?: MatchedRegulation[]
}

interface MediaUploadResult {
  id: number
  media_type: 'image' | 'video'
  url: string
  file_hash: string
  hazard_id?: number
  detection?: DetectionResult
}

interface AiImage {
  path: string
  uploading?: boolean
  result?: MediaUploadResult | null
  error?: string
}

interface WorkRecordUploadResult {
  detail: string
  record_id: number
  status: string
  photo_urls: string[]
}

Page(themed({
  data: {
    cameraReady: false,
    cameraActive: false,
    cameraPosition: 'back' as 'back' | 'front',
    recording: false,
    uploading: false,
    mediaType: '' as '' | 'image' | 'video',
    mediaPath: '',
    previewUrl: '',
    section: '',
    location: '',
    weather: '',
    workContent: '',
    triggerDetection: true,
    aiMode: false,
    workMode: false,
    workRecordId: 0,
    workPhotoUrl: '',
    businessType: '',
    businessId: 0,
    queueCount: 0,
    result: null as MediaUploadResult | null,
    durationSeconds: 0,
    recordStartedAt: 0,
    // 分段录像（PRD 19.2：开始/暂停/结束）
    recordingId: '',
    segments: [] as VideoSegment[],
    paused: false,
    merging: false,
    mergeReady: false,
    // AI 隐患识别·多图批量
    aiImages: [] as AiImage[],
    aiBatchMode: false,
    aiBatchDone: false,
  },
  onLoad(options: Record<string, string>) {
    if (!requireLogin('pages/capture/index', options)) return
    if (options.mode === 'hazard') {
      wx.setNavigationBarTitle({ title: 'AI隐患识别' })
      this.setData({ aiMode: true, triggerDetection: true })
    } else if (options.mode === 'work') {
      wx.setNavigationBarTitle({ title: '新建工作记录' })
      this.setData({ workMode: true, triggerDetection: false })
    }
    const context = wx.getStorageSync('gdjl_capture_context') as {
      businessType?: string
      businessId?: number
    }
    if (context?.businessType && context?.businessId) {
      this.setData({
        businessType: context.businessType,
        businessId: context.businessId,
        triggerDetection: context.businessType !== 'hazard',
      })
      wx.removeStorageSync('gdjl_capture_context')
    }
    this.refreshQueue()
  },
  onShow() {
    this.refreshQueue()
  },
  onHide() {
    // 先让 stopRecord 回调拿到最后一段并入库，再销毁 camera 组件，
    // 否则切后台时正在录的那一段会丢失。
    if (this.data.recording) this.stopRecord()
    setTimeout(() => {
      if (this.data.recording) return
      this.setData({ cameraActive: false, cameraReady: false })
    }, 400)
  },
  activateCamera() {
    wx.authorize({
      scope: 'scope.camera',
      success: () => this.setData({ cameraActive: true }),
      fail: () => {
        wx.showModal({
          title: '需要摄像头权限',
          content: '摄像头仅在您主动拍照、录像时使用。您仍可从相册选择媒体。',
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
  onDetectionChange(event: WechatMiniprogram.SwitchChange) {
    this.setData({ triggerDetection: event.detail.value })
  },
  switchCamera() {
    this.setData({
      cameraPosition: this.data.cameraPosition === 'back' ? 'front' : 'back',
    })
  },
  takePhoto() {
    if (!this.data.cameraActive) {
      this.activateCamera()
      return
    }
    if (!this.data.cameraReady || this.data.uploading) return
    wx.createCameraContext().takePhoto({
      quality: 'high',
      success: (result) => {
        this.setData({
          mediaType: 'image',
          mediaPath: result.tempImagePath,
          previewUrl: result.tempImagePath,
          result: null,
        })
      },
      fail: (error) => wx.showToast({ title: error.errMsg || '拍照失败', icon: 'none' }),
    })
  },
  /** 开始一次（支持暂停续录的）录像会话 */
  startRecord() {
    if (!this.data.cameraActive) {
      this.activateCamera()
      return
    }
    if (!this.data.cameraReady || this.data.uploading || this.data.recording || this.data.paused) return
    this.setData({
      recordingId: createIdempotencyKey('rec'),
      segments: [],
      paused: false,
      mergeReady: false,
      durationSeconds: 0,
      result: null,
    })
    this.startSegment()
  },

  /** 录制一段（首次开始或暂停后继续都走这里） */
  startSegment() {
    wx.createCameraContext().startRecord({
      // 单段上限与整体上限一致；不支持 timeout 的基础库会沿用默认 30s 并在超时时自动收尾
      timeout: MAX_VIDEO_SECONDS,
      timeoutCallback: (result) => {
        void this.appendSegment(result.tempVideoPath, true)
      },
      success: () => this.setData({ recording: true, recordStartedAt: Date.now() }),
      fail: (error) => wx.showToast({ title: error.errMsg || '录像失败', icon: 'none' }),
    })
  },

  /** 暂停：结束当前片段并保留，可继续录下一段 */
  pauseRecord() {
    if (!this.data.recording || this.data.paused) return
    if (this.data.segments.length + 1 >= MAX_SEGMENTS) {
      wx.showToast({ title: `最多录制 ${MAX_SEGMENTS} 段，请结束录像`, icon: 'none' })
      return
    }
    wx.createCameraContext().stopRecord({
      success: (result) => void this.appendSegment(result.tempVideoPath, false),
      fail: (error) => wx.showToast({ title: error.errMsg || '暂停失败', icon: 'none' }),
    })
  },

  resumeRecord() {
    if (!this.data.paused) return
    this.setData({ paused: false })
    this.startSegment()
  },

  /** 结束：收尾最后一段后合并（单段直接使用原文件） */
  stopRecord() {
    if (!this.data.recording) {
      if (this.data.paused) void this.finishSegments(this.data.segments)
      return
    }
    wx.createCameraContext().stopRecord({
      success: (result) => void this.appendSegment(result.tempVideoPath, true),
      fail: (error) => wx.showToast({ title: error.errMsg || '停止录像失败', icon: 'none' }),
    })
  },

  toggleRecord() {
    if (this.data.recording || this.data.paused) this.stopRecord()
    else this.startRecord()
  },

  /** 累加本段时长并把片段入列；finished=true 表示录像结束，进入合并 */
  async appendSegment(filePath: string, finished: boolean) {
    if (!filePath) return
    const elapsed = this.data.recordStartedAt
      ? Math.max(1, Math.round((Date.now() - this.data.recordStartedAt) / 1000))
      : 0
    this.setData({ recordStartedAt: 0, durationSeconds: this.data.durationSeconds + elapsed })
    try {
      // PRD 19.4：上传前校验时长、大小与分辨率
      const segment = await inspectVideo(filePath)
      const segments = [...this.data.segments, segment]
      this.setData({ segments, recording: false })
      if (finished) {
        await this.finishSegments(segments)
      } else {
        this.setData({ paused: true })
      }
    } catch (error) {
      this.setData({ recording: false, paused: false })
      wx.showModal({
        title: '这段录像不可用',
        content: (error as Error).message,
        showCancel: false,
      })
    }
  },

  async finishSegments(segments: VideoSegment[]) {
    this.setData({ recording: false, paused: false })
    if (segments.length === 0) return
    if (segments.length === 1) {
      this.handleVideo(segments[0].path)
      return
    }
    this.setData({ merging: true })
    try {
      const merged = await mergeSegments(segments, this.data.recordingId)
      this.setData({
        mediaType: 'video',
        mediaPath: merged,
        previewUrl: merged,
        mergeReady: false,
        result: null,
      })
      wx.showToast({ title: `已合并 ${segments.length} 段`, icon: 'success' })
    } catch (error) {
      // 合并失败保留片段，用户可重试合并，不丢已录内容
      this.setData({ mergeReady: true })
      wx.showModal({
        title: '视频合并失败',
        content: `${(error as Error).message}\n可重试合并，或分段直接上传。`,
        showCancel: false,
      })
    } finally {
      this.setData({ merging: false })
    }
  },

  async retryMerge() {
    if (this.data.merging || !this.data.segments.length) return
    await this.finishSegments(this.data.segments)
  },
  /** 合并不可用时把各段作为独立视频入队，避免已录内容丢失（19.5：可重试） */
  uploadSegments() {
    const segments = this.data.segments
    if (!segments.length) return
    const fields = this.buildFields()
    segments.forEach((segment, index) => {
      uploadQueue.enqueue(
        'video',
        segment.path,
        {
          ...fields,
          segment_index: index + 1,
          segment_total: segments.length,
          segment_duration: segment.duration || undefined,
        },
        '视频合并不可用，按分段上传',
      )
    })
    this.refreshQueue()
    wx.showModal({
      title: '已按分段加入上传队列',
      content: `共 ${segments.length} 段。请点下方「重试上传」逐个补传，成功后各段会在业务库中保留为独立影像。`,
      showCancel: false,
    })
  },
  handleVideo(filePath: string) {
    if (!filePath) return
    this.setData({
      recording: false,
      mediaType: 'video',
      mediaPath: filePath,
      previewUrl: filePath,
      durationSeconds: this.data.recordStartedAt
        ? Math.max(1, Math.round((Date.now() - this.data.recordStartedAt) / 1000))
        : this.data.durationSeconds,
      recordStartedAt: 0,
    })
  },
  chooseMedia() {
    wx.chooseMedia({
      count: 1,
      mediaType: this.data.workMode ? ['image'] : ['image', 'video'],
      sourceType: ['album', 'camera'],
      maxDuration: 60,
      camera: 'back',
      success: (result) => {
        const file = result.tempFiles[0]
        const mediaType = file.fileType === 'video' ? 'video' : 'image'
        this.setData({
          mediaType,
          mediaPath: file.tempFilePath,
          previewUrl: file.tempFilePath,
          result: null,
          durationSeconds: mediaType === 'video' ? Math.round(file.duration || 0) : 0,
        })
      },
      fail: (error) => {
        const msg = error.errMsg || ''
        if (msg.includes('cancel')) return
        wx.showToast({ title: msg.includes('privacy') ? '隐私协议审核中，暂无法选择文件' : `无法选择：${msg}`, icon: 'none' })
      },
    })
  },
  /** 多图入口：相册多选，进入批量识别模式（最多 9 张，可多次追加） */
  chooseAiImages() {
    wx.chooseMedia({
      count: 9,
      mediaType: ['image'],
      sourceType: ['album', 'camera'],
      camera: 'back',
      success: (result) => {
        const picked: AiImage[] = result.tempFiles.map((file) => ({
          path: file.tempFilePath,
          uploading: false,
          result: null,
          error: '',
        }))
        const merged = [...this.data.aiImages, ...picked].slice(0, 9)
        this.setData({
          aiImages: merged,
          aiBatchMode: true,
          aiBatchDone: false,
          // 清掉即时单图态，避免与批量并存
          mediaPath: '',
          previewUrl: '',
          result: null,
        })
      },
      // 隐私接口：隐私指引未过审或用户曾拒绝时会静默 fail，必须给出提示
      fail: (error) => {
        const msg = error.errMsg || ''
        if (msg.includes('cancel')) return
        wx.showToast({ title: msg.includes('privacy') ? '隐私协议审核中，暂无法选择照片' : `无法选择照片：${msg}`, icon: 'none' })
      },
    })
  },
  /** 批量：移除某张待识别图 */
  removeAiImage(event: WechatMiniprogram.TouchEvent) {
    const index = Number(event.currentTarget.dataset.index)
    const aiImages = this.data.aiImages.filter((_, i) => i !== index)
    this.setData({ aiImages, aiBatchMode: aiImages.length > 0 })
  },
  /** 批量：清空并退回即时模式 */
  clearAiBatch() {
    this.setData({ aiImages: [], aiBatchMode: false, aiBatchDone: false })
  },
  /** 批量：并发上传识别（限流 3，避免触发 429） */
  async uploadAiBatch() {
    const images = this.data.aiImages
    if (!images.length || this.data.uploading) return
    this.setData({ uploading: true })
    const fields = this.buildFields()
    let cursor = 0
    const runOne = async () => {
      while (cursor < images.length) {
        const idx = cursor++
        this.setData({ [`aiImages[${idx}].uploading`]: true })
        try {
          const res = await uploadImage<MediaUploadResult>(images[idx].path, fields)
          this.setData({
            [`aiImages[${idx}].uploading`]: false,
            [`aiImages[${idx}].result`]: res,
            [`aiImages[${idx}].error`]: '',
          })
        } catch (error) {
          this.setData({
            [`aiImages[${idx}].uploading`]: false,
            [`aiImages[${idx}].error`]: (error as Error).message,
          })
        }
      }
    }
    await Promise.all([runOne(), runOne(), runOne()])
    this.setData({ uploading: false, aiBatchDone: true })
    const failed = this.data.aiImages.filter((img) => !img.result && img.error).length
    wx.showToast({
      title: failed ? `完成，${failed} 张失败` : '批量识别完成',
      icon: failed ? 'none' : 'success',
    })
  },
  /** 跳转隐患详情（带 id） */
  openHazardById(event: WechatMiniprogram.TouchEvent) {
    const id = Number(event.currentTarget.dataset.id)
    if (id) wx.navigateTo({ url: `/pages/hazards/index?id=${id}` })
  },
  async locate() {
    const allowed = await ensurePrivacy('位置信息用于为现场影像添加位置水印，')
    if (!allowed) return
    wx.getLocation({
      type: 'gcj02',
      success: (result) => {
        this.setData({
          location: `${result.latitude.toFixed(6)},${result.longitude.toFixed(6)}`,
        })
      },
      fail: () => {
        wx.showModal({
          title: '定位未开启',
          content: '可在设置中授权定位，或在位置栏手动填写。',
          confirmText: '去设置',
          success: (result) => {
            if (result.confirm) wx.openSetting({})
          },
        })
      },
    })
  },
  buildFields() {
    return {
      source: 'miniprogram',
      section: this.data.section,
      location: this.data.location,
      weather: this.data.weather,
      work_content: this.data.workContent,
      trigger_detection: this.data.triggerDetection,
      business_type: this.data.businessType || undefined,
      business_id: this.data.businessId || undefined,
      duration_seconds: this.data.mediaType === 'video'
        ? this.data.durationSeconds || undefined
        : undefined,
    }
  },
  async upload() {
    if (!this.data.mediaPath || !this.data.mediaType || this.data.uploading) {
      wx.showToast({ title: '请先拍摄或选择文件', icon: 'none' })
      return
    }
    // PRD 19.4：上传前校验格式、大小与时长
    if (this.data.mediaType === 'video') {
      try {
        await inspectVideo(this.data.mediaPath)
      } catch (error) {
        wx.showModal({ title: '视频不符合要求', content: (error as Error).message, showCancel: false })
        return
      }
    }
    this.setData({ uploading: true })
    if (this.data.workMode) {
      let mediaResult: MediaUploadResult | null = null
      let workResult: WorkRecordUploadResult | null = null
      try {
        if (this.data.mediaType === 'image') {
          workResult = await uploadWorkRecordPhoto<WorkRecordUploadResult>(
            this.data.mediaPath,
            '待定',
            { payload_json: this.buildWorkPayload() },
          )
          if (this.data.triggerDetection) {
            try {
              mediaResult = await uploadImage<MediaUploadResult>(
                this.data.mediaPath,
                this.buildFields(),
              )
            } catch (error) {
              wx.showToast({ title: '工作记录已保存，隐患识别未完成', icon: 'none' })
            }
          }
        } else {
          mediaResult = await uploadVideo<MediaUploadResult>(
            this.data.mediaPath,
            this.buildFields(),
          )
          try {
            workResult = await createWorkRecordOther<WorkRecordUploadResult>({
              section: this.data.section,
              location: this.data.location,
              work_content: this.data.workContent,
            })
          } catch (error) {
            wx.showToast({ title: '视频已保存，但工作记录创建失败', icon: 'none' })
          }
        }
        this.setData({
          workRecordId: workResult ? workResult.record_id : 0,
          workPhotoUrl: absoluteMediaUrl(
            workResult && workResult.photo_urls.length ? workResult.photo_urls[0] : '',
          ),
          result: mediaResult,
          previewUrl: absoluteMediaUrl(
            mediaResult
              ? mediaResult.url
              : workResult && workResult.photo_urls.length
                ? workResult.photo_urls[0]
                : '',
          ),
        })
        wx.showToast({
          title: workResult ? '工作记录已保存' : '取证资料已保存',
          icon: 'success',
        })
      } catch (error) {
        wx.showModal({
          title: '保存失败',
          content: (error as Error).message,
          showCancel: false,
        })
      } finally {
        this.setData({ uploading: false })
      }
      return
    }
    const fields = this.buildFields()
    try {
      const result = this.data.mediaType === 'image'
        ? await uploadImage<MediaUploadResult>(this.data.mediaPath, fields)
        : await uploadVideo<MediaUploadResult>(this.data.mediaPath, fields)
      this.setData({
        result,
        previewUrl: absoluteMediaUrl(result.url),
      })
      wx.showToast({ title: this.data.aiMode ? 'AI识别完成' : '取证资料已保存', icon: 'success' })
    } catch (error) {
      if (!(error instanceof UploadError) || error.retryable) {
        uploadQueue.enqueue(
          this.data.mediaType,
          this.data.mediaPath,
          fields,
          (error as Error).message,
        )
        this.refreshQueue()
        wx.showModal({
          title: '已加入待上传队列',
          content: `${(error as Error).message}\n网络恢复后可点击“重试上传”。`,
          showCancel: false,
        })
      } else {
        wx.showModal({
          title: '上传内容不符合要求',
          content: error.message,
          showCancel: false,
        })
      }
    } finally {
      this.setData({ uploading: false })
    }
  },
  clearMedia() {
    this.setData({
      mediaType: '',
      mediaPath: '',
      previewUrl: '',
      result: null,
      durationSeconds: 0,
    })
  },
  clearWork() {
    this.setData({
      mediaType: '',
      mediaPath: '',
      previewUrl: '',
      result: null,
      workRecordId: 0,
      workPhotoUrl: '',
      durationSeconds: 0,
    })
  },
  goWorkList() {
    wx.navigateTo({ url: '/pages/work/index' })
  },
  buildWorkPayload() {
    return JSON.stringify({
      section: this.data.section,
      location: this.data.location,
      work_content: this.data.workContent,
      source: 'miniprogram',
    })
  },
  refreshQueue() {
    // 采集页只负责照片/视频补传；语音转写由对话页重试（需要承接识别文本）
    const count = uploadQueue.list().filter((item) => item.mediaType !== 'audio').length
    this.setData({ queueCount: count })
  },
  async retryQueue() {
    if (this.data.uploading || !this.data.queueCount) return
    this.setData({ uploading: true })
    const result = await uploadQueue.retryAll(true)
    this.refreshQueue()
    this.setData({ uploading: false })
    wx.showToast({
      title: result.failed ? `成功${result.succeeded}，失败${result.failed}` : `已上传${result.succeeded}项`,
      icon: result.failed ? 'none' : 'success',
    })
  },
  openHazard() {
    if (!this.data.result?.hazard_id) return
    wx.navigateTo({ url: `/pages/hazards/index?id=${this.data.result.hazard_id}` })
  },
}))
