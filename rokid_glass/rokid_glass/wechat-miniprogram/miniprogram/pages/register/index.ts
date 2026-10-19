import { bindAccount, loginWithWechat, wechatRegister } from '../../services/auth'
import { ROLE_KEYS, ROLE_LABELS } from '../../utils/role'
import { themed } from '../../utils/theme'

Page(themed({
  data: {
    mode: 'choose',                                  // choose | bind
    username: '',
    password: '',
    roleIndex: 3,                                    // 默认监理员（ROLE_KEYS 顺序见 utils/role.ts）
    roles: ROLE_KEYS,                                // 英文 key，提交用
    roleOptions: ROLE_KEYS.map((k) => ROLE_LABELS[k] || k), // 中文展示
    loading: false,
    error: '',
  },

  onModeChoose() {
    this.setData({ mode: 'choose', error: '' })
  },

  onModeBind() {
    this.setData({ mode: 'bind', error: '' })
  },

  onInput(event: WechatMiniprogram.Input) {
    const field = event.currentTarget.dataset.field as string
    this.setData({ [field]: event.detail.value, error: '' })
  },

  onRoleChange(event: WechatMiniprogram.PickerChange) {
    this.setData({ roleIndex: Number(event.detail.value), error: '' })
  },

  /** 路径 A：微信授权即注册（零短信成本）。 */
  async wechatRegister() {
    if (this.data.loading) return
    this.setData({ loading: true, error: '' })
    try {
      const loginResult = await loginWithWechat()
      if (loginResult.bound) {
        // 当前微信已绑定账号：loginWithWechat 已落地登录态，直接进入工作台
        this.setData({ loading: false })
        wx.reLaunch({ url: '/pages/agent/index' })
        return
      }
      await wechatRegister()
      this.setData({ loading: false })
      wx.showModal({
        title: '注册成功',
        content: '账号已创建并自动登录。项目岗位由项目负责人发放项目邀请码，下一步填写即可加入项目。',
        showCancel: false,
        confirmText: '去加入项目',
        success: () => {
          wx.navigateTo({ url: '/pages/join-project/index' })
        },
      })
    } catch (error) {
      this.setData({ error: (error as Error).message, loading: false })
    }
  },

  /** 绑定已有监理通账号到本微信（secondary 路径）。 */
  async bindAndLogin() {
    if (this.data.loading) return
    const { username, password, roleIndex, roles } = this.data
    if (!username.trim()) return this.setData({ error: '请输入用户名' })
    if (!password || password.length < 8) return this.setData({ error: '密码至少8位' })
    const role = roles[roleIndex]
    this.setData({ loading: true, error: '' })
    try {
      await bindAccount(username.trim(), password, role)
      this.setData({ loading: false })
      wx.reLaunch({ url: '/pages/agent/index' })
    } catch (error) {
      this.setData({ error: (error as Error).message, loading: false })
    }
  },

  goLogin() {
    wx.navigateBack()
  },
}))
