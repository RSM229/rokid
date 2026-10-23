import { request } from '../../services/request'
import { session } from '../../services/session'
import { config } from '../../config'
import { themed } from '../../utils/theme'

interface KbDetail {
  id: number
  name: string
  description?: string
  kb_type: 'personal' | 'shared'
  role: 'owner' | 'admin' | 'editor' | 'viewer'
  is_owner: boolean
  is_builtin?: boolean
  is_public?: boolean
  invite_code?: string
  document_count: number
  member_count: number
}

interface KbTag {
  id: number
  name: string
  count: number
}

interface KbDoc {
  id: number
  name: string
  filename: string
  size_bytes: number
  uploader_name: string
  created_at?: string
  tags?: KbTag[]
}

interface KbMember {
  user_id: number
  role: string
  real_name: string
  username: string
}

interface KbFolderNode {
  id: number
  name: string
  parent_id: number | null
  is_preset: boolean
  count: number
  children: KbFolderNode[]
}

const ROLE_LABEL: Record<string, string> = {
  owner: '创建者',
  admin: '管理员',
  editor: '协作成员',
  viewer: '普通成员',
}
const ROLE_LEVEL: Record<string, number> = { viewer: 1, editor: 2, admin: 3, owner: 4 }

function formatSize(bytes: number): string {
  if (bytes < 1024) return bytes + 'B'
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + 'KB'
  return (bytes / (1024 * 1024)).toFixed(1) + 'MB'
}

const recorder = wx.getRecorderManager()
let activeStop: Parameters<typeof recorder.onStop>[0] | null = null
let activeError: Parameters<typeof recorder.onError>[0] | null = null
recorder.onStop((result) => activeStop?.(result))
recorder.onError((error) => activeError?.(error))

