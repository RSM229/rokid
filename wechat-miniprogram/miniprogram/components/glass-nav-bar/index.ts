Component({
  options: {
    multipleSlots: true,
  },
  properties: {
    title: { type: String, value: '' },
    left: { type: String, value: 'back' }, // 'back' | 'menu' | 'none'
    spacer: { type: Boolean, value: true },
  },
  data: {
    statusBarHeight: 20,
    navBarHeight: 44,
    totalHeight: 64,
  },
  lifetimes: {
    attached() {
      const sys = wx.getWindowInfo()
      const statusBarHeight = sys.statusBarHeight || 20
      const navBarHeight = 44
      this.setData({ statusBarHeight, navBarHeight, totalHeight: statusBarHeight + navBarHeight })
    },
  },
  methods: {
    onLeftTap() {
      if (this.data.left === 'back') {
        const pages = getCurrentPages()
        if (pages.length > 1) {
          wx.navigateBack()
          return
        }
        // 单页栈兜底：底栏页之间是 wx.redirectTo 互切，页面栈始终只有 1 层，
        // navigateBack 无处可退 → 返回键退回工作台（工作台自身无返回键，不会死循环）
        const cur = pages[pages.length - 1]
        const route = (cur && cur.route) || ''
        if (route !== 'pages/agent/index') {
          wx.reLaunch({ url: '/pages/agent/index' })
          return
        }
      }
      this.triggerEvent('lefttap')
    },
  },
})
