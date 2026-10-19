import {
  getWechatBindingStatus,
  unbindWechat,
  updateWechatProfile,
} from '../../services/auth'
import { absoluteMediaUrl } from '../../config'
import { session } from '../../services/session'
import { themed } from '../../utils/theme'

/**
 * 微信绑定（账号安全 · 子目录）· 2026-10-12 晚
 *
 * 绑定状态 / 时间 / 最近登录 / 微信资料（昵称头像）/ 解绑。
 * 微信平台不向小程序提供真实微信号（wxid）；管理端「微信绑定管理」
 * 识别绑定者全靠用户确认的昵称与头像（官方「头像昵称填写能力」）。
 * onShow 刷新：解绑等操作后状态即时回显。
 */
Page(themed({
  data: {
    // 当前账号的微信绑定状态（/miniprogram/auth/status）
    wxBound: null as boolean | null,
    wxBoundAt: '',
    wxLastLoginAt: '',
    // 用户确认的微信昵称/头像（管理端识别绑定者用）；wxAvatarTemp 是本次
    // chooseAvatar 选中的临时文件，保存成功后才落到服务端
    wxNickname: '',
    wxAvatarUrl: '',
    wxAvatarTemp: '',
    showWxProfile: false,
    savingWxProfile: false,
    unbinding: false,
    // chooseAvatar 能力是否可用（基础库 ≥2.21.2）。false 时 open-type 静默失效，
    // 文案直接引导用户走「从相册选择」，别让用户对着没反应的按钮发呆
    avatarCapable: true,
  },

  onLoad() {
    this.setData({ avatarCapable: wx.canIUse('button.open-type.chooseAvatar') })
    if (!session.getToken()) {
      wx.reLaunch({ url: '/pages/login/index' })
      return
    }
  },

  onShow() {
    void this.loadWechatBinding()
  },

  /** 拉取当前账号的微信绑定状态，绑错了能在这里发现并自助纠正 */
  async loadWechatBinding() {
    try {
      const status = await getWechatBindingStatus()
      this.setData({
        wxBound: !!status.bound,
        wxBoundAt: status.bound_at ? String(status.bound_at).slice(0, 16).replace('T', ' ') : '',
        wxLastLoginAt: status.last_login_at ? String(status.last_login_at).slice(0, 16).replace('T', ' ') : '',
        wxNickname: status.wx_nickname || '',
        wxAvatarUrl: absoluteMediaUrl(status.wx_avatar_url || ''),
      })
    } catch {
      // 接口不可用时保持「未知」，不让用户误以为没绑
      this.setData({ wxBound: null, wxBoundAt: '', wxLastLoginAt: '' })
    }
  },

  // ============ 微信资料（昵称/头像）============
  openWxProfile() {
    this.setData({ showWxProfile: true, wxAvatarTemp: '' })
  },

  closeWxProfile() {
    this.setData({ showWxProfile: false, wxAvatarTemp: '' })
  },

  /** 官方 chooseAvatar：e.detail.avatarUrl 是临时文件或默认头像 URL */
  onChooseAvatar(event: WechatMiniprogram.CustomEvent<{ avatarUrl: string }>) {
    this.setData({ wxAvatarTemp: event.detail.avatarUrl || '' })
  },

  /**
   * 相册选图兜底（不依赖「微信昵称、头像」隐私能力）。
   *
   * 真机上 `open-type="chooseAvatar"` 点了没反应有两种已知原因：
   *   1）基础库 < 2.21.2，能力未实现，open-type 退化为普通按钮；
   *   2）平台侧《用户隐私保护指引》未声明「微信昵称、头像」——微信直接禁用该能力且静默失败
   *      （与 2026-10-09「选图/拍照全都没反应」同源，见 utils/privacy.ts）。
   * 两种情况都只影响「取微信头像」，选中的本地临时文件依旧能走 wx.uploadFile 上传，
   * 所以相册这条通道始终可用。
   */
  pickAvatarFromAlbum() {
    wx.chooseMedia({
      count: 1,
      mediaType: ['image'],
      sizeType: ['compressed'],
      success: (res) => {
        const file = (res.tempFiles || [])[0]
        const path = file ? file.tempFilePath : ''
        if (path) this.setData({ wxAvatarTemp: path })
      },
      fail: () => {},
    })
  },

  onWxNickInput(event: WechatMiniprogram.CustomEvent) {
    this.setData({ wxNickname: String(event.detail.value || '') })
  },

  async saveWxProfile() {
    if (this.data.savingWxProfile) return
    const nickname = this.data.wxNickname.trim()
    const avatarPath = this.data.wxAvatarTemp
    if (!nickname && !avatarPath) {
      wx.showToast({ title: '昵称与头像至少填一项', icon: 'none' })
      return
    }
    this.setData({ savingWxProfile: true })
    try {
      await updateWechatProfile({ nickname, avatarPath })
      wx.showToast({ title: '已保存', icon: 'success' })
      this.setData({ showWxProfile: false, wxAvatarTemp: '' })
      await this.loadWechatBinding()
    } catch (error) {
      wx.showToast({ title: (error as Error).message || '保存失败', icon: 'none' })
    } finally {
      this.setData({ savingWxProfile: false })
    }
  },

  /**
   * 解除本人账号的微信绑定。
   * 绑到别人微信 / 换微信号时自助纠正；需要输入密码做二次确认。
   */
  unbindWechat() {
    if (this.data.unbinding) return
    wx.showModal({
      title: '解除微信绑定',
      content: '解除后本微信号将不能直接登录，需要重新用账号密码绑定。请输入当前账号密码确认：',
      editable: true,
      placeholderText: '账号密码',
      confirmText: '解除绑定',
      cancelText: '取消',
      success: (result) => {
        if (!result.confirm) return
        const password = String(result.content || '').trim()
        if (!password) {
          wx.showToast({ title: '请输入密码', icon: 'none' })
          return
        }
        void this.doUnbindWechat(password)
      },
    })
  },

  async doUnbindWechat(password: string) {
    this.setData({ unbinding: true })
    try {
      const detail = await unbindWechat(password)
      wx.showToast({ title: detail, icon: 'none' })
      this.setData({ wxBound: false, wxBoundAt: '', wxLastLoginAt: '' })
      await this.loadWechatBinding()
    } catch (error) {
      wx.showToast({ title: (error as Error).message || '解绑失败', icon: 'none' })
    } finally {
      this.setData({ unbinding: false })
    }
  },
}))
