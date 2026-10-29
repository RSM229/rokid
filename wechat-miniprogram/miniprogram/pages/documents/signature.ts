const INK_COLOR = '#172b3a'
const LINE_WIDTH = 2.5
const DOT_RADIUS = 1.4

interface SignaturePoint {
  x: number
  y: number
}

interface SignatureCanvasRect {
  left: number
  top: number
  width: number
  height: number
}

interface SignatureRuntime {
  canvas: WechatMiniprogram.Canvas
  context: WechatMiniprogram.CanvasRenderingContext.CanvasRenderingContext2D
  rect: SignatureCanvasRect | null
  width: number
  height: number
  lastPoint: SignaturePoint | null
  strokes: SignaturePoint[][]
}

interface SignatureTouchCoordinates {
  x?: number
  y?: number
  clientX?: number
  clientY?: number
}

const runtimeByPage = new WeakMap<object, SignatureRuntime>()

/**
 * 触点到画布坐标的换算。
 *
 * 旧实现把 canvas 内坐标再乘到 1400x620 的"假想画布"上，但画布的绘制坐标系
 * 就是它自己的 CSS 像素（横屏时约 790x185），于是落笔点被横向放大 ~1.8 倍、
 * 纵向放大 ~3.3 倍，笔画整体偏离手指，越往右下偏得越多还会被裁掉。
 * 这里只做"页面坐标 → 画布内坐标"的平移/夹取，不做任何缩放。
 */
export function mapSignaturePoint(touch: SignatureTouchCoordinates, rect: SignatureCanvasRect): SignaturePoint | null {
  if (!rect.width || !rect.height) return null
  const hasCanvasCoordinates = Number.isFinite(touch.x) && Number.isFinite(touch.y)
  const localX = hasCanvasCoordinates ? Number(touch.x) : Number.isFinite(touch.clientX) ? Number(touch.clientX) - rect.left : NaN
  const localY = hasCanvasCoordinates ? Number(touch.y) : Number.isFinite(touch.clientY) ? Number(touch.clientY) - rect.top : NaN
  if (!Number.isFinite(localX) || !Number.isFinite(localY)) return null
  return {
    x: Math.max(0, Math.min(rect.width, localX)),
    y: Math.max(0, Math.min(rect.height, localY)),
  }
}

function getPoint(event: WechatMiniprogram.TouchCanvas, rect: SignatureCanvasRect): SignaturePoint | null {
  const touch = event.touches[0] || event.changedTouches[0]
  if (!touch) return null
  return mapSignaturePoint(touch, rect)
}

