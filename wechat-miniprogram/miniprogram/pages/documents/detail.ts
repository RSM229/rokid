import { request } from '../../services/request'
import { session } from '../../services/session'
import { config, absoluteMediaUrl } from '../../config'
import { themed } from '../../utils/theme'
import { isChiefRole } from '../../utils/role'

interface DocumentDetail {
  id: number
  doc_type: string
  doc_number?: string
  title?: string
  status: string
  content?: Record<string, unknown>
  section?: string
  location?: string
  weather?: string
  attached_images?: string[]
  pdf_path?: string
  created_at?: string
  signed_pdf_path?: string
  updated_at?: string | null
  archive_sync_status?: string | null
  archive_sync_error?: string | null
  can_retry_archive?: boolean
}

const SUBMITTABLE_STATUS = '草稿'

const CONTENT_LABELS: Record<string, string> = {
  record: '检查记录', method: '检查方法', basis: '检查依据', conclusion: '检查结论', handling: '处理记录',
  content: '记录内容', problems: '发现的问题及处理情况', treatment: '处理措施', worker_count: '施工人数',
  start_time: '开始时间', end_time: '结束时间', station_part: '旁站关键部位', station_process: '旁站关键工序',
  acceptance_content: '验收内容', acceptance_type: '验收类型', acceptance_result: '验收结论', acceptance_participants: '验收单位及人员',
  supervision_org: '项目监理机构', supervision_unit: '监理单位', review_date: '审阅日期', parallel_section: '平行检查情况', weather_source: '气象来源',
  doc_number: '编号',
  date: '日期',
  time: '时间',
  project_name: '工程项目名称',
  supervisor: '监理人员',
  supervisor_level: '监理级别',
  section: '楼栋/楼层/部位',
  location: '位置',
  weather: '天气',
  hazard_description: '隐患描述',
  deadline: '整改期限',
  summary: '施工进展情况',
  details: '监理工作情况',
  construction_unit: '施工单位',
  material_name: '见证材料名称',
  material_spec: '规格型号',
  manufacturer: '生产厂家',
  batch_no: '批次号',
  remark: '备注',
  evidence: '证据',
  recheck_suggestion: '复查建议',
  regulation_refs: '规范依据',
  weekday: '星期',
  temperature_high: '最高气温',
  temperature_low: '最低气温',
  weather_morning: '上午天气',
  weather_afternoon: '下午天气',
  weather_night: '夜间天气',
  construction_progress: '当日施工进展情况',
  material_section: '材料进场及见证取样情况',
  supervision_section: '旁站巡视检查情况',
  acceptance_section: '验收及实体抽测情况',
  other_work_section: '其他监理工作',
  problems_section: '当日存在问题及处理情况',
  meeting_section: '会议情况',
  document_section: '重要发文',
  closure_section: '闭合记录',
  report_section: '资料上报',
  misc_section: '其他有关事项',
}

