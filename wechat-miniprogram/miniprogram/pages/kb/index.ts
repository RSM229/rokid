import { request } from '../../services/request'
import { session } from '../../services/session'
import { config } from '../../config'
import { themed } from '../../utils/theme'

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

interface KbFolderNode {
  id: number
  name: string
  parent_id: number | null
  is_preset: boolean
  count: number
  children: KbFolderNode[]
}

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
  /** 等待选好文件夹后再上传的暂存内容（不放进 data，避免无谓的渲染层序列化） */
  pendingFiles: [] as string[],
  pendingAudio: '',
  pendingAudioName: '',
  /** 搜索防抖定时器 */
  searchTimer: null as number | null,
  data: {
    kbId: 0,
    kbName: '资料库',
    canUpload: false,
    resolving: true,

    docs: [] as Array<KbDoc & { sizeText: string }>,
    docsLoading: false,

    folders: [] as KbFolderNode[],
    folderMap: {} as Record<number, KbFolderNode>,
    /** 拍平后的文件夹清单（带层级缩进），供上传选目标 / 移动文档时选择 */
    folderOptions: [] as Array<{ id: number; name: string; depth: number }>,
    currentFolderId: 0,
    breadcrumb: [] as Array<{ id: number; name: string }>,
    currentSubfolders: [] as KbFolderNode[],
    /** 后端尚未提供分类树接口时的降级标记（仍按平铺文档展示） */
    treeUnavailable: false,

    /** 搜索：输入即全局搜文档名称（不带文件夹过滤），清空恢复文件夹视图 */
    searchKeyword: '',

    /** 文件夹选择器：上传前选目标文件夹 / 文档移动 */
    showPicker: false,
    pickerMode: '' as '' | 'upload' | 'audio' | 'move',
    pickerTitle: '选择文件夹',
    pickerDocId: 0,

    recording: false,
    recSec: 0,

    showContent: false,
    contentTitle: '',
    contentText: '',

    showNewFolder: false,
    newFolderName: '',
    creatingFolder: false,

    showRenameFolder: false,
    renameFolderId: 0,
    renameFolderName: '',
    renamingFolder: false,

    /** 文件夹操作下拉菜单（新建/重命名/删除） */
    showFolderMenu: false,

    /** 标签：跨文件夹的轻量分类维度（对标 ima 的 #标签） */
    tags: [] as KbTag[],
    tagFilterId: 0,
    tagFilterName: '',
    showTagManage: false,
    showTagEdit: false,
    editingDocId: 0,
    editingDocName: '',
    editTagIds: [] as number[],
    newTagName: '',
    creatingTag: false,
    savingTags: false,

    /** 回收站：软删除文档视图 */
    showTrash: false,
    trashDocs: [] as Array<KbDoc & { sizeText: string }>,
    trashLoading: false,
  },

  onShow() {
    if (!session.getToken()) {
      wx.reLaunch({ url: '/pages/login/index' })
      return
    }
    if (!this.data.kbId) this.resolveHome()
    else this.loadFolders()
  },

  onHide() {
    this.detachRecorder()
  },

  onUnload() {
    this.detachRecorder()
  },

  onPullDownRefresh() {
    this.loadFolders().finally(() => wx.stopPullDownRefresh())
  },

  attachRecorder() {
    if (this.recorderStopHandler) return
    this.recorderStopHandler = (res: Parameters<Parameters<typeof recorder.onStop>[0]>[0]) => {
      if (!this.data.recording) return
      this.stopRecTimer()
      this.setData({ recording: false })
      if (res && res.tempFilePath) this.startAudioUpload(res.tempFilePath, '录音转写')
    }
    this.recorderErrorHandler = (err: WechatMiniprogram.GeneralCallbackResult) => {
      if (!this.data.recording) return
      this.stopRecTimer()
      this.setData({ recording: false })
      const msg = (err && err.errMsg) || '录音失败'
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

  async resolveHome() {
    this.setData({ resolving: true })
    try {
      const home = await request<{ id: number; name: string; role: string }>('/kb/mine')
      this.setData({ kbId: home.id, kbName: home.name, canUpload: true })
      wx.setNavigationBarTitle({ title: home.name || '资料库' })
      await this.loadFolders()
      await this.loadTags()
    } catch (error) {
      // 后端版本较旧（尚无 /kb/mine，或分类树未上线）时降级：从资料库列表里定位个人库，
      // 保证文档、上传、录音仍可用，不把整页留成空白。
      const kb = await this.resolveHomeFallback()
      if (kb) {
        this.setData({ kbId: kb.id, kbName: kb.name, canUpload: true, treeUnavailable: true })
        wx.setNavigationBarTitle({ title: kb.name || '资料库' })
        await this.loadDocs(0)
      } else {
        wx.showToast({ title: (error as Error).message, icon: 'none' })
      }
    } finally {
      this.setData({ resolving: false })
    }
  },

  async resolveHomeFallback(): Promise<{ id: number; name: string } | null> {
    try {
      const res = await request<{ items: Array<{ id: number; name: string; kb_type?: string; role?: string; is_builtin?: boolean }> }>('/kb')
      const items = (res.items || []).filter((i) => !i.is_builtin)
      const personal = items.find((i) => i.kb_type === 'personal' && (i.role === 'owner' || i.role === 'admin'))
        || items.find((i) => i.role === 'owner' || i.role === 'admin')
        || items[0]
      return personal ? { id: personal.id, name: personal.name } : null
    } catch {
      return null
    }
  },

  async loadDocs(folderId: number = 0, tagId?: number) {
    this.setData({ docsLoading: true })
    try {
      // 标签筛选是跨文件夹的，不叠加 folder_id（folder_id=0 会被后端当成「未分类」导致漏结果）
      const data: Record<string, unknown> = tagId ? { tag_id: tagId } : { folder_id: folderId }
      const res = await request<{ items: KbDoc[] }>(
        `/kb/${this.data.kbId}/documents`,
        { data },
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
      const options: Array<{ id: number; name: string; depth: number }> = []
      const flatten = (nodes: KbFolderNode[], depth = 0) => {
        for (const n of nodes) {
          map[n.id] = n
          options.push({ id: n.id, name: n.name, depth })
          if (n.children && n.children.length) flatten(n.children, depth + 1)
        }
      }
      flatten(res.items || [])
      this.setData({ folders: res.items || [], folderMap: map, folderOptions: options, treeUnavailable: false })
      this.refreshCurrentFolder()
    } catch (error) {
      // 分类树接口不可用（后端未部署）时降级为平铺文档列表，不阻断上传与查看
      this.setData({ folders: [], folderMap: {}, folderOptions: [], treeUnavailable: true, breadcrumb: [], currentSubfolders: [] })
      await this.loadDocs(0)
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

  /** 文档变动后的刷新：标签筛选态下按标签重拉，搜索态下重搜，否则回到当前文件夹视图 */
  refreshDocs() {
    if (this.data.tagFilterId) this.loadDocs(0, this.data.tagFilterId)
    else if (this.data.searchKeyword.trim()) this.doSearch()
    else this.loadFolders()
  },

  refreshCurrentFolder() {
    const folderId = this.data.currentFolderId
    const map = this.data.folderMap
    const crumb: Array<{ id: number; name: string }> = []
    let cur: KbFolderNode | undefined = folderId ? map[folderId] : undefined
    while (cur) {
      crumb.unshift({ id: cur.id, name: cur.name })
      cur = cur.parent_id ? map[cur.parent_id] : undefined
    }
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

  uploadDoc() {
    wx.chooseMessageFile({
      count: 9,
      type: 'file',
      extension: ['docx', 'xlsx', 'pdf', 'doc', 'xls', 'txt', 'md'],
      success: (res) => {
        const files = res.tempFiles || []
        if (!files.length) return
        const paths = files.map((f) => f.path)
        // 已在某个文件夹内：直接归档到当前文件夹
        if (this.data.currentFolderId) {
          this.doUpload(paths, this.data.currentFolderId)
          return
        }
        // 根目录：先选目标文件夹，保证文档一定落在文件夹里；
        // 若分类接口不可用（降级模式没有任何文件夹），退回旧的未分类上传，不阻断使用
        if (!this.data.folderOptions.length) {
          this.doUpload(paths, 0)
          return
        }
        this.pendingFiles = paths
        this.openPicker('upload', '上传到哪个文件夹')
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

  async doUpload(paths: string[], folderId: number) {
    const token = session.getToken()
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
    } catch (error) {
      wx.hideLoading()
      wx.showToast({ title: (error as Error).message, icon: 'none' })
    }
  },

  /** 录音/音频统一入口：在文件夹内直接归档，在根目录先选文件夹 */
  startAudioUpload(path: string, name: string) {
    if (this.data.currentFolderId) {
      this.uploadAudio(path, name, this.data.currentFolderId)
      return
    }
    if (!this.data.folderOptions.length) {
      this.uploadAudio(path, name, 0)
      return
    }
    this.pendingAudio = path
    this.pendingAudioName = name
    this.openPicker('audio', '这段录音存到哪个文件夹')
  },

  async uploadAudio(path: string, name: string | undefined, folderId: number) {
    const token = session.getToken()
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
        this.startAudioUpload(f.path, base)
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
        } catch (error) {
          wx.showToast({ title: (error as Error).message, icon: 'none' })
        }
      },
    })
  },

  openPicker(mode: 'upload' | 'audio' | 'move', title: string, docId = 0) {
    if (!this.data.folderOptions.length) {
      wx.showToast({ title: '还没有文件夹，请先新建', icon: 'none' })
      return
    }
    this.setData({ showPicker: true, pickerMode: mode, pickerTitle: title, pickerDocId: docId })
  },

  hidePicker() {
    // 取消时丢弃暂存内容，避免下次误传
    this.pendingFiles = []
    this.pendingAudio = ''
    this.pendingAudioName = ''
    this.setData({ showPicker: false, pickerMode: '', pickerDocId: 0 })
  },

  async choosePickerFolder(event: WechatMiniprogram.TouchEvent) {
    const targetId = Number(event.currentTarget.dataset.id)
    const mode = this.data.pickerMode
    const docId = this.data.pickerDocId
    this.setData({ showPicker: false, pickerMode: '', pickerDocId: 0 })

    if (mode === 'upload') {
      const paths = this.pendingFiles
      this.pendingFiles = []
      if (paths.length) await this.doUpload(paths, targetId)
      return
    }
    if (mode === 'audio') {
      const path = this.pendingAudio
      const name = this.pendingAudioName
      this.pendingAudio = ''
      this.pendingAudioName = ''
      if (path) await this.uploadAudio(path, name || undefined, targetId)
      return
    }
    if (mode === 'move') await this.moveDoc(docId, targetId)
  },

  openMoveDoc(event: WechatMiniprogram.TouchEvent) {
    const docId = Number(event.currentTarget.dataset.id)
    this.openPicker('move', '移动到哪个文件夹', docId)
  },

  async moveDoc(docId: number, folderId: number) {
    wx.showLoading({ title: '移动中...', mask: true })
    try {
      await request(`/kb/${this.data.kbId}/documents/${docId}/folder`, {
        method: 'PUT',
        data: { folder_id: folderId },
      })
      wx.hideLoading()
      wx.showToast({ title: folderId ? '已移动' : '已移回未分类', icon: 'success' })
      this.refreshDocs()
    } catch (error) {
      wx.hideLoading()
      wx.showToast({ title: (error as Error).message, icon: 'none' })
    }
  },

  showNewFolder() { this.setData({ showNewFolder: true, newFolderName: '' }) },
  hideNewFolder() { this.setData({ showNewFolder: false }) },
  onNewFolderInput(event: WechatMiniprogram.Input) { this.setData({ newFolderName: event.detail.value }) },

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

  /** 判断 folderId 是否位于（或就是）ancestorId 的子树内 */
  isInsideFolder(folderId: number, ancestorId: number): boolean {
    let cur: KbFolderNode | undefined = this.data.folderMap[folderId]
    while (cur) {
      if (cur.id === ancestorId) return true
      cur = cur.parent_id ? this.data.folderMap[cur.parent_id] : undefined
    }
    return false
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

  toggleTagFilter(event: WechatMiniprogram.TouchEvent) {
    const tagId = Number(event.currentTarget.dataset.id)
    if (this.data.tagFilterId === tagId) { this.clearTagFilter(); return }
    const tag = this.data.tags.find((t) => t.id === tagId)
    this.setData({ tagFilterId: tagId, tagFilterName: tag?.name || '' })
    this.loadDocs(0, tagId)
  },
  clearTagFilter() {
    this.setData({ tagFilterId: 0, tagFilterName: '' })
    this.loadFolders()
  },

  openTagManage() { this.setData({ showTagManage: true }) },
  hideTagManage() { this.setData({ showTagManage: false }) },
  deleteTagInManage(event: WechatMiniprogram.TouchEvent) {
    const tagId = Number(event.currentTarget.dataset.id)
    const tag = this.data.tags.find((t) => t.id === tagId)
    wx.showModal({
      title: '删除标签',
      content: `确定删除标签「${tag?.name || ''}」？\n仅摘除标签，文档不会丢失。`,
      confirmText: '删除',
      confirmColor: '#d9534f',
      success: async (res) => {
        if (!res.confirm) return
        try {
          await request(`/kb/${this.data.kbId}/tags/${tagId}`, { method: 'DELETE' })
          wx.showToast({ title: '已删除', icon: 'success' })
          this.loadTags()
        } catch (error) {
          wx.showToast({ title: (error as Error).message, icon: 'none' })
        }
      },
    })
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
      this.refreshDocs()
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
        } catch (error) {
          wx.showToast({ title: (error as Error).message, icon: 'none' })
        }
      },
    })
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
}))
