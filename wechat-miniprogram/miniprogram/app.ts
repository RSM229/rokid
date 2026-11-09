import { session } from './services/session'
import { uploadQueue } from './services/uploadQueue'

let queueRetrying = false

const ONBOARD_KEY = 'gdjl_onboarded_v1'

// 首次启动展示新手指引；看完/跳过后写 storage，后续不再弹
function maybeShowGuide(): void {
  try {
    if (wx.getStorageSync(ONBOARD_KEY)) return
    // 服务通知 / 分享携带业务参数（id、item_id）冷启动时，先把目标页送达，
    // 否则下面 setTimeout 里的 reLaunch 会用引导页覆盖通知跳转。
    // 这里不写 ONBOARD_KEY：下次从主入口正常启动时仍会展示新手指引。
    const query = wx.getLaunchOptionsSync().query || {}
    if (query.id || query.item_id) return
    setTimeout(() => {
      wx.reLaunch({ url: '/pages/guide/index' })
    }, 0)
  } catch (e) {
    void e
  }
}

function retryPendingUploads(showResult = false): void {
  if (queueRetrying || !session.getToken() || uploadQueue.list().length === 0) return
  queueRetrying = true
  // 语音需要对话页承接转写文本，这里只补传照片/视频
  uploadQueue.retryAll(true)
    .then((result) => {
      if (showResult && result.succeeded > 0) {
        wx.showToast({
          title: result.failed
            ? `补传成功${result.succeeded}项`
            : `已补传${result.succeeded}项`,
          icon: result.failed ? 'none' : 'success',
        })
      }
    })
    .finally(() => {
      queueRetrying = false
    })
}

App({
  onLaunch() {
    wx.onNetworkStatusChange((result) => {
      if (result.isConnected) retryPendingUploads(true)
    })
    maybeShowGuide()
  },
  onShow() {
    if (session.getToken()) {
      retryPendingUploads()
    }
  },
})
