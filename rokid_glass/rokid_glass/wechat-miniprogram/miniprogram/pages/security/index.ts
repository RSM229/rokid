import { getSecurityQuestions, setSecurityQuestions } from '../../services/auth'
import { session } from '../../services/session'
import { utf8ByteLength } from '../../utils/text'
import { themed } from '../../utils/theme'

interface QuestionItem {
  question: string
  answer: string
}

/**
 * 密保问题设置 —— 「忘记密码」自助重置的身份校验依据。
 * 答案只存哈希，界面不回显；修改时需重新填写全部答案。
 */
Page(themed({
  data: {
    loading: true,
    saving: false,
    isSet: false,
    currentPassword: '',
    questions: [{ question: '', answer: '' }] as QuestionItem[],
    error: '',
  },

  onLoad() {
    if (!session.getToken()) {
      wx.reLaunch({ url: '/pages/login/index' })
      return
    }
    void this.load()
  },

  async load() {
    this.setData({ loading: true })
    try {
      const res = await getSecurityQuestions()
      const questions: QuestionItem[] = (res.questions || []).length
        ? res.questions.map((question) => ({ question, answer: '' }))
        : [{ question: '', answer: '' }]
      this.setData({ isSet: Boolean(res.is_set), questions, loading: false })
    } catch {
      this.setData({ loading: false })
    }
  },

  onPasswordInput(event: WechatMiniprogram.Input) {
    this.setData({ currentPassword: event.detail.value, error: '' })
  },

  onQuestionInput(event: WechatMiniprogram.Input) {
    const index = Number(event.currentTarget.dataset.index)
    const questions = this.data.questions.slice()
    questions[index] = { ...questions[index], question: event.detail.value }
    this.setData({ questions, error: '' })
  },

  onAnswerInput(event: WechatMiniprogram.Input) {
    const index = Number(event.currentTarget.dataset.index)
    const questions = this.data.questions.slice()
    questions[index] = { ...questions[index], answer: event.detail.value }
    this.setData({ questions, error: '' })
  },

  addQuestion() {
    if (this.data.questions.length >= 3) return
    this.setData({ questions: [...this.data.questions, { question: '', answer: '' }] })
  },

  removeQuestion() {
    if (this.data.questions.length <= 1) return
    const questions = this.data.questions.slice(0, -1)
    this.setData({ questions })
  },

  async save() {
    if (this.data.saving) return
    if (!this.data.currentPassword) return this.setData({ error: '请输入当前密码' })
    const items = this.data.questions
      .map((item) => ({ question: item.question.trim(), answer: item.answer.trim() }))
      .filter((item) => item.question && item.answer)
    if (!items.length) return this.setData({ error: '请至少完整填写一组问题与答案' })
    if (items.length !== this.data.questions.length) {
      return this.setData({ error: '每个密保问题都必须填写答案' })
    }
    const texts = items.map((item) => item.question)
    if (new Set(texts).size !== texts.length) return this.setData({ error: '密保问题不能重复' })
    // 答案后端 bcrypt 哈希存储，上限 72 字节；提前拦截避免提交后才报错
    const overlong = items.find((item) => utf8ByteLength(item.answer) > 72)
    if (overlong) return this.setData({ error: '答案过长：最多 72 字节（约 24 个汉字）' })

    this.setData({ saving: true, error: '' })
    try {
      await setSecurityQuestions(this.data.currentPassword, items)
      wx.showToast({ title: '密保问题已保存', icon: 'success' })
      this.setData({ saving: false, currentPassword: '' })
      await this.load()
    } catch (error) {
      this.setData({ saving: false, error: (error as Error).message })
    }
  },
}))
