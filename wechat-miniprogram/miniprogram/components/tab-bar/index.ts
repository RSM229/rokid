Component({
  properties: {
    active: { type: String, value: '' },
  },
  data: {
    // 顺序：工作台居中（2026-10-09 调整，工作台为第 3 格）
    // 2026-11-16：第 1 格由「隐患识别」改为「待办事项」（入口原在左侧栏）；
    // 隐患识别仍可从工作台模型下拉菜单进入（agent/index.wxml 的 model-menu-hazard）。
    tabs: [
      { key: 'todo', label: '待办事项', icon: 'ic-list', url: '/pages/todo/index' },
      { key: 'work', label: '工作记录', icon: 'ic-memo', url: '/pages/work/index' },
      { key: 'agent', label: '工作台', icon: 'ic-chat-v', url: '/pages/agent/index' },
      { key: 'kb', label: '资料库', icon: 'ic-collection', url: '/pages/kb/index' },
      { key: 'mine', label: '我', icon: 'ic-user', url: '/pages/mine/index' },
    ],
    /** 双击「当前所在 tab」检测：上一次点击活动 tab 的时间戳（<300ms 视为双击） */
    lastActiveTabTapTime: 0,
  },
  methods: {
    onTap(e: WechatMiniprogram.TouchEvent) {
      const key = e.currentTarget.dataset.key as string
      const url = e.currentTarget.dataset.url as string
      if (this.data.active === key) {
        // 已在该 tab 上：双击抛 doubletap 事件（工作台页据此新建对话），单击不做任何事
        const now = Date.now()
        if (now - this.data.lastActiveTabTapTime < 300) {
          this.setData({ lastActiveTabTapTime: 0 })
          this.triggerEvent('doubletap', { key })
        } else {
          this.setData({ lastActiveTabTapTime: now })
        }
        return
      }
      this.setData({ lastActiveTabTapTime: 0 })
      wx.redirectTo({ url })
    },
  },
})
