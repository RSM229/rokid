import { request } from '../../services/request'
import { session } from '../../services/session'
import {
  noticeUnavailableReason,
  preloadNoticeTemplates,
  requestSubscribeMessage,
  getSubscribeStatus,
  NOTICE_TEMPLATE_KEYS,
} from '../../services/notice'
import {
  DEFAULT_THEME,
  THEME_PRESETS,
  getTheme,
  isDefaultTheme,
  previewColor,
  resetTheme,
  setTheme,
  themeStyle,
  themed,
  type ThemeSetting,
} from '../../utils/theme'
import drawQrcode = require('../../utils/qrcode')

interface PresetOption extends ThemeSetting {
  name: string
  color: string
  active: boolean
}

/**
 * 版本号取微信真实的「关于」信息，不再写死。
 *
 * 起因：原先「关于 → 版本」硬编码 1.0.0，而实际上传版本早已是 1.0.2 ——
 * 写死的字面量必然随每次发版过期，且没人会记得改。
 *
 * 取值规则：
 *   - 正式版：显示真实版本号（如 1.0.2）
 *   - 体验版 / 开发版：微信读不到版本字符串，如实显示环境名，不冒充正式版本号
 *     （在开发者工具里能看到「开发版」，正是我们想要的诚实反馈）
 */
function resolveVersion(): string {
  try {
    const mini = wx.getAccountInfoSync().miniProgram
    const version = mini && mini.version
    if (version) return version
    if (mini && mini.envVersion === 'trial') return '体验版'
    if (mini && mini.envVersion === 'develop') return '开发版'
  } catch (error) {
    // 取不到就让下面兜底，不因为一行版本号把整页拉挂
  }
  return '未知'
}

/**
 * 设置（二级页）· 2026-10-12 新建
 *
 * 从「我的」迁出：通用设置（主题配色 / 眼镜端扫码登录）、通知与权限、关于。
 * 账号组 2026-10-12 晚再度拆出独立页 pages/account-security。
 * 两个浮层（主题抽屉、扫码弹层）由本页入口唤起，故其状态与绘制逻辑一并搬来。
 */