Page(themed({
  recTimer: null as number | null,
  recorderStopHandler: null as Parameters<typeof recorder.onStop>[0] | null,
  recorderErrorHandler: null as Parameters<typeof recorder.onError>[0] | null,
  /** 搜索防抖定时器 */
  searchTimer: null as number | null,
  data: {
    kbId: 0,
    kb: {} as KbDetail,
    roleLabel: ROLE_LABEL,
    canUpload: false,
    canManage: false,
    tab: 'docs' as 'docs' | 'qa' | 'members',

    docs: [] as Array<KbDoc & { sizeText: string }>,
    docsLoading: false,

    /** 搜索：输入即全局搜文档名称（不带文件夹过滤），清空恢复文件夹视图 */
    searchKeyword: '',

    folders: [] as KbFolderNode[],
    folderMap: {} as Record<number, KbFolderNode>,
    currentFolderId: 0,
    breadcrumb: [] as Array<{ id: number; name: string }>,
    currentSubfolders: [] as KbFolderNode[],

    recording: false,
    recSec: 0,

    question: '',
    answer: '',
    citations: [] as Array<{ id: number; name: string; snippet: string }>,
    asking: false,

    members: [] as Array<KbMember & { roleLabel: string }>,
    membersLoading: false,

    showContent: false,
    contentTitle: '',
    contentText: '',

    showAddMember: false,
    addUsername: '',
    addRoleIndex: 0,
    addRoles: ['editor', 'viewer', 'admin'],
    addRoleLabels: ['协作成员', '普通成员', '管理员'],
    adding: false,

    showRenameFolder: false,
    renameFolderId: 0,
    renameFolderName: '',
    renamingFolder: false,

    showNewFolder: false,
    newFolderName: '',
    creatingFolder: false,

    /** 文件夹操作下拉菜单（新建/重命名/删除） */
    showFolderMenu: false,

    /** 标签：跨文件夹的轻量分类维度（对标 ima 的 #标签） */
    tags: [] as KbTag[],
    showTagEdit: false,
    editingDocId: 0,
    editingDocName: '',
    editTagIds: [] as number[],
    newTagName: '',
    creatingTag: false,
    savingTags: false,

    /** 回收站（软删除文档）：切换视图、列表、恢复 / 彻底删除 */
    showTrash: false,
    trashDocs: [] as Array<KbDoc & { sizeText: string }>,
    trashLoading: false,
  },

  onLoad(options: Record<string, string>) {
    if (!session.getToken()) {
      wx.reLaunch({ url: '/pages/login/index' })
      return
    }
    const kbId = Number(options.id || 0)
    if (!kbId) {
      wx.showToast({ title: '资料库无效', icon: 'none' })
      setTimeout(() => wx.navigateBack(), 800)
      return
    }
    this.setData({ kbId })
    this.attachRecorder()
    this.loadKb()
    this.loadFolders()
    this.loadMembers()
    this.loadTags()
  },

  onShow() {
    if (this.data.kbId) this.attachRecorder()
  },

  onHide() {
    this.detachRecorder()
  },

  onUnload() {
    this.detachRecorder()
  },

  attachRecorder() {
    if (this.recorderStopHandler) return
    this.recorderStopHandler = (res: Parameters<Parameters<typeof recorder.onStop>[0]>[0]) => {
      if (!this.data.recording) return
      this.stopRecTimer()
      this.setData({ recording: false })
      if (res && res.tempFilePath) this.uploadAudio(res.tempFilePath, '录音转写')
    }
    this.recorderErrorHandler = (err: WechatMiniprogram.GeneralCallbackResult) => {
      if (!this.data.recording) return
      this.stopRecTimer()
      this.setData({ recording: false })
      const msg = (err && err.errMsg) || '录音失败'
      // 拒绝过授权 / 隐私协议未同意 → 引导去设置；其余错误照常 toast
      if (/auth|deny|privacy/i.test(msg)) {
        wx.showModal({
          title: '需要麦克风权限',
          content: '您可以在设置中授权录音，或继续使用文字和文件功能。',
          confirmText: '去设置',
          success: (result) => { if (result.confirm) wx.openSetting({}) },
        })
      } else {
        wx.showToast({ title: msg, icon: 'none' })
      }
    }
    activeStop = this.recorderStopHandler
    activeError = this.recorderErrorHandler
  },

  detachRecorder() {
    if (activeStop === this.recorderStopHandler) activeStop = null
    if (activeError === this.recorderErrorHandler) activeError = null
    this.recorderStopHandler = null
    this.recorderErrorHandler = null
    this.stopRecTimer()
    if (this.data.recording) {
      this.setData({ recording: false })
      recorder.stop()
    }
  },

  stopRecTimer() {
    if (this.recTimer !== null) {
      clearInterval(this.recTimer)
      this.recTimer = null
    }
  },

  onPullDownRefresh() {
    Promise.all([this.loadKb(), this.loadFolders(), this.loadMembers()])
      .finally(() => wx.stopPullDownRefresh())
  },

  onTab(event: WechatMiniprogram.TouchEvent) {
    this.setData({ tab: event.currentTarget.dataset.tab as 'docs' | 'qa' | 'members' })
  },

  async loadKb() {
    try {
      const kb = await request<KbDetail>(`/kb/${this.data.kbId}`)
      const level = ROLE_LEVEL[kb.role] || 0
      const builtin = !!kb.is_builtin
      this.setData({
        kb,
        canUpload: !builtin && level >= 2,
        canManage: !builtin && level >= 3,
      })
      wx.setNavigationBarTitle({ title: kb.name || '资料库' })
    } catch (error) {
      wx.showToast({ title: (error as Error).message, icon: 'none' })
    }
  },

  async loadDocs(folderId: number = 0) {
    this.setData({ docsLoading: true })
    try {
      const res = await request<{ items: KbDoc[] }>(
        `/kb/${this.data.kbId}/documents`,
        { data: { folder_id: folderId } },
      )
      this.setData({
        docs: (res.items || []).map((d) => ({ ...d, sizeText: formatSize(d.size_bytes || 0) })),
      })
    } catch (error) {
      wx.showToast({ title: (error as Error).message, icon: 'none' })
    } finally {
      this.setData({ docsLoading: false })
    }
  },

  async loadFolders() {
    try {
      const res = await request<{ items: KbFolderNode[] }>(`/kb/${this.data.kbId}/folders`)
      const map: Record<number, KbFolderNode> = {}
      const flatten = (nodes: KbFolderNode[]) => {
        for (const n of nodes) {
          map[n.id] = n
          if (n.children && n.children.length) flatten(n.children)
        }
      }
      flatten(res.items || [])
      this.setData({ folders: res.items || [], folderMap: map })
      this.refreshCurrentFolder()
    } catch (error) {
      wx.showToast({ title: (error as Error).message, icon: 'none' })
    }
  },

  onSearchInput(event: WechatMiniprogram.Input) {
    const keyword = event.detail.value || ''
    this.setData({ searchKeyword: keyword })
    if (this.searchTimer) clearTimeout(this.searchTimer)
    if (!keyword.trim()) {
      // 清空即退出搜索态，恢复当前文件夹视图
      this.loadFolders()
      return
    }
    this.searchTimer = setTimeout(() => this.doSearch(), 300) as unknown as number
  },

  onSearchConfirm() {
    if (this.searchTimer) clearTimeout(this.searchTimer)
    this.doSearch()
  },

  async doSearch() {
    const keyword = this.data.searchKeyword.trim()
    if (!keyword) return
    this.setData({ docsLoading: true })
    try {
      const res = await request<{ items: KbDoc[] }>(
        `/kb/${this.data.kbId}/documents`,
        { data: { keyword } },
      )
      this.setData({
        docs: (res.items || []).map((d) => ({ ...d, sizeText: formatSize(d.size_bytes || 0) })),
      })
    } catch (error) {
      wx.showToast({ title: (error as Error).message, icon: 'none' })
    } finally {
      this.setData({ docsLoading: false })
    }
  },

  clearSearch() {
    if (this.searchTimer) clearTimeout(this.searchTimer)
    this.setData({ searchKeyword: '' })
    this.loadFolders()
  },

  /** 文档变动后的刷新：搜索态下重搜，否则回到当前文件夹视图 */
  refreshDocs() {
    if (this.data.searchKeyword.trim()) this.doSearch()
    else this.loadFolders()
  },

  refreshCurrentFolder() {
    const folderId = this.data.currentFolderId
    const map = this.data.folderMap
    // 计算面包屑（从当前文件夹向上回溯到根）
    const crumb: Array<{ id: number; name: string }> = []
    let cur: KbFolderNode | undefined = folderId ? map[folderId] : undefined
    while (cur) {
      crumb.unshift({ id: cur.id, name: cur.name })
      cur = cur.parent_id ? map[cur.parent_id] : undefined
    }
    // 当前层级的子文件夹
    let subs: KbFolderNode[] = []
    if (folderId === 0) {
      subs = this.data.folders.filter((f) => f.parent_id === null || f.parent_id === 0)
    } else {
      const node = map[folderId]
      subs = node && node.children ? node.children : []
    }
    this.setData({ breadcrumb: crumb, currentSubfolders: subs })
    this.loadDocs(folderId)
  },

  selectFolder(event: WechatMiniprogram.TouchEvent) {
    const id = Number(event.currentTarget.dataset.id)
    this.setData({ currentFolderId: id })
    this.refreshCurrentFolder()
  },

  goBreadcrumb(event: WechatMiniprogram.TouchEvent) {
    const id = Number(event.currentTarget.dataset.id)
    this.setData({ currentFolderId: id })
    this.refreshCurrentFolder()
  },

  /** 判断 folderId 是否位于（或就是）ancestorId 的子树内 */
  isInsideFolder(folderId: number, ancestorId: number): boolean {
    let cur: KbFolderNode | undefined = this.data.folderMap[folderId]
    while (cur) {
      if (cur.id === ancestorId) return true
      cur = cur.parent_id ? this.data.folderMap[cur.parent_id] : undefined
    }
    return false
  },

  openRenameFolderById(folderId: number) {
    const folder = this.data.folderMap[folderId]
    if (!folder) return
    this.setData({ showRenameFolder: true, renameFolderId: folderId, renameFolderName: folder.name })
  },
  hideRenameFolder() { this.setData({ showRenameFolder: false, renameFolderId: 0, renameFolderName: '' }) },
  onRenameFolderInput(event: WechatMiniprogram.Input) { this.setData({ renameFolderName: event.detail.value }) },
  async confirmRenameFolder() {
    const name = this.data.renameFolderName.trim()
    if (!name) { wx.showToast({ title: '请输入文件夹名称', icon: 'none' }); return }
    this.setData({ renamingFolder: true })
    wx.showLoading({ title: '保存中...', mask: true })
    try {
      await request(`/kb/${this.data.kbId}/folders/${this.data.renameFolderId}`, {
        method: 'PUT',
        data: { name },
      })
      wx.hideLoading()
      wx.showToast({ title: '已保存', icon: 'success' })
      this.setData({ showRenameFolder: false })
      this.loadFolders()
    } catch (error) {
      wx.hideLoading()
      wx.showToast({ title: (error as Error).message, icon: 'none' })
    } finally {
      this.setData({ renamingFolder: false })
    }
  },

  /* ===== 文件夹操作下拉菜单（右上角「+ ▾」） ===== */
  toggleFolderMenu() { this.setData({ showFolderMenu: !this.data.showFolderMenu }) },
  closeFolderMenu() { this.setData({ showFolderMenu: false }) },
  menuNewFolder() {
    this.closeFolderMenu()
    this.setData({ showNewFolder: true, newFolderName: '' })
  },
  menuRenameFolder() {
    if (!this.data.currentFolderId) {
      wx.showToast({ title: '请先进入一个文件夹', icon: 'none' })
      return
    }
    this.closeFolderMenu()
    this.openRenameFolderById(this.data.currentFolderId)
  },
  menuDeleteFolder() {
    if (!this.data.currentFolderId) {
      wx.showToast({ title: '请先进入一个文件夹', icon: 'none' })
      return
    }
    this.closeFolderMenu()
    this.deleteFolderById(this.data.currentFolderId)
  },

  hideNewFolder() { this.setData({ showNewFolder: false }) },
  onNewFolderInput(event: WechatMiniprogram.Input) { this.setData({ newFolderName: event.detail.value }) },
  async confirmNewFolder() {
    const name = this.data.newFolderName.trim()
    if (!name) { wx.showToast({ title: '请输入文件夹名称', icon: 'none' }); return }
    this.setData({ creatingFolder: true })
    wx.showLoading({ title: '创建中...', mask: true })
    try {
      await request(`/kb/${this.data.kbId}/folders`, {
        method: 'POST',
        data: { name, parent_id: this.data.currentFolderId || null },
      })
      wx.hideLoading()
      wx.showToast({ title: '已创建', icon: 'success' })
      this.setData({ showNewFolder: false })
      this.loadFolders()
    } catch (error) {
      wx.hideLoading()
      wx.showToast({ title: (error as Error).message, icon: 'none' })
    } finally {
      this.setData({ creatingFolder: false })
    }
  },

  deleteFolderById(folderId: number) {
    const folder = this.data.folderMap[folderId]
    if (!folder) return
    let subCount = 0
    const walk = (nodes?: KbFolderNode[]) => {
      for (const n of nodes || []) {
        subCount += 1
        walk(n.children)
      }
    }
    walk(folder.children)
    const parent = folder.parent_id ? this.data.folderMap[folder.parent_id] : undefined
    const target = parent ? `「${parent.name}」` : '未分类'
    let content = folder.count
      ? `其中 ${folder.count} 份文档将移至${target}，不会丢失`
      : '该文件夹为空'
    if (subCount) content += `；${subCount} 个子文件夹会一并删除`
    wx.showModal({
      title: '删除文件夹',
      content: `确定删除「${folder.name}」？\n${content}`,
      confirmText: '删除',
      confirmColor: '#d9534f',
      success: async (res) => {
        if (!res.confirm) return
        try {
          await request(`/kb/${this.data.kbId}/folders/${folderId}`, { method: 'DELETE' })
          wx.showToast({ title: '已删除', icon: 'success' })
          // 正停留在被删子树内时退回它的上层，避免浏览一个已不存在的节点
          if (this.data.currentFolderId && this.isInsideFolder(this.data.currentFolderId, folderId)) {
            this.setData({ currentFolderId: folder.parent_id || 0 })
          }
          this.loadFolders()
        } catch (error) {
          wx.showToast({ title: (error as Error).message, icon: 'none' })
        }
      },
    })
  },

  uploadDoc() {
    wx.chooseMessageFile({
      count: 9,
      type: 'file',
      extension: ['docx', 'xlsx', 'pdf', 'doc', 'xls', 'txt', 'md'],
      success: (res) => {
        const files = res.tempFiles || []
        if (!files.length) return
        this.doUpload(files.map((f) => f.path))
      },
      fail: (err) => {
        wx.showModal({
          title: '选择文件失败',
          content: (err && err.errMsg) || '请先把文件发送到微信聊天，再从“聊天文件”中选择；开发者工具请改用真机预览。',
          showCancel: false,
        })
      },
    })
  },

  async doUpload(paths: string[]) {
    const token = session.getToken()
    const folderId = this.data.currentFolderId
    const formData = folderId ? { folder_id: String(folderId) } : {}
    let done = 0
    try {
      for (const path of paths) {
        wx.showLoading({ title: `上传中 ${done + 1}/${paths.length}`, mask: true })
        await new Promise<void>((resolve, reject) => {
          wx.uploadFile({
            url: `${config.apiBaseUrl}/kb/${this.data.kbId}/documents`,
            filePath: path,
            name: 'file',
            formData,
            header: token ? { Authorization: `Bearer ${token}` } : {},
            success(res) {
              if (res.statusCode >= 200 && res.statusCode < 300) { resolve(); return }
              let detail = `上传失败（HTTP ${res.statusCode}）`
              try {
                const data = JSON.parse(res.data || '{}')
                if (data && data.detail) detail = typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail)
              } catch { /* ignore */ }
              reject(new Error(detail))
            },
            fail(err) { reject(new Error((err && err.errMsg) || '上传失败')) },
          })
        })
        done += 1
      }
      wx.hideLoading()
      wx.showToast({ title: '上传完成', icon: 'success' })
      this.loadFolders()
      this.loadKb()
    } catch (error) {
      wx.hideLoading()
      wx.showToast({ title: (error as Error).message, icon: 'none' })
    }
  },

  async uploadAudio(path: string, name?: string) {
    const token = session.getToken()
    const folderId = this.data.currentFolderId
    const formData: Record<string, string> = {}
    if (name) formData.name = name
    if (folderId) formData.folder_id = String(folderId)
    wx.showLoading({ title: '语音转写中...', mask: true })
    try {
      await new Promise<void>((resolve, reject) => {
        wx.uploadFile({
          url: `${config.apiBaseUrl}/kb/${this.data.kbId}/documents/audio`,
          filePath: path,
          name: 'file',
          formData,
          header: token ? { Authorization: `Bearer ${token}` } : {},
          success(res) {
            if (res.statusCode >= 200 && res.statusCode < 300) { resolve(); return }
            let detail = `转写失败（HTTP ${res.statusCode}）`
            try {
              const data = JSON.parse(res.data || '{}')
              if (data && data.detail) detail = typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail)
            } catch { /* ignore */ }
            reject(new Error(detail))
          },
          fail(err) { reject(new Error((err && err.errMsg) || '上传失败')) },
        })
      })
      wx.hideLoading()
      wx.showToast({ title: '已转写入库', icon: 'success' })
      this.loadFolders()
      this.loadKb()
    } catch (error) {
      wx.hideLoading()
      wx.showToast({ title: (error as Error).message, icon: 'none' })
    }
  },

  toggleRecord() {
    if (!this.data.canUpload) return
    if (this.data.recording) {
      recorder.stop()
      return
    }
    // 不做 wx.authorize 预检（对隐私类 scope 从不弹窗、未授权时必 fail），
    // 直接 recorder.start() 让微信官方「隐私弹窗 → 系统授权」链路接管；
    // 拒绝过的兜底在 attachRecorder 的 onError 里。
    this.setData({ recording: true, recSec: 0 })
    recorder.start({
      duration: 600000,
      format: 'mp3',
      sampleRate: 16000,
      numberOfChannels: 1,
      encodeBitRate: 48000,
    })
    this.recTimer = setInterval(() => this.setData({ recSec: this.data.recSec + 1 }), 1000) as unknown as number
  },

  chooseAudio() {
    wx.chooseMessageFile({
      count: 1,
      type: 'file',
      extension: ['mp3', 'm4a', 'aac', 'wav', 'webm', 'ogg', 'amr'],
      success: (res) => {
        const f = res.tempFiles && res.tempFiles[0]
        if (!f) { wx.showToast({ title: '未选择音频', icon: 'none' }); return }
        const base = (f.name || '录音').replace(/\.[^.]+$/, '')
        this.uploadAudio(f.path, base)
      },
      fail: (err) => {
        wx.showModal({
          title: '选择音频失败',
          content: (err && err.errMsg) || '请先把音频发送到微信聊天，再从“聊天文件”中选择；开发者工具请改用真机预览。',
          showCancel: false,
        })
      },
    })
  },

  async openContent(event: WechatMiniprogram.TouchEvent) {
    const docId = Number(event.currentTarget.dataset.id)
    const doc = this.data.docs.find((d) => d.id === docId)
    wx.showLoading({ title: '解析中...', mask: true })
    try {
      const res = await request<{ name: string; content: string }>(`/kb/${this.data.kbId}/documents/${docId}/content`)
      wx.hideLoading()
      this.setData({ showContent: true, contentTitle: res.name || doc?.name || '文档', contentText: res.content || '（无法解析该文档内容）' })
    } catch (error) {
      wx.hideLoading()
      wx.showToast({ title: (error as Error).message, icon: 'none' })
    }
  },
  closeContent() { this.setData({ showContent: false }) },

  downloadDoc(event: WechatMiniprogram.TouchEvent) {
    const docId = Number(event.currentTarget.dataset.id)
    const doc = this.data.docs.find((d) => d.id === docId)
    const token = session.getToken()
    const url = `${config.apiBaseUrl}/kb/${this.data.kbId}/documents/${docId}/download`
    wx.showLoading({ title: '下载中', mask: true })
    wx.downloadFile({
      url,
      header: token ? { Authorization: `Bearer ${token}` } : {},
      success(res) {
        wx.hideLoading()
        if (res.statusCode !== 200) { wx.showToast({ title: '下载失败', icon: 'none' }); return }
        const ext = ((doc && doc.filename) || '').split('.').pop()?.toLowerCase() || ''
        const fileType = ext === 'pdf' ? 'pdf' : ['xlsx', 'xls'].includes(ext) ? 'xls' : 'doc'
        wx.openDocument({ filePath: res.tempFilePath, fileType, showMenu: true, fail() { wx.showToast({ title: '无法打开', icon: 'none' }) } })
      },
      fail() { wx.hideLoading(); wx.showToast({ title: '下载失败', icon: 'none' }) },
    })
  },

  deleteDoc(event: WechatMiniprogram.TouchEvent) {
    const docId = Number(event.currentTarget.dataset.id)
    const doc = this.data.docs.find((d) => d.id === docId)
    wx.showModal({
      title: '删除文档',
      content: `确定删除「${doc?.name || docId}」？`,
      success: async (res) => {
        if (!res.confirm) return
        try {
          await request(`/kb/${this.data.kbId}/documents/${docId}`, { method: 'DELETE' })
          wx.showToast({ title: '已删除', icon: 'success' })
          this.refreshDocs()
          this.loadKb()
        } catch (error) {
          wx.showToast({ title: (error as Error).message, icon: 'none' })
        }
      },
    })
  },

  onQuestionInput(event: WechatMiniprogram.Input) { this.setData({ question: event.detail.value }) },
  async ask() {
    const question = this.data.question.trim()
    if (!question) { wx.showToast({ title: '请输入问题', icon: 'none' }); return }
    this.setData({ asking: true, answer: '', citations: [] })
    wx.showLoading({ title: '思考中...', mask: true })
    try {
      const res = await request<{ answer: string; citations: Array<{ id: number; name: string; snippet: string }> }>(
        `/kb/${this.data.kbId}/query`,
        { method: 'POST', data: { question }, timeout: 120000 },
      )
      wx.hideLoading()
      this.setData({ answer: res.answer || '（无回答）', citations: res.citations || [] })
    } catch (error) {
      wx.hideLoading()
      wx.showToast({ title: (error as Error).message, icon: 'none' })
    } finally {
      this.setData({ asking: false })
    }
  },

  async loadMembers() {
    this.setData({ membersLoading: true })
    try {
      const res = await request<{ items: KbMember[] }>(`/kb/${this.data.kbId}/members`)
      this.setData({
        members: (res.items || []).map((m) => ({ ...m, roleLabel: ROLE_LABEL[m.role] || m.role })),
      })
    } catch (error) {
      wx.showToast({ title: (error as Error).message, icon: 'none' })
    } finally {
      this.setData({ membersLoading: false })
    }
  },

  showAddMember() { this.setData({ showAddMember: true, addUsername: '', addRoleIndex: 0 }) },
  hideAddMember() { this.setData({ showAddMember: false }) },
  onAddInput(event: WechatMiniprogram.Input) { this.setData({ addUsername: event.detail.value }) },
  onAddRole(event: WechatMiniprogram.PickerChange) { this.setData({ addRoleIndex: Number(event.detail.value) }) },
  async confirmAddMember() {
    const username = this.data.addUsername.trim()
    if (!username) { wx.showToast({ title: '请输入用户名', icon: 'none' }); return }
    this.setData({ adding: true })
    wx.showLoading({ title: '添加中...', mask: true })
    try {
      await request(`/kb/${this.data.kbId}/members`, {
        method: 'POST',
        data: { username, role: this.data.addRoles[this.data.addRoleIndex] },
      })
      wx.hideLoading()
      wx.showToast({ title: '已添加', icon: 'success' })
      this.setData({ showAddMember: false })
      this.loadMembers()
      this.loadKb()
    } catch (error) {
      wx.hideLoading()
      wx.showToast({ title: (error as Error).message, icon: 'none' })
    } finally {
      this.setData({ adding: false })
    }
  },

  removeMember(event: WechatMiniprogram.TouchEvent) {
    const userId = Number(event.currentTarget.dataset.id)
    const member = this.data.members.find((m) => m.user_id === userId)
    wx.showModal({
      title: '移除成员',
      content: `确定移除「${member?.real_name || userId}」？`,
      success: async (res) => {
        if (!res.confirm) return
        try {
          await request(`/kb/${this.data.kbId}/members/${userId}`, { method: 'DELETE' })
          wx.showToast({ title: '已移除', icon: 'success' })
          this.loadMembers()
          this.loadKb()
        } catch (error) {
          wx.showToast({ title: (error as Error).message, icon: 'none' })
        }
      },
    })
  },

  togglePublish() {
    const next = !this.data.kb.is_public
    wx.showLoading({ title: '处理中...', mask: true })
    request(`/kb/${this.data.kbId}/publish`, { method: 'POST', data: { is_public: next } })
      .then(() => {
        wx.hideLoading()
        wx.showToast({ title: next ? '已发布到广场' : '已取消发布', icon: 'success' })
        this.loadKb()
      })
      .catch((error) => {
        wx.hideLoading()
        wx.showToast({ title: (error as Error).message, icon: 'none' })
      })
  },

  async saveAnswerNote() {
    if (!this.data.answer) return
    wx.showLoading({ title: '保存中...', mask: true })
    try {
      await request('/notes', {
        method: 'POST',
        data: {
          title: `${this.data.kb.name}·${this.data.question || '问答'}`.slice(0, 60),
          content: this.data.answer,
          source_kb_id: this.data.kbId,
        },
      })
      wx.hideLoading()
      wx.showToast({ title: '已保存到笔记', icon: 'success' })
    } catch (error) {
      wx.hideLoading()
      wx.showToast({ title: (error as Error).message, icon: 'none' })
    }
  },

  showInvite() {
    wx.showLoading({ title: '生成中...', mask: true })
    request<{ invite_code: string }>(`/kb/${this.data.kbId}/invite`, { method: 'POST' })
      .then((res) => {
        wx.hideLoading()
        wx.showModal({
          title: '邀请码',
          content: `${res.invite_code}\n\n把邀请码发给同事，让对方在“资料库 → 加入共享库”里输入即可加入。`,
          showCancel: false,
        })
      })
      .catch((error) => {
        wx.hideLoading()
        wx.showToast({ title: (error as Error).message, icon: 'none' })
      })
  },

  /* ===== 标签（跨文件夹轻量分类维度，对标 ima 的 #标签） ===== */
  async loadTags() {
    try {
      const res = await request<{ items: KbTag[] }>(`/kb/${this.data.kbId}/tags`)
      this.setData({ tags: res.items || [] })
    } catch {
      /* 标签接口不可用时不阻断页面其余功能 */
    }
  },

  openTagEdit(event: WechatMiniprogram.TouchEvent) {
    const docId = Number(event.currentTarget.dataset.id)
    const doc = this.data.docs.find((d) => d.id === docId)
    const currentTagIds = (doc?.tags || []).map((t) => t.id)
    this.setData({
      showTagEdit: true,
      editingDocId: docId,
      editingDocName: doc?.name || '',
      editTagIds: currentTagIds,
      newTagName: '',
    })
  },
  cancelTagEdit() { this.setData({ showTagEdit: false }) },
  toggleEditTag(event: WechatMiniprogram.TouchEvent) {
    const tagId = Number(event.currentTarget.dataset.id)
    const cur = this.data.editTagIds
    this.setData({
      editTagIds: cur.includes(tagId) ? cur.filter((i) => i !== tagId) : [...cur, tagId],
    })
  },
  onNewTagInput(event: WechatMiniprogram.Input) { this.setData({ newTagName: event.detail.value }) },
  async createTagInline() {
    const name = this.data.newTagName.trim()
    if (!name) { wx.showToast({ title: '请输入标签名称', icon: 'none' }); return }
    this.setData({ creatingTag: true })
    try {
      const tag = await request<{ id: number; name: string }>(`/kb/${this.data.kbId}/tags`, {
        method: 'POST',
        data: { name },
      })
      const editTagIds = this.data.editTagIds.includes(tag.id)
        ? this.data.editTagIds
        : [...this.data.editTagIds, tag.id]
      await this.loadTags()
      this.setData({ newTagName: '', creatingTag: false, editTagIds })
    } catch (error) {
      this.setData({ creatingTag: false })
      wx.showToast({ title: (error as Error).message, icon: 'none' })
    }
  },
  async confirmSaveDocTags() {
    this.setData({ savingTags: true })
    wx.showLoading({ title: '保存中...', mask: true })
    try {
      await request(`/kb/${this.data.kbId}/documents/${this.data.editingDocId}/tags`, {
        method: 'PUT',
        data: { tag_ids: this.data.editTagIds },
      })
      wx.hideLoading()
      wx.showToast({ title: '已保存', icon: 'success' })
      this.setData({ showTagEdit: false })
      this.loadFolders()
      this.loadTags()
    } catch (error) {
      wx.hideLoading()
      wx.showToast({ title: (error as Error).message, icon: 'none' })
    } finally {
      this.setData({ savingTags: false })
    }
  },

  /* ===== 回收站（软删除文档：可恢复 / 彻底删除） ===== */
  toggleTrash() {
    const next = !this.data.showTrash
    this.setData({ showTrash: next })
    if (next) this.loadTrash()
  },
  async loadTrash() {
    this.setData({ trashLoading: true })
    try {
      const res = await request<{ items: KbDoc[] }>(`/kb/${this.data.kbId}/trash`)
      this.setData({
        trashDocs: (res.items || []).map((d) => ({ ...d, sizeText: formatSize(d.size_bytes || 0) })),
      })
    } catch (error) {
      wx.showToast({ title: (error as Error).message, icon: 'none' })
    } finally {
      this.setData({ trashLoading: false })
    }
  },
  async restoreDoc(event: WechatMiniprogram.TouchEvent) {
    const docId = Number(event.currentTarget.dataset.id)
    try {
      await request(`/kb/${this.data.kbId}/documents/${docId}/restore`, { method: 'POST' })
      wx.showToast({ title: '已恢复', icon: 'success' })
      this.loadTrash()
      this.loadKb()
    } catch (error) {
      wx.showToast({ title: (error as Error).message, icon: 'none' })
    }
  },
  purgeDoc(event: WechatMiniprogram.TouchEvent) {
    const docId = Number(event.currentTarget.dataset.id)
    const doc = this.data.trashDocs.find((d) => d.id === docId)
    wx.showModal({
      title: '彻底删除',
      content: `确定彻底删除「${doc?.name || docId}」？\n此操作不可恢复，磁盘文件将被移除。`,
      confirmText: '彻底删除',
      confirmColor: '#d9534f',
      success: async (res) => {
        if (!res.confirm) return
        try {
          await request(`/kb/${this.data.kbId}/trash/${docId}`, { method: 'DELETE' })
          wx.showToast({ title: '已彻底删除', icon: 'success' })
          this.loadTrash()
          this.loadKb()
        } catch (error) {
          wx.showToast({ title: (error as Error).message, icon: 'none' })
        }
      },
    })
  },

  onShareAppMessage() {
    return {
      title: `邀你加入资料库：${this.data.kb.name || ''}`,
      path: `/pages/kb/detail?id=${this.data.kbId}`,
    }
  },
}))
