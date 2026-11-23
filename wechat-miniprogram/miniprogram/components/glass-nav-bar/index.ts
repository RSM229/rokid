Component({
  options: {
    multipleSlots: true,
  },
  properties: {
    title: { type: String, value: '' },
    left: { type: String, value: 'back' }, // 'back' | 'menu' | 'none'
    spacer: { type: Boolean, value: true },
    /** 页面自行处理返回（如资料库先返回上一级目录），组件只抛出 lefttap。 */
    customBack: { type: Boolean, value: false },
    /** 大标题模式：标题独立在栏下方，随页面滚动收起进栏内 */
    largeTitle: { type: Boolean, value: false },
    /** 当前页面滚动距离（px），页面通过 onPageScroll 传入 */
    scrollTop: { type: Number, value: 0 },
  },
  data: {
    statusBarHeight: 20,
    navBarHeight: 44,
    totalHeight: 64,
    largeHeight: 96,
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
        if (this.data.customBack) {
          this.triggerEvent('lefttap')
          return
        }
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
