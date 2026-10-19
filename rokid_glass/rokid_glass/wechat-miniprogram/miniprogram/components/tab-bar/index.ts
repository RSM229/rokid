Component({
  properties: {
    active: { type: String, value: '' },
  },
  data: {
    // 顺序：工作台居中（2026-10-09 调整，工作台为第 3 格）
    tabs: [
      { key: 'hazard', label: '隐患识别', icon: 'ic-hazard', url: '/pages/capture/index?mode=hazard' },
      { key: 'work', label: '工作记录', icon: 'ic-memo', url: '/pages/work/index' },
      { key: 'agent', label: '工作台', icon: 'ic-chat-v', url: '/pages/agent/index' },
      { key: 'kb', label: '资料库', icon: 'ic-collection', url: '/pages/kb/index' },
      { key: 'mine', label: '我', icon: 'ic-user', url: '/pages/mine/index' },
    ],
  },
  methods: {
    onTap(e: WechatMiniprogram.TouchEvent) {
      const key = e.currentTarget.dataset.key as string
      const url = e.currentTarget.dataset.url as string
      if (this.data.active === key) return
      wx.redirectTo({ url })
    },
  },
})
