import { ModelItem } from '../../services/modelCatalog'

Component({
  properties: {
    models: { type: Array, value: [] as ModelItem[] },
    value: { type: String, value: '' },
    visible: { type: Boolean, value: false },
    title: { type: String, value: '选择模型' },
  },
  methods: {
    onClose() {
      this.triggerEvent('close')
    },
    onSelect(event: WechatMiniprogram.TouchEvent) {
      const id = event.currentTarget.dataset.id as string
      this.triggerEvent('select', { id })
      this.triggerEvent('close')
    },
    onCopy(event: WechatMiniprogram.TouchEvent) {
      const model = event.currentTarget.dataset.model as string
      if (!model) return
      wx.setClipboardData({
        data: model,
        success: () => wx.showToast({ title: '已复制模型 ID', icon: 'none' }),
      })
    },
  },
})