Page(themed({
  data: {
    // 关于：版本号（运行时读微信「关于」信息，见 resolveVersion）
    version: resolveVersion(),
    // 眼镜端扫码登录
    showQr: false,
    qrLoading: false,
    qrTicket: '',
    qrCanvasId: 'glassesLoginQr',
    qrImage: '',
    // 主题配色（2026-09-20 新增，2026-10-12 随本页迁出）
    showTheme: false,
    themeH: DEFAULT_THEME.h,
    themeS: DEFAULT_THEME.s,
    themeL: DEFAULT_THEME.l,
    themePreview: previewColor(DEFAULT_THEME),
    themeName: '默认',
    presets: [] as PresetOption[],
    // 微信服务通知订阅状态（后端 /wechat/notice/status）
    noticeTotal: Object.keys(NOTICE_TEMPLATE_KEYS).length,
    noticeSubscribed: 0,
  },

  onLoad() {
    if (!session.getToken()) {
      wx.reLaunch({ url: '/pages/login/index' })
      return
    }
    void preloadNoticeTemplates()
  },

  onShow() {
    this.syncTheme()
    void this.loadNoticeStatus()
  },

  /** 拉取微信服务通知订阅状态，让用户直接看到有几项已授权 */
  async loadNoticeStatus() {
    const status = await getSubscribeStatus()
    const subscribed = Object.values(status).filter(Boolean).length
    this.setData({ noticeSubscribed: subscribed })
  },

  subscribeNotices(event: WechatMiniprogram.TouchEvent) {
    const keys = event.currentTarget.dataset.group === 'approval'
      ? [NOTICE_TEMPLATE_KEYS.DOCUMENT_APPROVED, NOTICE_TEMPLATE_KEYS.DOCUMENT_REJECTED]
      : [NOTICE_TEMPLATE_KEYS.TASK_ASSIGNED, NOTICE_TEMPLATE_KEYS.TASK_URGED, NOTICE_TEMPLATE_KEYS.HAZARD_DETECTED]
    if (!requestSubscribeMessage(keys)) {
      // 区分两种不可用：后端没配模板（重试无用）vs 预加载失败（可重试）
      const title = noticeUnavailableReason() === 'empty'
        ? '订阅消息模板未配置，需管理员在后台配置后开放'
        : '通知模板尚未就绪，请稍后重试'
      wx.showToast({ title, icon: 'none' })
      void preloadNoticeTemplates()
      return
    }
    // 授权结果由微信回调后端落库，稍等再刷新展示
    setTimeout(() => void this.loadNoticeStatus(), 1500)
  },

  openPermissionSettings() {
    wx.openSetting({})
  },

  openPrivacyGuide() {
    wx.openPrivacyContract({ fail: () => wx.showToast({ title: '隐私指引暂时无法打开', icon: 'none' }) })
  },

  openHelp() {
    wx.navigateTo({ url: '/pages/guide/index?mode=help' })
  },

  /** 把当前主题同步进界面状态（色板高亮、预览色） */
  syncTheme(theme?: ThemeSetting) {
    const current = theme || getTheme()
    const presets: PresetOption[] = THEME_PRESETS.map((preset) => ({
      ...preset,
      color: previewColor(preset),
      active: Math.abs(preset.h - current.h) < 2
        && Math.abs(preset.s - current.s) < 2
        && Math.abs(preset.l - current.l) < 2,
    }))
    this.setData({
      themeH: current.h,
      themeS: current.s,
      themeL: current.l,
      themePreview: previewColor(current),
      themeName: isDefaultTheme(current) ? '默认' : '自定义',
      presets,
      themeStyle: themeStyle(current),
    })
  },

  openTheme() {
    this.syncTheme()
    this.setData({ showTheme: true })
  },

  closeTheme() {
    this.setData({ showTheme: false })
  },

  /** 拖动中：实时预览（不落盘，避免频繁写 storage） */
  onThemeSlide(event: WechatMiniprogram.SliderChange) {
    const key = event.currentTarget.dataset.key as string
    const value = Number(event.detail.value)
    this.syncTheme({
      h: key === 'h' ? value : this.data.themeH,
      s: key === 's' ? value : this.data.themeS,
      l: key === 'l' ? value : this.data.themeL,
    })
  },

  /** 松手：落盘保存 */
  onThemeSlideEnd(event: WechatMiniprogram.SliderChange) {
    const key = event.currentTarget.dataset.key as string
    const value = Number(event.detail.value)
    const next: ThemeSetting = {
      h: key === 'h' ? value : this.data.themeH,
      s: key === 's' ? value : this.data.themeS,
      l: key === 'l' ? value : this.data.themeL,
    }
    setTheme(next)
    this.syncTheme(next)
  },

  pickPreset(event: WechatMiniprogram.TouchEvent) {
    const index = Number(event.currentTarget.dataset.index)
    const preset = THEME_PRESETS[index]
    if (!preset) return
    const next: ThemeSetting = { h: preset.h, s: preset.s, l: preset.l }
    setTheme(next)
    this.syncTheme(next)
    wx.showToast({ title: `已应用「${preset.name}」`, icon: 'none' })
  },

  restoreDefaultTheme() {
    resetTheme()
    this.syncTheme({ ...DEFAULT_THEME })
    wx.showToast({ title: '已恢复默认配色', icon: 'none' })
  },

  async openQrLogin() {
    this.setData({ showQr: true, qrLoading: true })
    await this.fetchAndDrawQr()
  },

  closeQrLogin() {
    this.setData({ showQr: false })
  },

  async refreshQr() {
    this.setData({ qrLoading: true })
    await this.fetchAndDrawQr()
  },

  async fetchAndDrawQr() {
    try {
      const { ticket } = await request<{ ticket: string; expires_in: number }>(
        '/auth/glasses/qr-create',
        { method: 'POST', data: {} },
      )
      this.setData({ qrTicket: ticket })
      this.drawQr(ticket)
    } catch (error) {
      wx.showToast({ title: (error as Error).message, icon: 'none' })
    } finally {
      this.setData({ qrLoading: false })
    }
  },

  drawQr(ticket: string) {
    const render = () => {
      wx.nextTick(() => {
        try {
          drawQrcode({
            canvasId: this.data.qrCanvasId,
            text: ticket,
            width: 220,
            height: 220,
            _this: this,
            callback: () => {
              wx.canvasToTempFilePath({
                canvasId: this.data.qrCanvasId,
                success: (res) => {
                  this.setData({ qrImage: res.tempFilePath })
                },
                fail: () => {
                  // 转图失败时保留 canvas 直绘兜底
                },
              }, this as WechatMiniprogram.Page.Instance<WechatMiniprogram.IAnyObject, WechatMiniprogram.IAnyObject>)
            },
          })
        } catch (error) {
          wx.showToast({ title: `二维码生成失败: ${(error as Error).message}`, icon: 'none' })
        }
      })
    }
    render()
    setTimeout(render, 120)
  },
}))
