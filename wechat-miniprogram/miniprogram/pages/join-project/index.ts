import { joinProjectByInvite } from '../../services/auth'
import { themed } from '../../utils/theme'

Page(themed({
  data: {
    code: '',
    loading: false,
    error: '',
    result: null as {
      project_name?: string
      role_label?: string
    } | null,
  },

  onInput(event: WechatMiniprogram.Input) {
    const field = event.currentTarget.dataset.field as string
    this.setData({ [field]: event.detail.value, error: '', result: null })
  },

  async submit() {
    if (this.data.loading) return
    const code = (this.data.code || '').trim()
    if (!code) {
      return this.setData({ error: '请输入项目负责人发放的项目邀请码' })
    }
    this.setData({ loading: true, error: '' })
    try {
      const res = await joinProjectByInvite(code)
      this.setData({
        loading: false,
        result: { project_name: res.project_name, role_label: res.role_label },
      })
      wx.showModal({
        title: '已加入项目',
        content: `你已加入项目「${res.project_name || ''}」，岗位：${res.role_label || res.role}。`,
        showCancel: false,
        confirmText: '进入工作台',
        success: () => {
          wx.reLaunch({ url: '/pages/agent/index' })
        },
      })
    } catch (error) {
      this.setData({ error: (error as Error).message, loading: false })
    }
  },
}))
