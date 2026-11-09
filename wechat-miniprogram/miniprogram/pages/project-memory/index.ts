import { request } from '../../services/request'
import { themed } from '../../utils/theme'

/**
 * 项目记忆（项目级共享记忆）· 2026-11-09 新建
 *
 * 能力：查看/维护「本项目总监设定的统一口径」——全项目成员共享，个人记忆
 * 不在这里（去「我的 - AI 记忆」）。写权限由后端按 project_members 岗位判定：
 * 总监/总监代表可直接维护，普通成员提交后进入 pending 等总监审批。
 *
 * 隔离口径：接口不接受客户端传 project_id，后端从登录态取当前项目；
 * 切到其他项目后本页展示的是另一个项目的记忆，互不串。
 */

interface ProjectMemory {
  id: number
  content: string
  kind: string
  category: string
  source: string
  status: string
  active: boolean
  pinned: boolean
  author_id?: number | null
  author_name?: string
  approved_by?: number | null
  created_at?: string | null
  updated_at?: string | null
  categoryLabel?: string
  sourceLabel?: string
}

interface Maintainer {
  user_id: number
  name: string
  role: string
  roleLabel?: string
}

interface UsageItem {
  id: number
  content: string
  status: string
  active: boolean
  use_count: number
  last_used_at: string
  lastUsedText?: string
}

interface AuditItem {
  id: number
  memory_id: number | null
  action: string
  actor_id: number | null
  actor_name: string
  detail: Record<string, any>
  created_at: string
  actionLabel?: string
  timeText?: string
}

const CATEGORY_LABELS: Record<string, string> = {
  general: '通用',
  rectification: '整改',
  record: '记录',
  writing: '行文',
}

const SOURCE_LABELS: Record<string, string> = {
  chief_declared: '总监设定',
  deputy_declared: '总监代表设定',
  member_suggested: '成员建议',
  system: '管理员设定',
}

const ROLE_LABELS: Record<string, string> = {
  chief_supervisor: '总监理工程师',
  deputy_chief: '总监代表',
}

const ACTION_LABELS: Record<string, string> = {
  create: '新增',
  suggest: '提交建议',
  update: '修改',
  enable: '启用',
  disable: '停用',
  delete: '删除',
  approve: '通过建议',
  reject: '驳回建议',
  publish: '由个人记忆发布',
  use: '被注入对话',
}

function categoryLabel(value: string): string {
  return CATEGORY_LABELS[value] || '通用'
}

function formatTime(value?: string): string {
  const text = String(value || '')
  if (!text) return ''
  return text.replace('T', ' ').slice(0, 16)
}

function decorate(item: ProjectMemory): ProjectMemory {
  return {
    ...item,
    categoryLabel: categoryLabel(item.category),
    sourceLabel: SOURCE_LABELS[item.source] || item.source,
  }
}

