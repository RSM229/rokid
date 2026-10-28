import { bindAccount, loginWithWechat } from '../../services/auth'
import { session } from '../../services/session'
import { themed } from '../../utils/theme'
import { ROLE_KEYS, ROLE_LABELS } from '../../utils/role'

function enterAfterLogin(): void {
  // 「忘记密码」兜底：管理员重置为一次性口令的账号，提示先改密（可跳过，见 skip()）
  const user = session.getUser()
  if (user?.must_change_password && !session.isForcePasswordSkipped()) {
    wx.redirectTo({ url: '/pages/force-password/index' })
    return
  }
  const pending = wx.getStorageSync('gdjl_pending_route') as {
    route?: string
    options?: Record<string, string>
  }
  wx.removeStorageSync('gdjl_pending_route')
  if (!pending?.route || pending.route === 'pages/login/index') {
    wx.redirectTo({ url: '/pages/agent/index' })
    return
  }
  const query = Object.entries(pending.options || {})
    .map(([key, value]) => `${encodeURIComponent(key)}=${encodeURIComponent(value)}`)
    .join('&')
  wx.redirectTo({ url: `/${pending.route}${query ? `?${query}` : ''}` })
}

Page(themed({
  data: {
    loading: false,
    needBind: false,
    username: '',
    password: '',
    // 绑定时可选填的微信昵称：微信平台不提供真实微信号（wxid），
    // 管理端「微信绑定管理」凭用户确认的昵称/头像识别绑定者
    wxNickname: '',
    roleIndex: 0,
    // S2：后端 role 为英文 key，展示时经 roleLabels 转中文
    roles: ROLE_KEYS,
    roleLabels: ROLE_KEYS.map((k) => ROLE_LABELS[k]),
    error: '',
  },
  onLoad() {
    if (session.getToken()) wx.redirectTo({ url: '/pages/agent/index' })
  },
  onInput(event: WechatMiniprogram.Input) {
    const field = event.currentTarget.dataset.field as string
    this.setData({ [field]: event.detail.value })
  },
  onRoleChange(event: WechatMiniprogram.PickerChange) {
    this.setData({ roleIndex: Number(event.detail.value) })
  },
  async login() {
    this.setData({ loading: true, error: '' })
    try {
      const result = await loginWithWechat()
      if (result.bound) {
        enterAfterLogin()
      } else {
        // 当前微信绑定的账号在小程序端不可用（公司级账号）→ 服务端已发
        // bind_token，这里展开绑定表单，让用户用项目级账号改绑。
        this.setData({
          needBind: true,
          error: result.message || '',
        })
      }
    } catch (error) {
      this.setData({ error: (error as Error).message })
    } finally {
      this.setData({ loading: false })
    }
  },
  async bind() {
    if (!this.data.username || !this.data.password) {
      this.setData({ error: '请输入现有广东建设智慧监理账号和密码' })
      return
    }
    this.setData({ loading: true, error: '' })
    try {
      await bindAccount(
        this.data.username,
        this.data.password,
        this.data.roles[this.data.roleIndex],
        this.data.wxNickname,
      )
      enterAfterLogin()
    } catch (error) {
      const message = (error as Error).message
      this.setData({
        error: /角色/.test(message)
          ? `${message}。角色由账号在系统中登记的信息决定，不确定请联系管理员。`
          : message,
      })
    } finally {
      this.setData({ loading: false })
    }
  },

  goRegister() {
    wx.navigateTo({ url: '/pages/register/index' })
  },

  goForgot() {
    wx.navigateTo({ url: '/pages/forgot/index' })
  },
}))
