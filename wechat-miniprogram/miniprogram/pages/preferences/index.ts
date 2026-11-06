import { request } from '../../services/request'
import { themed } from '../../utils/theme'

interface Preference {
  id: number
  rule: string
  category: string
  source: string
  active: boolean
  created_at?: string
  updated_at?: string
}

const CATEGORY_LABELS: Record<string, string> = {
  general: '通用',
  rectification: '整改',
  record: '记录',
  writing: '行文',
}

function categoryLabel(value: string): string {
  return CATEGORY_LABELS[value] || '通用'
}

Page(themed({
  data: {
    preferences: [] as Preference[],
    loading: true,
    adding: false,
    inputValue: '',
    showCategoryPicker: false,
    categories: Object.entries(CATEGORY_LABELS).map(([value, label]) => ({ value, label })),
    selectedCategory: 'general',
    selectedCategoryLabel: categoryLabel('general'),
  },

  onShow() {
    void this.loadPreferences()
  },

  async loadPreferences() {
    this.setData({ loading: true })
    try {
      const res = await request<{ preferences: Preference[] }>('/agent/preferences')
      const items = (res.preferences || []).map((item) => ({
        ...item,
        categoryLabel: CATEGORY_LABELS[item.category] || item.category,
      }))
      this.setData({ preferences: items })
    } catch {
      wx.showToast({ title: '加载失败，请稍后重试', icon: 'none' })
    } finally {
      this.setData({ loading: false })
    }
  },

  onInput(event: WechatMiniprogram.Input) {
    this.setData({ inputValue: event.detail.value || '' })
  },

  onCategoryChange(event: WechatMiniprogram.TouchEvent) {
    this.setData({
      selectedCategory: String(event.currentTarget.dataset.value || 'general'),
      selectedCategoryLabel: categoryLabel(String(event.currentTarget.dataset.value || 'general')),
      showCategoryPicker: false,
    })
  },

  openCategoryPicker() {
    this.setData({ showCategoryPicker: true })
  },

  closeCategoryPicker() {
    this.setData({ showCategoryPicker: false })
  },

  async addPreference() {
    const rule = this.data.inputValue.trim()
    if (!rule) {
      wx.showToast({ title: '请输入偏好内容', icon: 'none' })
      return
    }
    this.setData({ adding: true })
    try {
      await request('/agent/preferences', {
        method: 'POST',
        data: {
          rule,
          category: this.data.selectedCategory,
          source: 'user_declared',
        },
      })
      this.setData({ inputValue: '', selectedCategory: 'general', showCategoryPicker: false })
      wx.showToast({ title: '已添加，AI 下次会话即生效', icon: 'none' })
      void this.loadPreferences()
    } catch (error) {
      wx.showToast({ title: (error as Error).message || '添加失败', icon: 'none' })
    } finally {
      this.setData({ adding: false })
    }
  },

  async togglePreference(event: WechatMiniprogram.TouchEvent) {
    const id = Number(event.currentTarget.dataset.id)
    const active = !event.currentTarget.dataset.active
    try {
      await request(`/agent/preferences/${id}`, {
        method: 'POST',
        data: { active },
      })
      this.setData({
        preferences: this.data.preferences.map((p) => (p.id === id ? { ...p, active } : p)),
      })
      wx.showToast({ title: active ? '已启用' : '已停用', icon: 'none' })
    } catch (error) {
      wx.showToast({ title: (error as Error).message || '操作失败', icon: 'none' })
    }
  },

  removePreference(event: WechatMiniprogram.TouchEvent) {
    const item = this.data.preferences.find(
      (p) => p.id === Number(event.currentTarget.dataset.id),
    )
    if (!item) return
    wx.showModal({
      title: '删除偏好',
      content: `确定删除「${item.rule}」吗？删除后 AI 将不再遵守这条规则。`,
      confirmText: '删除',
      confirmColor: '#c0392b',
      success: async (res) => {
        if (!res.confirm) return
        try {
          await request(`/agent/preferences/${item.id}`, {
            method: 'POST',
            data: { active: false },
          })
          this.setData({
            preferences: this.data.preferences.filter((p) => p.id !== item.id),
          })
          wx.showToast({ title: '已删除', icon: 'none' })
        } catch (error) {
          wx.showToast({ title: (error as Error).message || '删除失败', icon: 'none' })
        }
      },
    })
  },
}))