Page(themed({
  data: {
    loading: true,
    doc: null as DocumentDetail | null,
    fields: [] as { key: string; value: string }[],
    images: [] as string[],
    // 全屏图片预览（带「上一张/下一张」按钮，替代原生 wx.previewImage）
    imageViewerVisible: false,
    imageViewerUrls: [] as string[],
    imageViewerCurrent: 0,
    hasPdf: false,
    submitting: false,
    generatingPdf: false,
    canApprove: false,
    approving: false,
    contentEditor: [] as { key: string; label: string; value: string }[],
    signSlots: [] as { key: string; label: string; mode?: string; completed?: boolean; completion_type?: string | null }[], signHistory: [] as { slot: string; label: string; signer?: string; confirmed_by?: string; signed_at: string; revision: number; current: boolean; type?: string; status?: string }[],
    projectSealId: 0, signatures: [] as { id: number; name: string }[],
    showSign: false, signing: false, uploadingSignature: false, signPassword: '', signSlotIndex: 0, signSignatureIndex: 0,
    signingMode: 'uploaded', signingModeIndex: 1,
    signatureTempPath: '', signatureUploadedId: 0,
  },

  onLoad(options: Record<string, string>) {
    const id = Number(options.id)
    if (!id) {
      wx.showToast({ title: '缺少文书ID', icon: 'none' })
      wx.navigateBack()
      return
    }
    this.loadDoc(id, true)
  },

  onPullDownRefresh() {
    const id = this.data.doc?.id
    if (id) {
      this.loadDoc(id).finally(() => wx.stopPullDownRefresh())
    } else {
      wx.stopPullDownRefresh()
    }
  },

  /** autoOpenFile=true 时（仅首次进入页面），加载完成后直接打开文件内容预览，不再停留在文件卡片页 */
  async loadDoc(id: number, autoOpenFile = false) {
    this.setData({ loading: true })
    try {
      const doc = await request<DocumentDetail>(`/document/${id}`)
      const fields = Object.entries(doc.content || {})
        .filter(([key, value]) => {
          const label = CONTENT_LABELS[key]
          return label && value !== null && value !== undefined && value !== ''
        })
        .map(([key, value]) => {
          const label = CONTENT_LABELS[key]!
          const text = Array.isArray(value) ? value.join('；') : String(value)
          return { key: label, value: text }
        })
      const images = (doc.attached_images || [])
        .map((path) => absoluteMediaUrl(path))
        .filter(Boolean)
      const user = session.getUser()
      // S2：user.role 是英文 key，比较必须用 key（用中文会静默永假）
      const canApprove = doc.status === '待审批'
        && (isChiefRole(user?.role) || (user?.permissions || []).includes('document_approve'))
      const signoffs = await request<{ slots: { key: string; label: string; mode?: string; completed?: boolean; completion_type?: string | null }[]; history: { slot: string; label: string; signer?: string; confirmed_by?: string; signed_at: string; revision: number; current: boolean; type?: string; status?: string }[]; project_seal_signature_id?: number }>(`/documents/${id}/signoffs`)
      const editableKeys = ['temperature_high', 'temperature_low', 'weather_morning', 'weather_afternoon', 'weather_night', 'construction_progress', 'material_section', 'supervision_section', 'parallel_section', 'acceptance_section', 'other_work_section', 'problems_section', 'meeting_section', 'document_section', 'closure_section', 'report_section', 'misc_section']
      const contentEditor = doc.doc_type === '监理日志' ? editableKeys.map(key => ({ key, label: CONTENT_LABELS[key] || key, value: String(doc.content?.[key] ?? '') })) : []
      this.setData({ doc, fields, images, contentEditor, signSlots: signoffs.slots, signHistory: signoffs.history, projectSealId: signoffs.project_seal_signature_id || 0, hasPdf: Boolean(doc.signed_pdf_path || doc.pdf_path), canApprove, loading: false })
      if (autoOpenFile) this.onPdfTap()
    } catch (error) {
      this.setData({ loading: false })
      wx.showModal({ title: '加载失败', content: (error as Error).message, showCancel: false })
    }
  },

  onContentInput(event: WechatMiniprogram.Input) {
    const key = String(event.currentTarget.dataset.key)
    this.setData({ contentEditor: this.data.contentEditor.map(f => f.key === key ? { ...f, value: event.detail.value } : f) })
  },

  /** 关联影像：传全文书影像，支持左右切换（原生 wx.previewImage 无上一张/下一张按钮） */
  previewImage(event: WechatMiniprogram.TouchEvent) {
    const url = String(event.currentTarget.dataset.url || '')
    const list = Array.from(new Set([...(this.data.images || []), url].filter(Boolean)))
    if (!list.length) return
    this.setData({
      imageViewerVisible: true,
      imageViewerUrls: list,
      imageViewerCurrent: Math.max(0, list.indexOf(url)),
    })
  },

  onImageViewerClose() {
    this.setData({ imageViewerVisible: false, imageViewerUrls: [], imageViewerCurrent: 0 })
  },

  /** 文件导出 PDF 行：有则打开，无则先生成再打开 */
  onPdfTap() {
    if (this.data.hasPdf) this.openPdf()
    else this.generatePdf()
  },

  async saveContent() {
    const doc = this.data.doc
    if (!doc) return
    try {
      const content = Object.fromEntries(this.data.contentEditor.map(f => [f.key, f.value]))
      await request(`/document/${doc.id}`, { method: 'PUT', data: { content } })
      wx.showToast({ title: '已保存，需重新签认', icon: 'none' })
      await this.loadDoc(doc.id)
    } catch (error) { wx.showModal({ title: '保存失败', content: (error as Error).message, showCancel: false }) }
  },

  async openSign() {
    const doc = this.data.doc
    if (!doc) return
    try {
      const signatures = await request<{ id: number; name: string }[]>('/signatures/me')
      const choices = [...signatures]
      if (this.data.projectSealId && !choices.some(s => s.id === this.data.projectSealId)) choices.push({ id: this.data.projectSealId, name: '本项目机构章（需授权密码）' })
      const mode = this.data.signSlots[0]?.mode || 'uploaded'
      const signingModeIndex = mode === 'online' ? 0 : mode === 'offline' ? 2 : 1
      this.setData({ signatures: choices, signPassword: '', signSlotIndex: 0, signSignatureIndex: 0, signingMode: mode, signingModeIndex, signatureTempPath: '', signatureUploadedId: 0, showSign: true })
    } catch (error) { wx.showToast({ title: (error as Error).message, icon: 'none' }) }
  },
  closeSign() {
    if (this.data.signing) return
    this.setData({ showSign: false, signPassword: '', signatureTempPath: '', signatureUploadedId: 0 })
  },
  preventTouchMove() {},
  onSignSlot(event: WechatMiniprogram.PickerChange) {
    const index = Number(event.detail.value)
    const mode = this.data.signSlots[index]?.mode || 'uploaded'
    this.setData({ signSlotIndex: index, signingMode: mode, signingModeIndex: mode === 'online' ? 0 : mode === 'offline' ? 2 : 1, signatureTempPath: '', signatureUploadedId: 0 })
  },
  onSignSignature(event: WechatMiniprogram.PickerChange) { this.setData({ signSignatureIndex: Number(event.detail.value), signatureUploadedId: 0 }) },
  onSignPassword(event: WechatMiniprogram.Input) { this.setData({ signPassword: event.detail.value }) },
  onSigningMode(event: WechatMiniprogram.PickerChange) {
    const modes = ['online', 'uploaded', 'offline']
    const index = Number(event.detail.value)
    const mode = modes[index] || 'uploaded'
    if (this.data.signSlots[this.data.signSlotIndex]?.key === 'project_seal' && mode === 'online') {
      wx.showToast({ title: '项目用章只能选择已授权印章或线下签署', icon: 'none' })
      return
    }
    this.setData({ signingMode: mode, signingModeIndex: index, signatureTempPath: '', signatureUploadedId: 0 })
  },

  openSignatureCanvas() {
    wx.navigateTo({
      url: '/pages/documents/signature',
      success: (result) => {
        result.eventChannel.on('signatureComplete', (payload: { tempFilePath?: string }) => {
          if (payload.tempFilePath) this.setData({ signatureTempPath: payload.tempFilePath, signatureUploadedId: 0 })
        })
      },
      fail: () => wx.showToast({ title: '无法打开签名页，请重试', icon: 'none' }),
    })
  },

  uploadSignaturePath(path: string): Promise<number> {
    return new Promise((resolve, reject) => {
      wx.uploadFile({
        url: `${config.apiBaseUrl}/signatures/upload`, filePath: path, name: 'file',
        header: { Authorization: `Bearer ${session.getToken()}` },
        formData: { name: '个人签名', password: this.data.signPassword },
        success: (response) => {
          try {
            const value = JSON.parse(response.data) as { id?: number; detail?: string }
            if (response.statusCode !== 200 || !value.id) throw new Error(value.detail || '签名上传失败')
            resolve(value.id)
          } catch (error) { reject(error) }
        },
        fail: () => reject(new Error('签名上传失败，请重试')),
      })
    })
  },

  async saveSigningMode(docId: number, slot: string, mode: string) {
    await request(`/documents/${docId}/signing-mode`, {
      method: 'POST', data: { slot, mode },
    })
  },

  async confirmOfflineSignoff(event: WechatMiniprogram.CustomEvent<{ slot?: string }>) {
    const doc = this.data.doc
    const slot = String(event.currentTarget.dataset.slot || '')
    if (!doc || !slot) return
    const confirmation = await wx.showModal({
      title: '确认纸质签署完成',
      content: '请确认已打印当前版本，并由对应人员完成纸面手写签名。系统只记录你的确认，不会生成电子签名，也不代表已收到纸质原件。',
      confirmText: '确认已签',
      confirmColor: '#176b58',
    })
    if (!confirmation.confirm) return
    try {
      await request(`/documents/${doc.id}/offline-signoff`, {
        method: 'POST', data: { slot },
      })
      wx.showToast({ title: '已记录线下签署', icon: 'success' })
      await this.loadDoc(doc.id)
    } catch (error) {
      wx.showModal({ title: '确认失败', content: (error as Error).message, showCancel: false })
    }
  },

  async confirmSign() {
    if (this.data.signing) return
    const doc = this.data.doc
    const slot = this.data.signSlots[this.data.signSlotIndex]
    const sig = this.data.signatures[this.data.signSignatureIndex]
    const mode = this.data.signingMode as string
    if (!doc || !slot) { wx.showToast({ title: '请选择签认位置', icon: 'none' }); return }
    if (mode === 'offline') {
      this.setData({ signing: true })
      try {
        await this.saveSigningMode(doc.id, slot.key, mode)
        this.setData({ showSign: false, signPassword: '', signatureTempPath: '', signatureUploadedId: 0 })
        wx.showModal({ title: '已记录线下签署方式', content: '请下载并打印文书，由相关人员手写签名。系统不会生成电子签名图。', showCancel: false })
        await this.loadDoc(doc.id)
      } catch (error) { wx.showModal({ title: '设置失败', content: (error as Error).message, showCancel: false }) }
      finally { this.setData({ signing: false }) }
      return
    }
    if (mode === 'online' && !this.data.signatureTempPath && !this.data.signatureUploadedId) { wx.showToast({ title: '请先完成手写签名', icon: 'none' }); return }
    if (mode === 'uploaded' && !sig) { wx.showToast({ title: '请选择个人签名', icon: 'none' }); return }
    if (!this.data.signPassword) { wx.showToast({ title: '请输入签章使用密码', icon: 'none' }); return }
    this.setData({ signing: true })
    try {
      let signatureId = sig?.id || 0
      if (mode === 'online') {
        signatureId = this.data.signatureUploadedId
        if (!signatureId) {
          signatureId = await this.uploadSignaturePath(this.data.signatureTempPath)
          this.setData({ signatureUploadedId: signatureId })
        }
      }
      await this.saveSigningMode(doc.id, slot.key, mode)
      await request(`/documents/${doc.id}/sign`, { method: 'POST', data: { slot: slot.key, signature_id: signatureId, password: this.data.signPassword } })
      this.closeSign()
      wx.showToast({ title: '签认已完成', icon: 'success' })
      await this.loadDoc(doc.id)
    } catch (error) { wx.showModal({ title: '签认失败', content: (error as Error).message, showCancel: false }) }
    finally { this.setData({ signing: false, signPassword: '' }) }
  },

  uploadPersonalSignature() {
    if (!this.data.signPassword || this.data.signPassword.length < 6) { wx.showToast({ title: '先输入至少6位签章密码', icon: 'none' }); return }
    if (this.data.uploadingSignature) return
    this.setData({ uploadingSignature: true })
    wx.chooseMedia({ count: 1, mediaType: ['image'], sourceType: ['album'], success: (res) => {
      const file = res.tempFiles[0]
      if (!file) { this.setData({ uploadingSignature: false }); return }
      wx.uploadFile({ url: `${config.apiBaseUrl}/signatures/upload`, filePath: file.tempFilePath, name: 'file',
        header: { Authorization: `Bearer ${session.getToken()}` },
        formData: { name: '个人签名', password: this.data.signPassword },
        success: (response) => {
          try {
            const value = JSON.parse(response.data) as { id?: number; name?: string; detail?: string }
            if (response.statusCode !== 200 || !value.id) throw new Error(typeof value.detail === 'string' ? value.detail : '签名上传失败')
            const signatures = [...this.data.signatures]
            const existingIndex = signatures.findIndex(item => item.id === value.id)
            if (existingIndex >= 0) signatures[existingIndex] = { id: value.id, name: value.name || '个人签名' }
            else signatures.push({ id: value.id, name: value.name || '个人签名' })
            this.setData({ signatures, signSignatureIndex: signatures.findIndex(item => item.id === value.id) })
            wx.showToast({ title: '个人签名已保存', icon: 'success' })
          } catch (error) { wx.showToast({ title: (error as Error).message, icon: 'none' }) }
          finally { this.setData({ uploadingSignature: false }) }
        }, fail: () => { this.setData({ uploadingSignature: false }); wx.showToast({ title: '上传失败，请重试', icon: 'none' }) },
      })
    }, fail: () => this.setData({ uploadingSignature: false }) })
  },

  submit() {
    const doc = this.data.doc
    if (!doc) return
    wx.showModal({
      title: '提交审批',
      content: '确认提交该文书进入审批流程？',
      success: async (res) => {
        if (!res.confirm) return
        this.setData({ submitting: true })
        try {
          const result = await request<{ status: string }>(`/document/${doc.id}/submit`, {
            method: 'POST',
          })
          this.setData({ 'doc.status': result.status, submitting: false })
          wx.showToast({ title: '已提交', icon: 'success' })
        } catch (error) {
          this.setData({ submitting: false })
          wx.showToast({ title: (error as Error).message, icon: 'none' })
        }
      },
    })
  },

  async generatePdf() {
    const doc = this.data.doc
    if (!doc || this.data.generatingPdf) return
    this.setData({ generatingPdf: true })
    wx.showLoading({ title: '正在生成PDF', mask: true })
    try {
      const result = await request<{ pdf_url?: string; pdf_path?: string }>(`/document/${doc.id}/export-pdf`)
      this.setData({ 'doc.pdf_path': result.pdf_url || result.pdf_path || '', hasPdf: true })
      wx.hideLoading()
      wx.showToast({ title: 'PDF已生成', icon: 'success' })
      this.openPdf()
    } catch (error) {
      wx.hideLoading()
      wx.showToast({ title: (error as Error).message, icon: 'none' })
    } finally {
      this.setData({ generatingPdf: false })
    }
  },

  approve() {
    const doc = this.data.doc
    if (!doc || this.data.approving) return
    this.setData({ approving: true })
    request<{ status: string }>(`/document/${doc.id}/approve`, { method: 'POST' })
      .then(async (result) => {
        this.setData({ 'doc.status': result.status, canApprove: false, approving: false })
        wx.showToast({ title: '已审批，待归档', icon: 'success' })
        await this.loadDoc(doc.id)
      })
      .catch((error) => {
        this.setData({ approving: false })
        wx.showModal({ title: '审批失败', content: (error as Error).message, showCancel: false })
      })
  },

  reject() {
    const doc = this.data.doc
    if (!doc || this.data.approving) return
    wx.showModal({
      title: '驳回文书',
      content: '请填写驳回原因',
      editable: true,
      placeholderText: '驳回原因',
      success: (res) => {
        if (!res.confirm || this.data.approving) return
        this.setData({ approving: true })
        request<{ status: string }>(`/document/${doc.id}/reject`, {
          method: 'POST',
          data: { reason: res.content || '' },
        })
          .then(async (result) => {
            this.setData({ 'doc.status': result.status, canApprove: false, approving: false })
            wx.showToast({ title: '已驳回', icon: 'success' })
            await this.loadDoc(doc.id)
          })
          .catch((error) => {
            this.setData({ approving: false })
            wx.showModal({ title: '驳回失败', content: (error as Error).message, showCancel: false })
          })
      },
    })
  },

  openWord() {
    const id = this.data.doc?.id
    if (!id) return
    wx.showLoading({ title: '生成Word中', mask: true })
    request(`/document/${id}/export-docx`).then(() => {
      wx.downloadFile({ url: `${config.apiBaseUrl}/document/${id}/download-docx`, header: { Authorization: `Bearer ${session.getToken()}` },
        success(res) { wx.hideLoading(); if (res.statusCode === 200) wx.openDocument({ filePath: res.tempFilePath, fileType: 'docx', showMenu: true }); else wx.showToast({ title: 'Word下载失败', icon: 'none' }) },
        fail() { wx.hideLoading(); wx.showToast({ title: 'Word下载失败', icon: 'none' }) },
      })
    }).catch(error => { wx.hideLoading(); wx.showToast({ title: (error as Error).message, icon: 'none' }) })
  },

  async retryArchive() {
    const id = this.data.doc?.id
    if (!id) return
    wx.showLoading({ title: '同步资料库', mask: true })
    try {
      const result = await request<{ status: string; error?: string | null }>(
        `/document/${id}/archive-sync`, { method: 'POST', data: {} },
      )
      await this.loadDoc(id)
      if (result.status === 'synced') {
        wx.showToast({ title: '已同步到资料库', icon: 'success' })
      } else {
        wx.showModal({ title: '同步未完成', content: result.error || '请稍后重试', showCancel: false })
      }
    } catch (error) {
      wx.showModal({ title: '同步失败', content: (error as Error).message, showCancel: false })
    } finally {
      wx.hideLoading()
    }
  },

  openCollaboration() {
    const doc = this.data.doc
    if (!doc) return
    wx.navigateTo({ url: `/pages/todo/compose?type=document&id=${doc.id}` })
  },

  openPdf() {
    const doc = this.data.doc
    if (!doc) return
    const token = session.getToken()
    wx.showLoading({ title: '下载中', mask: true })
    wx.downloadFile({
      url: `${config.apiBaseUrl}/document/${doc.id}/download-pdf`,
      header: token ? { Authorization: `Bearer ${token}` } : {},
      success(res) {
        wx.hideLoading()
        if (res.statusCode !== 200) {
          wx.showToast({ title: '下载失败', icon: 'none' })
          return
        }
        wx.openDocument({
          filePath: res.tempFilePath,
          fileType: 'pdf',
          showMenu: true,
          fail() {
            wx.showToast({ title: '无法打开PDF', icon: 'none' })
          },
        })
      },
      fail() {
        wx.hideLoading()
        wx.showToast({ title: '下载失败', icon: 'none' })
      },
    })
  },
}))
