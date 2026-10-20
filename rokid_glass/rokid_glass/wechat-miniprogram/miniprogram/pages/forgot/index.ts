import { forgotQuestions, forgotReset, forgotVerify } from '../../services/auth'
import { themed } from '../../utils/theme'

/**
 * 忘记密码 —— 密保问题自助重置
 * 三步：输入用户名 → 回答密保问题 → 设置新密码
 */
Page(themed({
  data: {
    step: 0,
    loading: false,
    username: '',
    questions: [] as string[],
    answers: [] as string[],
    resetToken: '',
    newPassword: '',
    confirmPassword: '',
    error: '',
  },

  onUsernameInput(event: WechatMiniprogram.Input) {
    this.setData({ username: event.detail.value, error: '' })
  },

  onAnswerInput(event: WechatMiniprogram.Input) {
    const index = Number(event.currentTarget.dataset.index)
    const answers = this.data.answers.slice()
    answers[index] = event.detail.value
    this.setData({ answers, error: '' })
  },

  onPasswordInput(event: WechatMiniprogram.Input) {
    const field = event.currentTarget.dataset.field as 'newPassword' | 'confirmPassword'
    this.setData({ [field]: event.detail.value, error: '' })
  },

  /** 第一步：按用户名取回密保题目 */
  async fetchQuestions() {
    const username = this.data.username.trim()
    if (!username) return this.setData({ error: '请输入用户名' })
    this.setData({ loading: true, error: '' })
    try {
      const res = await forgotQuestions(username)
      if (!res.available) {
        this.setData({ error: res.message || '该账号未设置密保问题，请联系管理员' })
        return
      }
      this.setData({
        username,
        questions: res.questions || [],
        answers: new Array((res.questions || []).length).fill(''),
        step: 1,
      })
    } catch (error) {
      this.setData({ error: (error as Error).message })
    } finally {
      this.setData({ loading: false })
    }
  },

  /** 第二步：提交密保答案换取重置凭证 */
  async verifyAnswers() {
    if (this.data.answers.some((item) => !String(item || '').trim())) {
      return this.setData({ error: '请填写全部密保答案' })
    }
    this.setData({ loading: true, error: '' })
    try {
      const res = await forgotVerify(this.data.username, this.data.answers)
      this.setData({ resetToken: res.reset_token, step: 2 })
    } catch (error) {
      this.setData({ error: (error as Error).message })
    } finally {
      this.setData({ loading: false })
    }
  },

  /** 第三步：凭重置凭证设置新密码 */
  async resetPassword() {
    if (this.data.newPassword.length < 8) return this.setData({ error: '新密码至少 8 位' })
    if (this.data.newPassword !== this.data.confirmPassword) {
      return this.setData({ error: '两次输入的密码不一致' })
    }
    this.setData({ loading: true, error: '' })
    try {
      await forgotReset(this.data.resetToken, this.data.newPassword)
      this.setData({ step: 3 })
      wx.showToast({ title: '密码已重置', icon: 'success' })
    } catch (error) {
      this.setData({ error: (error as Error).message })
      // 凭证失效等异常 → 回到密保验证步重新获取
      this.setData({ resetToken: '', step: 1 })
    } finally {
      this.setData({ loading: false })
    }
  },

  goNext() {
    if (this.data.step === 0) return void this.fetchQuestions()
    if (this.data.step === 1) return void this.verifyAnswers()
    return void this.resetPassword()
  },

  goPrev() {
    this.setData({ step: Math.max(0, this.data.step - 1), error: '' })
  },

  goLogin() {
    wx.reLaunch({ url: '/pages/login/index' })
  },
}))
