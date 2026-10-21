import { bindAccount, loginWithWechat, validateInviteCode, wechatRegister } from '../../services/auth'
import { ROLE_KEYS, ROLE_LABELS } from '../../utils/role'
import { themed } from '../../utils/theme'

Page(themed({
  data: {
    mode: 'choose',                                  // choose | bind
    // choose 模式：邀请码注册
    inviteCode: '',
    realName: '',
    inviteHint: '',                                  // 校验成功的企业/项目回显
    inviteError: '',                                 // 校验失败的提示
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

  onInviteInput(event: WechatMiniprogram.Input) {
    // 输入时清掉上一次的校验结果，避免旧提示误导
    this.setData({ inviteCode: event.detail.value, inviteHint: '', inviteError: '', error: '' })
  },

  /** 失焦即校验邀请码：让用户在点「注册」前就看到会进哪家公司 */
  async onInviteBlur() {
    const code = (this.data.inviteCode || '').trim()
    if (!code) {
      this.setData({ inviteHint: '', inviteError: '' })
      return
    }
    try {
      const info = await validateInviteCode(code)
      const org = info.org_name ? `将加入企业：${info.org_name}` : '邀请码有效'
      this.setData({ inviteHint: org, inviteError: '' })
    } catch (e) {
      // validate 接口对无效码返回 400 + detail，这里落到 inviteError
      this.setData({ inviteHint: '', inviteError: (e as Error).message || '邀请码无效' })
    }
  },

  onRoleChange(event: WechatMiniprogram.PickerChange) {
    this.setData({ roleIndex: Number(event.detail.value), error: '' })
  },

  /** 微信授权 + 邀请码注册。 */
  async wechatRegister() {
    if (this.data.loading) return
    const code = (this.data.inviteCode || '').trim()
    if (!code) {
      this.setData({ error: '请先填写项目负责人发放的邀请码' })
      return
    }
    this.setData({ loading: true, error: '' })
    try {
      const loginResult = await loginWithWechat()
      if (loginResult.bound) {
        // 当前微信已绑定账号：loginWithWechat 已落地登录态，直接进入工作台
        this.setData({ loading: false })
        wx.reLaunch({ url: '/pages/agent/index' })
        return
      }
      const res = await wechatRegister(code, {
        real_name: this.data.realName || undefined,
      })
      this.setData({ loading: false })
      // 项目码 → 注册即入项目，直接进工作台；企业码 → 仍需去 join-project 填项目码
      if (res.project_id) {
        wx.showModal({
          title: '注册成功',
          content: `账号已创建并自动登录，已加入项目「${res.project_name || ''}」。`,
          showCancel: false,
          confirmText: '进入工作台',
          success: () => wx.reLaunch({ url: '/pages/agent/index' }),
        })
      } else {
        wx.showModal({
          title: '注册成功',
          content: '账号已创建并自动登录。请向项目负责人索取项目邀请码，下一步填写即可加入项目。',
          showCancel: false,
          confirmText: '去加入项目',
          success: () => wx.navigateTo({ url: '/pages/join-project/index' }),
        })
      }
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
