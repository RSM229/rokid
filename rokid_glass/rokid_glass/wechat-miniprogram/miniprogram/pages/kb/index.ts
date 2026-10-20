import { request } from '../../services/request'
import { session } from '../../services/session'
import { getCached, CACHE_KEYS, invalidateCache } from '../../services/cache'
import { themed } from '../../utils/theme'

interface KbItem {
  id: number
  name: string
  description?: string
  kb_type: 'personal' | 'shared'
  owner_id: number
  role: 'owner' | 'admin' | 'editor' | 'viewer'
  is_owner: boolean
  is_builtin?: boolean
  is_public?: boolean
  document_count: number
  member_count: number
  updated_at?: string
}

interface PlazaItem {
  id: number
  name: string
  description?: string
  owner_name: string
  is_builtin?: boolean
  document_count: number
  member_count: number
  joined: boolean
  my_role?: string
}

const ROLE_LABEL: Record<string, string> = {
  owner: '创建者',
  admin: '管理员',
  editor: '协作成员',
  viewer: '普通成员',
}

Page(themed({
  data: {
    tab: 'mine' as 'mine' | 'shared' | 'plaza',
    all: [] as KbItem[],
    list: [] as KbItem[],
    plaza: [] as PlazaItem[],
    loading: false,
    plazaLoading: false,
    roleLabel: ROLE_LABEL,
    showCreate: false,
    createName: '',
    createType: 'personal' as 'personal' | 'shared',
    createDesc: '',
    creating: false,
    showJoin: false,
    joinCode: '',
    joining: false,
  },

  onLoad(options: Record<string, string>) {
    if (options && (options.tab === 'shared' || options.tab === 'plaza')) {
      this.setData({ tab: options.tab })
    }
  },

  onShow() {
    if (!session.getToken()) {
      wx.reLaunch({ url: '/pages/login/index' })
      return
    }
    this.load()
    if (this.data.tab === 'plaza') this.loadPlaza()
  },

  onPullDownRefresh() {
    const jobs: Promise<unknown>[] = [this.load()]
    if (this.data.tab === 'plaza') jobs.push(this.loadPlaza())
    Promise.all(jobs).finally(() => wx.stopPullDownRefresh())
  },

  applyFilter() {
    if (this.data.tab === 'plaza') {
      this.setData({ list: this.data.plaza as unknown as KbItem[] })
      return
    }
    const filtered = this.data.tab === 'shared'
      ? this.data.all.filter((item) => item.kb_type === 'shared')
      : this.data.all
    this.setData({ list: filtered })
  },

  onTab(event: WechatMiniprogram.TouchEvent) {
    const tab = event.currentTarget.dataset.tab as 'mine' | 'shared' | 'plaza'
    this.setData({ tab })
    if (tab === 'plaza') this.loadPlaza()
    else this.applyFilter()
  },

  async loadPlaza() {
    this.setData({ plazaLoading: true })
    try {
      const result = await getCached<{ items: PlazaItem[] }>(
        CACHE_KEYS.KB_PLAZA,
        () => request<{ items: PlazaItem[] }>('/kb/plaza'),
      )
      this.setData({ plaza: result.items || [] })
      if (this.data.tab === 'plaza') this.applyFilter()
    } catch (error) {
      wx.showToast({ title: (error as Error).message, icon: 'none' })
    } finally {
      this.setData({ plazaLoading: false })
    }
  },

  async subscribe(event: WechatMiniprogram.TouchEvent) {
    const kbId = Number(event.currentTarget.dataset.id)
    const item = this.data.plaza.find((it) => it.id === kbId)
    if (item && item.joined) {
      wx.navigateTo({ url: `/pages/kb/detail?id=${kbId}` })
      return
    }
    wx.showLoading({ title: '订阅中...', mask: true })
    try {
      await request(`/kb/${kbId}/subscribe`, { method: 'POST' })
      invalidateCache(CACHE_KEYS.KB_PLAZA)
      invalidateCache(CACHE_KEYS.KB_LIST)
      wx.hideLoading()
      wx.showToast({ title: '已订阅', icon: 'success' })
      this.loadPlaza()
      this.load()
    } catch (error) {
      wx.hideLoading()
      wx.showToast({ title: (error as Error).message, icon: 'none' })
    }
  },

  async load() {
    this.setData({ loading: true })
    try {
      const result = await getCached<{ items: KbItem[] }>(
        CACHE_KEYS.KB_LIST,
        () => request<{ items: KbItem[] }>('/kb'),
      )
      this.setData({ all: result.items || [] })
      this.applyFilter()
    } catch (error) {
      wx.showToast({ title: (error as Error).message, icon: 'none' })
    } finally {
      this.setData({ loading: false })
    }
  },

  open(event: WechatMiniprogram.TouchEvent) {
    wx.navigateTo({ url: `/pages/kb/detail?id=${event.currentTarget.dataset.id}` })
  },

  showCreate() { this.setData({ showCreate: true, createName: '', createDesc: '', createType: 'personal' }) },
  hideCreate() { this.setData({ showCreate: false }) },
  onCreateInput(event: WechatMiniprogram.Input) {
    this.setData({ [event.currentTarget.dataset.field as string]: event.detail.value })
  },
  onCreateType(event: WechatMiniprogram.TouchEvent) {
    this.setData({ createType: event.currentTarget.dataset.type as 'personal' | 'shared' })
  },
  async confirmCreate() {
    const name = this.data.createName.trim()
    if (!name) { wx.showToast({ title: '请输入资料库名称', icon: 'none' }); return }
    this.setData({ creating: true })
    wx.showLoading({ title: '创建中...', mask: true })
    try {
      await request('/kb', {
        method: 'POST',
        data: { name, kb_type: this.data.createType, description: this.data.createDesc.trim() || undefined },
      })
      invalidateCache(CACHE_KEYS.KB_LIST)
      wx.hideLoading()
      wx.showToast({ title: '已创建', icon: 'success' })
      this.setData({ showCreate: false })
      this.load()
    } catch (error) {
      wx.hideLoading()
      wx.showToast({ title: (error as Error).message, icon: 'none' })
    } finally {
      this.setData({ creating: false })
    }
  },

  showJoin() { this.setData({ showJoin: true, joinCode: '' }) },
  hideJoin() { this.setData({ showJoin: false }) },
  onJoinInput(event: WechatMiniprogram.Input) { this.setData({ joinCode: event.detail.value }) },
  async confirmJoin() {
    const code = this.data.joinCode.trim()
    if (!code) { wx.showToast({ title: '请输入邀请码', icon: 'none' }); return }
    this.setData({ joining: true })
    wx.showLoading({ title: '加入中...', mask: true })
    try {
      const res = await request<{ kb_id: number }>(`/kb/join/${encodeURIComponent(code)}`, { method: 'POST' })
      wx.hideLoading()
      wx.showToast({ title: '已加入', icon: 'success' })
      this.setData({ showJoin: false })
      await this.load()
      if (res && res.kb_id) {
        wx.navigateTo({ url: `/pages/kb/detail?id=${res.kb_id}` })
      }
    } catch (error) {
      wx.hideLoading()
      wx.showToast({ title: (error as Error).message, icon: 'none' })
    } finally {
      this.setData({ joining: false })
    }
  },
}))
