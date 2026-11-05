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
  /** 置顶：同级内置顶文件夹排最前 */
  is_pinned?: boolean
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
  pendingFiles: [] as Array<{ path: string; name: string }>,
  pendingAudio: '',
  pendingAudioName: '',
  /** 搜索防抖定时器 */
  searchTimer: null as number | null,
  data: {
    kbId: 0,
    kbName: '资料库',
    projectArchiveKbId: 0,
    projectArchiveKbName: '',
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

    /** 文档重命名：修正上传时丢了原始名的历史文档（微信内部临时名） */
    showRenameDoc: false,
    renameDocId: 0,
    renameDocName: '',
    renamingDoc: false,

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

    /** 文件夹多选模式（更多→选择 进入）：勾选后可重命名 / 删除 */
    selectMode: false,
    selectedFolderIds: [] as number[],
    // WXML 表达式不支持方法调用（indexOf 不可用），用对象映射做勾选态绑定
    selectedMap: {} as Record<string, boolean>,

    /** 多选栏置顶按钮文案：选中的文件夹全部已置顶时变为「取消置顶」 */
    pinLabel: '置顶',

    /** 回收站：软删除文档列表（可恢复 / 彻底删除） */
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
    void this.loadProjectArchiveLibrary()
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

  async loadProjectArchiveLibrary() {
    const projectId = Number(session.getUser()?.project_id || 0)
    if (!projectId) {
      this.setData({ projectArchiveKbId: 0, projectArchiveKbName: '' })
      return
    }
    try {
      // The mapping table behind this endpoint is the only source of truth.
      // Never scan /kb for a same-named/shared library: that can select a
      // personal, builtin, or unrelated project's KB.
      const library = await request<{ id: number; name: string }>('/kb/project-archive')
      this.setData({
        projectArchiveKbId: library.id || 0,
        projectArchiveKbName: library.name || '',
      })
    } catch {
      this.setData({ projectArchiveKbId: 0, projectArchiveKbName: '' })
    }
  },

  openProjectArchiveLibrary() {
    if (!this.data.projectArchiveKbId) return
    wx.navigateTo({ url: `/pages/kb/detail?id=${this.data.projectArchiveKbId}` })
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
    // 多选模式下点文件夹只切换勾选，不进入
    if (this.data.selectMode) { this.toggleFolderSelect(id); return }
    this.setData({ currentFolderId: id })
    this.refreshCurrentFolder()
  },

  goBreadcrumb(event: WechatMiniprogram.TouchEvent) {
    const id = Number(event.currentTarget.dataset.id)
    // 面包屑切换目录时退出多选，避免勾选状态与新的文件夹列表错位
    this.setData({ currentFolderId: id, selectMode: false })
    this.applySelection([])
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
        // 微信 chooseMessageFile 上传后，服务端 multipart filename 是微信内部临时名
        // （形如 d3VoY5wpTT6F1ee... 的一串字符），用户可见的原始文件名只在 f.name，
        // 必须随表单一起提交，后端才存得到真实文档名
        const picked = files.map((f) => ({ path: f.path, name: f.name || '' }))
        // 已在某个文件夹内：直接归档到当前文件夹
        if (this.data.currentFolderId) {
          this.doUpload(picked, this.data.currentFolderId)
          return
        }
        // 根目录：先选目标文件夹，保证文档一定落在文件夹里；
        // 若分类接口不可用（降级模式没有任何文件夹），退回旧的未分类上传，不阻断使用
        if (!this.data.folderOptions.length) {
          this.doUpload(picked, 0)
          return
        }
        this.pendingFiles = picked
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

  async doUpload(files: Array<{ path: string; name: string }>, folderId: number) {
    const token = session.getToken()
    const formData: Record<string, string> = {}
    if (folderId) formData.folder_id = String(folderId)
    let done = 0
    try {
      for (const file of files) {
        // 原始文件名（含扩展名）随表单提交：后端据此存显示名与 filename，
        // 否则微信会把内部临时名当成文档名
        if (file.name) formData.name = file.name
        wx.showLoading({ title: `上传中 ${done + 1}/${files.length}`, mask: true })
        await new Promise<void>((resolve, reject) => {
          wx.uploadFile({
            url: `${config.apiBaseUrl}/kb/${this.data.kbId}/documents`,
            filePath: file.path,
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
    // 二进制文档（pdf/office）不走文本解析（扫描件 PDF 提不出字，只会弹「无法解析」），
    // 直接下载后用微信原生文档查看器打开
    const binExts = ['pdf', 'doc', 'docx', 'xls', 'xlsx', 'ppt', 'pptx']
    const ext = ((doc && (doc.filename || doc.name)) || '').split('.').pop()?.toLowerCase() || ''
    if (binExts.includes(ext)) { this.downloadDoc(event); return }
    wx.showLoading({ title: '解析中...', mask: true })
    try {
      const res = await request<{ name: string; content: string }>(`/kb/${this.data.kbId}/documents/${docId}/content`)
      wx.hideLoading()
      this.setData({
        showContent: true,
        contentTitle: res.name || doc?.name || '文档',
        contentText: res.content || '该文档没有可提取的文字（可能是扫描件图片）。可点「下载」保存后查看。',
      })
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
      const files = this.pendingFiles
      this.pendingFiles = []
      if (files.length) await this.doUpload(files, targetId)
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

  /* ===== 文件夹操作下拉菜单（「更多 ▾」：选择 / 新建） ===== */
  toggleFolderMenu() { this.setData({ showFolderMenu: !this.data.showFolderMenu }) },
  closeFolderMenu() { this.setData({ showFolderMenu: false }) },
  menuNewFolder() {
    this.closeFolderMenu()
    this.setData({ showNewFolder: true, newFolderName: '' })
  },
  /* ===== 文件夹多选（更多→选择）：勾选后重命名 / 删除 ===== */
  menuSelectFolders() {
    this.closeFolderMenu()
    if (!this.data.currentSubfolders.length) {
      wx.showToast({ title: '当前目录下没有文件夹', icon: 'none' })
      return
    }
    this.setData({ selectMode: true })
    this.applySelection([])
  },
  exitSelectMode() {
    this.setData({ selectMode: false })
    this.applySelection([])
  },
  /** 同步选中 id 数组与 WXML 可用的勾选映射对象（WXML 不支持 indexOf 方法调用） */
  applySelection(ids: number[]) {
    const map: Record<string, boolean> = {}
    for (const id of ids) map[String(id)] = true
    // 选中项全部已置顶时，多选栏按钮切换为「取消置顶」
    const allPinned = ids.length > 0 && ids.every((id) => this.data.folderMap[id]?.is_pinned)
    this.setData({
      selectedFolderIds: ids,
      selectedMap: map,
      pinLabel: allPinned ? '取消置顶' : '置顶',
    })
  },
  toggleFolderSelect(folderId: number) {
    const cur = this.data.selectedFolderIds
    this.applySelection(cur.includes(folderId) ? cur.filter((i) => i !== folderId) : [...cur, folderId])
  },
  toggleSelectAllFolders() {
    const all = this.data.currentSubfolders.map((f) => f.id)
    const cur = this.data.selectedFolderIds
    const allOn = all.length > 0 && all.every((id) => cur.includes(id))
    this.applySelection(allOn ? [] : all)
  },
  renameSelectedFolder() {
    const ids = this.data.selectedFolderIds
    if (ids.length !== 1) {
      wx.showToast({ title: '重命名需要且只能选择 1 个文件夹', icon: 'none' })
      return
    }
    this.openRenameFolderById(ids[0])
  },
  deleteSelectedFolders() {
    const ids = this.data.selectedFolderIds
    if (!ids.length) {
      wx.showToast({ title: '请先勾选文件夹', icon: 'none' })
      return
    }
    const map = this.data.folderMap
    let docCount = 0
    let subCount = 0
    const walk = (nodes?: KbFolderNode[]) => {
      for (const n of nodes || []) {
        subCount += 1
        walk(n.children)
      }
    }
    for (const id of ids) {
      const folder = map[id]
      if (!folder) continue
      docCount += folder.count || 0
      walk(folder.children)
    }
    const notes: string[] = []
    if (docCount) notes.push(`${docCount} 份文档将移至各自上层，不会丢失`)
    if (subCount) notes.push(`${subCount} 个子文件夹会一并删除`)
    wx.showModal({
      title: `删除 ${ids.length} 个文件夹`,
      content: `确定删除已勾选的 ${ids.length} 个文件夹？${notes.length ? '\n' + notes.join('；') : '\n选中的文件夹均为空'}`,
      confirmText: '删除',
      confirmColor: '#d9534f',
      success: async (res) => {
        if (!res.confirm) return
        wx.showLoading({ title: '删除中...', mask: true })
        // 先子后父：删父文件夹时子文件夹会被后端级联移除，倒序可避免对已消失的节点再发请求
        const sorted = [...ids].sort((a, b) => this.folderDepth(b) - this.folderDepth(a))
        let failed = 0
        for (const id of sorted) {
          try {
            await request(`/kb/${this.data.kbId}/folders/${id}`, { method: 'DELETE' })
          } catch {
            failed += 1
          }
        }
        wx.hideLoading()
        wx.showToast({ title: failed ? `${failed} 个删除失败` : '已删除', icon: failed ? 'none' : 'success' })
        // 正停留在被删子树内时回到根，避免浏览一个已不存在的节点
        const insideDeleted = this.data.currentFolderId !== 0
          && ids.some((id) => id === this.data.currentFolderId || this.isInsideFolder(this.data.currentFolderId, id))
        this.setData({ selectMode: false, currentFolderId: insideDeleted ? 0 : this.data.currentFolderId })
        this.applySelection([])
        this.loadFolders()
      },
    })
  },
  /** 文件夹在树中的深度（根下一级为 1），用于批量删除时先子后父排序 */
  folderDepth(folderId: number): number {
    let depth = 0
    let cur: KbFolderNode | undefined = this.data.folderMap[folderId]
    while (cur && cur.parent_id) {
      depth += 1
      cur = this.data.folderMap[cur.parent_id]
    }
    return depth
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
      // 重命名是多选态里的单条操作，保存后退出多选，避免勾选状态与刷新后的列表错位
      this.setData({ showRenameFolder: false, selectMode: false })
      this.applySelection([])
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

  /* ===== 置顶：单文件夹一键切换 + 多选批量置顶/取消 ===== */
  async togglePinFolder(event: WechatMiniprogram.TouchEvent) {
    // catchtap 已阻止冒泡，不会误触发行内的「进入文件夹」
    const id = Number(event.currentTarget.dataset.id)
    const folder = this.data.folderMap[id]
    if (!folder) return
    const toPin = !folder.is_pinned
    try {
      await request(`/kb/${this.data.kbId}/folders/${id}/pin`, {
        method: 'PUT',
        data: { pinned: toPin },
      })
      this.loadFolders()
    } catch (error) {
      wx.showToast({ title: (error as Error).message, icon: 'none' })
    }
  },
  async pinSelectedFolders() {
    const ids = this.data.selectedFolderIds
    if (!ids.length) {
      wx.showToast({ title: '请先勾选文件夹', icon: 'none' })
      return
    }
    // 混合选择时统一朝「置顶」操作；全部已置顶时一键取消
    const toPin = !ids.every((id) => this.data.folderMap[id]?.is_pinned)
    wx.showLoading({ title: toPin ? '置顶中...' : '取消中...', mask: true })
    let failed = 0
    for (const id of ids) {
      try {
        await request(`/kb/${this.data.kbId}/folders/${id}/pin`, {
          method: 'PUT',
          data: { pinned: toPin },
        })
      } catch {
        failed += 1
      }
    }
    wx.hideLoading()
    wx.showToast({
      title: failed ? `${failed} 个操作失败` : toPin ? '已置顶' : '已取消置顶',
      icon: failed ? 'none' : 'success',
    })
    this.loadFolders()
  },

  /* ===== 文档重命名：修正上传时丢了原始名的历史文档 ===== */
  openRenameDoc(event: WechatMiniprogram.TouchEvent) {
    const docId = Number(event.currentTarget.dataset.id)
    const doc = this.data.docs.find((d) => d.id === docId)
    if (!doc) return
    this.setData({ showRenameDoc: true, renameDocId: docId, renameDocName: doc.name || '' })
  },
  hideRenameDoc() { this.setData({ showRenameDoc: false }) },
  onRenameDocInput(event: WechatMiniprogram.Input) { this.setData({ renameDocName: event.detail.value }) },
  async confirmRenameDoc() {
    const name = this.data.renameDocName.trim()
    if (!name) { wx.showToast({ title: '请输入文档名称', icon: 'none' }); return }
    this.setData({ renamingDoc: true })
    wx.showLoading({ title: '保存中...', mask: true })
    try {
      await request(`/kb/${this.data.kbId}/documents/${this.data.renameDocId}`, {
        method: 'PUT',
        data: { name },
      })
      wx.hideLoading()
      wx.showToast({ title: '已保存', icon: 'success' })
      this.setData({ showRenameDoc: false })
      this.refreshDocs()
    } catch (error) {
      wx.hideLoading()
      wx.showToast({ title: (error as Error).message, icon: 'none' })
    } finally {
      this.setData({ renamingDoc: false })
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

}))
