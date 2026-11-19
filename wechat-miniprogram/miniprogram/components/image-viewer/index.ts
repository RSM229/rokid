/**
 * 全屏图片预览（带「上一张/下一张」按钮与计数）
 *
 * 为什么不用 wx.previewImage：
 *   1) 原生预览没有任何按钮，多图只能靠手指滑（很多人以为是「点不了下一张」）；
 *   2) 只传 1 张 url 时原生预览根本切不了，只能退出去再点下一张；
 *   3) 本项目多处图片是「受控 URL + access_token」，需要自己控制切换与失败兜底。
 *
 * 用法：
 *   <image-viewer visible="{{imageViewerVisible}}" urls="{{urls}}"
 *                 current="{{index}}" bind:close="onImageViewerClose" />
 *
 * 内部状态与外部属性分离（open / activeIndex），避免父页面频繁 setData 时
 * 把用户已经滑到的第 N 张重置回第 1 张。
 */
Component({
  properties: {
    /** 外部控制显隐 */
    visible: { type: Boolean, value: false },
    /** 图片全集 */
    urls: { type: Array, value: [] },
    /** 打开时定位到第几张（0 起） */
    current: { type: Number, value: 0 },
  },

  data: {
    open: false,
    activeIndex: 0,
    showHint: true,
  },

  observers: {
    'visible, current': function (visible: boolean, current: number) {
      if (visible) {
        // 每次打开都定位到外部指定的那张，并重新显示一次操作提示
        this.setData({ open: true, activeIndex: current >= 0 ? current : 0, showHint: true })
      } else {
        this.setData({ open: false })
      }
    },
  },

  methods: {
    onSwiperChange(event: WechatMiniprogram.SwiperChange) {
      this.setData({ activeIndex: event.detail.current })
    },

    prev() {
      const { urls, activeIndex } = this.data
      if (!urls || urls.length < 2) return
      this.setData({ activeIndex: (activeIndex - 1 + urls.length) % urls.length })
    },

    next() {
      const { urls, activeIndex } = this.data
      if (!urls || urls.length < 2) return
      this.setData({ activeIndex: (activeIndex + 1) % urls.length })
    },

    close() {
      // 立即收起（父页面同时把 visible 置 false，双保险）
      this.setData({ open: false, showHint: true })
      this.triggerEvent('close')
    },
  },
})