Page(themed({
  data: {
    projectId: 0,
    projectName: '',
    canManage: false,
    maintainers: [] as Maintainer[],
    maintainerText: '',
    memories: [] as ProjectMemory[],
    pending: [] as ProjectMemory[],
    usage: [] as UsageItem[],
    audit: [] as AuditItem[],
    loading: true,
    adding: false,
    inputValue: '',
    showCategoryPicker: false,
    categories: Object.entries(CATEGORY_LABELS).map(([value, label]) => ({ value, label })),
    selectedCategory: 'general',
    selectedCategoryLabel: categoryLabel('general'),
  },

  onShow() {
    void this.loadMemories()
  },

  async loadMemories() {
    this.setData({ loading: true })
    try {
      const res = await request<{
        project_id: number
        project_name: string
        can_manage: boolean
        maintainers: Maintainer[]
        memories: ProjectMemory[]
        pending: ProjectMemory[]
        usage: UsageItem[]
        audit: AuditItem[]
      }>('/agent/project-memories')
      const maintainers = (res.maintainers || []).map(item => ({
        ...item,
        roleLabel: ROLE_LABELS[item.role] || item.role,
      }))
      this.setData({
        projectId: res.project_id || 0,
        projectName: res.project_name || '',
        canManage: Boolean(res.can_manage),
        maintainers,
        maintainerText: maintainers
          .map(item => `${item.name}（${item.roleLabel}）`)
          .join('、'),
        memories: (res.memories || []).map(decorate),
        pending: (res.pending || []).map(decorate),
        usage: (res.usage || []).map(item => ({
          ...item,
          lastUsedText: formatTime(item.last_used_at),
        })),
        audit: (res.audit || []).map(item => ({
          ...item,
          actionLabel: ACTION_LABELS[item.action] || item.action,
          timeText: formatTime(item.created_at),
        })),
      })
    } catch (error) {
      wx.showToast({ title: (error as Error).message || '加载失败，请稍后重试', icon: 'none' })
    } finally {
      this.setData({ loading: false })
    }
  },

  onInput(event: WechatMiniprogram.Input) {
    this.setData({ inputValue: event.detail.value || '' })
  },

  onCategoryChange(event: WechatMiniprogram.TouchEvent) {
    const value = String(event.currentTarget.dataset.value || 'general')
    this.setData({
      selectedCategory: value,
      selectedCategoryLabel: categoryLabel(value),
      showCategoryPicker: false,
    })
  },

  openCategoryPicker() {
    this.setData({ showCategoryPicker: true })
  },

  closeCategoryPicker() {
    this.setData({ showCategoryPicker: false })
  },

  async submitMemory() {
    const content = this.data.inputValue.trim()
    if (!content) {
      wx.showToast({ title: '请输入项目记忆内容', icon: 'none' })
      return
    }
    this.setData({ adding: true })
    try {
      const res = await request<{ pending?: boolean; message?: string }>('/agent/project-memories', {
        method: 'POST',
        data: { content, category: this.data.selectedCategory, kind: 'rule' },
      })
      this.setData({
        inputValue: '',
        selectedCategory: 'general',
        selectedCategoryLabel: categoryLabel('general'),
        showCategoryPicker: false,
      })
      wx.showToast({ title: res.message || '已保存', icon: 'none' })
      void this.loadMemories()
    } catch (error) {
      wx.showToast({ title: (error as Error).message || '保存失败', icon: 'none' })
    } finally {
      this.setData({ adding: false })
    }
  },

  async toggleMemory(event: WechatMiniprogram.TouchEvent) {
    const id = Number(event.currentTarget.dataset.id)
    const active = !event.currentTarget.dataset.active
    try {
      await request(`/agent/project-memories/${id}`, {
        method: 'POST',
        data: { active },
      })
      this.setData({
        memories: this.data.memories.map(item => (item.id === id ? { ...item, active } : item)),
      })
      wx.showToast({ title: active ? '已启用' : '已停用', icon: 'none' })
    } catch (error) {
      wx.showToast({ title: (error as Error).message || '操作失败', icon: 'none' })
    }
  },

  removeMemory(event: WechatMiniprogram.TouchEvent) {
    const item = this.data.memories.find(
      memory => memory.id === Number(event.currentTarget.dataset.id),
    )
    if (!item) return
    wx.showModal({
      title: '删除项目记忆',
      content: `确定删除「${item.content}」吗？删除后全项目下一轮对话都不再遵守这条口径。`,
      confirmText: '删除',
      confirmColor: '#c0392b',
      success: async (res) => {
        if (!res.confirm) return
        try {
          await request(`/agent/project-memories/${item.id}`, { method: 'DELETE' })
          this.setData({
            memories: this.data.memories.filter(memory => memory.id !== item.id),
          })
          wx.showToast({ title: '已删除', icon: 'none' })
        } catch (error) {
          wx.showToast({ title: (error as Error).message || '删除失败', icon: 'none' })
        }
      },
    })
  },

  approveMemory(event: WechatMiniprogram.TouchEvent) {
    const id = Number(event.currentTarget.dataset.id)
    const approve = String(event.currentTarget.dataset.approve) === 'true'
    const item = this.data.pending.find(memory => memory.id === id)
    if (!item) return
    wx.showModal({
      title: approve ? '通过建议' : '驳回建议',
      content: approve
        ? `通过后「${item.content}」将对全项目生效。`
        : `驳回后「${item.content}」不会生效。`,
      success: async (res) => {
        if (!res.confirm) return
        try {
          await request(`/agent/project-memories/${id}/approve`, {
            method: 'POST',
            data: { approve },
          })
          wx.showToast({ title: approve ? '已通过' : '已驳回', icon: 'none' })
          void this.loadMemories()
        } catch (error) {
          wx.showToast({ title: (error as Error).message || '操作失败', icon: 'none' })
        }
      },
    })
  },

  openUserMemory() {
    wx.navigateTo({ url: '/pages/preferences/index' })
  },
}))
