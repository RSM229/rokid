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
      // 只抛 select，由父页面在 select 处理中关闭面板（agent 的 selectModel
      // 与 ai 的 onModelPicked 都会把面板 visible 置 false）。若这里再抛 close，
      // 会与父页面 toggle 类关闭逻辑叠加，把刚关闭的面板又打开（需二次点击）。
      this.triggerEvent('select', { id })
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