Page({
  data: {
    hasInk: false,
    exporting: false,
  },

  onReady() {
    this.prepareCanvas()
  },

  onResize() {
    // 横竖屏切换会重建 2D 画布的位图，已画的笔画需要重放。
    this.prepareCanvas(true)
  },

  prepareCanvas(keepStrokes = false, attempt = 0) {
    const previous = runtimeByPage.get(this)
    wx.createSelectorQuery().in(this).select('#signature-surface')
      .fields({ node: true, size: true, rect: true })
      .exec(result => {
        const info = (result && result[0]) as {
          node?: WechatMiniprogram.Canvas
          width?: number
          height?: number
          left?: number
          top?: number
        } | undefined
        const canvas = info?.node
        const width = Number(info?.width || 0)
        const height = Number(info?.height || 0)
        if (!canvas || !width || !height) {
          // 横屏页面首次布局可能还没量到尺寸；稍后重试，避免画布整块失灵。
          if (attempt < 3) setTimeout(() => this.prepareCanvas(keepStrokes, attempt + 1), 80)
          return
        }

        // 2D 画布的位图尺寸必须显式设置；按 dpr 放大后再 scale，
        // 这样笔画坐标可以直接用 CSS 像素，线条也不会发虚。
        const dpr = wx.getSystemInfoSync().pixelRatio || 1
        canvas.width = Math.round(width * dpr)
        canvas.height = Math.round(height * dpr)
        const context = canvas.getContext('2d')
        context.scale(dpr, dpr)
        context.lineCap = 'round'
        context.lineJoin = 'round'

        const runtime: SignatureRuntime = {
          canvas,
          context,
          rect: {
            left: Number(info?.left || 0),
            top: Number(info?.top || 0),
            width,
            height,
          },
          width,
          height,
          lastPoint: null,
          strokes: keepStrokes && previous ? previous.strokes : [],
        }
        runtimeByPage.set(this, runtime)
        if (runtime.strokes.length) this.redrawStrokes()
      })
  },

  onSignatureTouchStart(event: WechatMiniprogram.TouchCanvas) {
    const runtime = runtimeByPage.get(this)
    if (!runtime?.rect) return
    const point = getPoint(event, runtime.rect)
    if (!point) return
    runtime.lastPoint = point
    runtime.strokes.push([point])
    runtime.context.fillStyle = INK_COLOR
    runtime.context.beginPath()
    runtime.context.arc(point.x, point.y, DOT_RADIUS, 0, Math.PI * 2)
    runtime.context.fill()
    this.setData({ hasInk: true })
  },

  onSignatureTouchMove(event: WechatMiniprogram.TouchCanvas) {
    const runtime = runtimeByPage.get(this)
    if (!runtime?.rect || !runtime.lastPoint) return
    const point = getPoint(event, runtime.rect)
    if (!point) return
    const last = runtime.lastPoint
    if (point.x === last.x && point.y === last.y) return
    runtime.context.strokeStyle = INK_COLOR
    runtime.context.lineWidth = LINE_WIDTH
    runtime.context.beginPath()
    runtime.context.moveTo(last.x, last.y)
    runtime.context.lineTo(point.x, point.y)
    runtime.context.stroke()
    runtime.lastPoint = point
    const stroke = runtime.strokes[runtime.strokes.length - 1]
    if (stroke) stroke.push(point)
  },

  onSignatureTouchEnd() {
    const runtime = runtimeByPage.get(this)
    if (runtime) runtime.lastPoint = null
  },

  redrawStrokes() {
    const runtime = runtimeByPage.get(this)
    if (!runtime) return
    const context = runtime.context
    context.clearRect(0, 0, runtime.width, runtime.height)
    context.strokeStyle = INK_COLOR
    context.fillStyle = INK_COLOR
    context.lineWidth = LINE_WIDTH
    for (const stroke of runtime.strokes) {
      if (!stroke.length) continue
      if (stroke.length === 1) {
        context.beginPath()
        context.arc(stroke[0].x, stroke[0].y, DOT_RADIUS, 0, Math.PI * 2)
        context.fill()
        continue
      }
      context.beginPath()
      context.moveTo(stroke[0].x, stroke[0].y)
      for (const point of stroke.slice(1)) context.lineTo(point.x, point.y)
      context.stroke()
    }
  },

  clearSignature() {
    const runtime = runtimeByPage.get(this)
    if (!runtime) return
    runtime.context.clearRect(0, 0, runtime.width, runtime.height)
    runtime.strokes = []
    runtime.lastPoint = null
    this.setData({ hasInk: false })
  },

  cancelSignature() {
    wx.navigateBack()
  },

  confirmSignature() {
    if (this.data.exporting) return
    if (!this.data.hasInk) {
      wx.showToast({ title: '请先完成手写签名', icon: 'none' })
      return
    }
    const runtime = runtimeByPage.get(this)
    if (!runtime) return
    this.setData({ exporting: true })
    wx.canvasToTempFilePath({
      canvas: runtime.canvas,
      fileType: 'png',
      destWidth: runtime.canvas.width,
      destHeight: runtime.canvas.height,
      success: result => {
        const channel = this.getOpenerEventChannel()
        if (typeof channel.emit === 'function') channel.emit('signatureComplete', { tempFilePath: result.tempFilePath })
        wx.navigateBack()
      },
      fail: () => {
        this.setData({ exporting: false })
        wx.showToast({ title: '签名图片生成失败，请重试', icon: 'none' })
      },
    }, this)
  },
})
