import { request } from '../../services/request'
import { themed } from '../../utils/theme'

interface Preference {
  id: number
  rule: string
  category: string
  source: string
  active: boolean
  project_id?: number | null
  created_at?: string
  updated_at?: string
  categoryLabel?: string
  scopeLabel?: string
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
    editingId: 0,
    inputValue: '',
    showCategoryPicker: false,
    categories: Object.entries(CATEGORY_LABELS).map(([value, label]) => ({ value, label })),
    selectedCategory: 'general',
    selectedCategoryLabel: categoryLabel('general'),
    canManageProjectMemory: false,
    projectName: '',
    projectId: 0,
    publishingId: 0,
    selectedScope: 'global',
    scopeBusyId: 0,
  },

  onShow() {
    void this.loadPreferences()
  },

  async loadPreferences() {
    this.setData({ loading: true })
    try {
      const res = await request<{
        preferences: Preference[]
        can_manage_project_memory?: boolean
        project_name?: string
        project_id?: number | null
      }>('/agent/preferences')
      const items = (res.preferences || []).map((item) => ({
        ...item,
        categoryLabel: CATEGORY_LABELS[item.category] || item.category,
        scopeLabel: item.project_id ? '本项目' : '通用',
      }))
      this.setData({
        preferences: items,
        canManageProjectMemory: Boolean(res.can_manage_project_memory),
        projectName: res.project_name || '',
        projectId: Number(res.project_id || 0),
      })
    } catch {
      wx.showToast({ title: '加载失败，请稍后重试', icon: 'none' })
    } finally {
      this.setData({ loading: false })
    }
  },

  /** 项目记忆页：查看本项目共享口径；总监/总监代表可在那边直接维护。 */
  openProjectMemory() {
    wx.navigateTo({ url: '/pages/project-memory/index' })
  },

  /**
   * 把一条个人记忆发布为项目记忆（仅总监/总监代表可用）。
   * 复制而不是引用：之后个人记忆停用，不会撤掉项目统一口径。
   */
  publishToProject(event: WechatMiniprogram.TouchEvent) {
    const item = this.data.preferences.find(
      (p) => p.id === Number(event.currentTarget.dataset.id),
    )
    if (!item) return
    wx.showModal({
      title: '发布到项目记忆',
      content: `发布后「${item.rule}」将对全项目成员生效。确定发布吗？`,
      confirmText: '发布',
      success: async (res) => {
        if (!res.confirm) return
        this.setData({ publishingId: item.id })
        try {
          await request(`/agent/preferences/${item.id}/publish-to-project`, { method: 'POST' })
          wx.showToast({ title: '已发布，全项目生效', icon: 'none' })
        } catch (error) {
          wx.showToast({ title: (error as Error).message || '发布失败', icon: 'none' })
        } finally {
          this.setData({ publishingId: 0 })
        }
      },
    })
  },

  onInput(event: WechatMiniprogram.Input) {
    this.setData({ inputValue: event.detail.value || '' })
  },

  /** 新增记忆时选择作用域：通用（跟人走）或仅本项目。 */
  chooseScope(event: WechatMiniprogram.TouchEvent) {
    const scope = String(event.currentTarget.dataset.scope || 'global')
    this.setData({ selectedScope: scope === 'project' ? 'project' : 'global' })
  },

  /** 已有记忆切换作用域：通用 ↔ 仅本项目。 */
  async toggleMemoryScope(event: WechatMiniprogram.TouchEvent) {
    const id = Number(event.currentTarget.dataset.id)
    const item = this.data.preferences.find((p) => p.id === id)
    if (!item) return
    if (!item.project_id && !this.data.projectId) {
      wx.showToast({ title: '当前没有项目，无法绑定', icon: 'none' })
      return
    }
    const targetScope = item.project_id ? 'global' : 'project'
    this.setData({ scopeBusyId: id })
    try {
      await request(`/agent/preferences/${id}/scope`, {
        method: 'POST',
        data: { scope: targetScope },
      })
      wx.showToast({
        title: targetScope === 'project' ? '已改为仅本项目生效' : '已改为通用生效',
        icon: 'none',
      })
      void this.loadPreferences()
    } catch (error) {
      wx.showToast({ title: (error as Error).message || '操作失败', icon: 'none' })
    } finally {
      this.setData({ scopeBusyId: 0 })
    }
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
      const data: Record<string, unknown> = {
        rule,
        category: this.data.selectedCategory,
        source: 'user_declared',
      }
      if (this.data.selectedScope === 'project' && this.data.projectId) {
        data.project_id = this.data.projectId
      }
      await request('/agent/preferences', { method: 'POST', data })
      this.setData({
        inputValue: '',
        selectedCategory: 'general',
        selectedScope: 'global',
        showCategoryPicker: false,
      })
      wx.showToast({ title: '已添加，AI 下次会话即生效', icon: 'none' })
      void this.loadPreferences()
    } catch (error) {
      wx.showToast({ title: (error as Error).message || '添加失败', icon: 'none' })
    } finally {
      this.setData({ adding: false })
    }
  },

  submitPreference() {
    if (this.data.editingId) return this.saveEdit()
    return this.addPreference()
  },

  startEdit(event: WechatMiniprogram.TouchEvent) {
    const item = this.data.preferences.find((p) => p.id === Number(event.currentTarget.dataset.id))
    if (!item) return
    this.setData({
      editingId: item.id,
      inputValue: item.rule,
      selectedCategory: item.category,
      selectedCategoryLabel: categoryLabel(item.category),
    })
    wx.pageScrollTo({ scrollTop: 0, duration: 200 })
  },

  cancelEdit() {
    this.setData({
      editingId: 0,
      inputValue: '',
      selectedCategory: 'general',
      selectedCategoryLabel: categoryLabel('general'),
      showCategoryPicker: false,
    })
  },

  async saveEdit() {
    const id = Number(this.data.editingId)
    const rule = this.data.inputValue.trim()
    if (!id) return
    if (!rule) {
      wx.showToast({ title: '请输入记忆内容', icon: 'none' })
      return
    }
    this.setData({ adding: true })
    try {
      await request(`/agent/preferences/${id}`, {
        method: 'PUT',
        data: { rule, category: this.data.selectedCategory },
      })
      this.cancelEdit()
      wx.showToast({ title: '记忆已更新', icon: 'none' })
      void this.loadPreferences()
    } catch (error) {
      wx.showToast({ title: (error as Error).message || '保存失败', icon: 'none' })
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
          await request(`/agent/preferences/${item.id}`, { method: 'DELETE' })
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
