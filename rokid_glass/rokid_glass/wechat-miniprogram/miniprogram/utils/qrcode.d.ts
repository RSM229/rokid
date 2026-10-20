interface DrawQrcodeOptions {
  canvasId: string
  text: string
  width: number
  height: number
  _this?: WechatMiniprogram.Page.Instance<WechatMiniprogram.IAnyObject, WechatMiniprogram.IAnyObject>
  background?: string
  foreground?: string
  callback?: (res: unknown) => void
}

declare const drawQrcode: (options: DrawQrcodeOptions) => { toDataURL(cb?: (url: string) => void): string }

export = drawQrcode
