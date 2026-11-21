/**
 * 主题配色引擎（2026-09-20 新增）
 *
 * 用途：让用户在「我的 → 主题配色」里自由调节自己喜欢的主题色，全站即时生效。
 *
 * 原理：
 *   1. 全站品牌色已参数化为 CSS 变量（app.wxss 顶部有默认值，各页 wxss 写 var(--x, 默认值)）
 *   2. 用户选定色彩后存 storage；每个页面 onShow 时由 themed() 把变量注入页面根节点 style
 *   3. CSS 变量沿 DOM 继承，页面内所有元素（含 tab-bar / glass-nav-bar 组件）自动跟随
 *
 * 变量清单（与 app.wxss 中的 var() 兜底值一一对应）：
 *   --brand / --brand-l / --brand-d / --brand-deep      品牌色三档 + 导航深色
 *   --ink / --ink-2 / --on-brand                        正文深色 / 次级深色 / 主按钮文字
 *   --brand-rgb / --brand-l-rgb / --brand-d-rgb         品牌色 RGB 分量（供 rgba() 拼 alpha）
 *   --deep-rgb / --shadow-rgb / --wash-rgb / --ink-rgb  深色 / 投影 / 冷白洗 / 正文 的 RGB 分量
 *
 * 注意：危险红、警示橙、成功绿等**语义色不随主题变**，保持固定。
 */

export interface ThemeSetting {
  /** 色相 0-360 */
  h: number
  /** 饱和度 0-100 */
  s: number
  /** 亮度 0-100 */
  l: number
}

export interface ThemePreset extends ThemeSetting {
  name: string
}

const STORAGE_KEY = 'app_theme_v1'

/** 默认主题 = 墨青 #0f766e（hsl 175,77%,26%），与平面设计令牌 --flat-brand 对齐 */
export const DEFAULT_THEME: ThemeSetting = { h: 175, s: 77, l: 26 }

/** 预设配色（点击即用，也可在预设基础上继续微调） */
export const THEME_PRESETS: ThemePreset[] = [
  { name: '墨青', h: 175, s: 77, l: 26 },
  { name: '电光蓝', h: 215, s: 100, l: 54 },
  { name: '靛青', h: 248, s: 84, l: 62 },
  { name: '湖蓝', h: 190, s: 92, l: 40 },
  { name: '青竹', h: 160, s: 70, l: 36 },
  { name: '翡翠', h: 138, s: 50, l: 38 },
  { name: '琥珀', h: 33, s: 95, l: 47 },
  { name: '朱砂', h: 6, s: 74, l: 50 },
  { name: '品红', h: 332, s: 72, l: 50 },
  { name: '石墨', h: 214, s: 12, l: 38 },
]

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value))
}

function hslCss(h: number, s: number, l: number): string {
  return `hsl(${Math.round(h)}, ${Math.round(s)}%, ${Math.round(l)}%)`
}

/** HSL → RGB（0-255 整数） */
function hslToRgb(h: number, s: number, l: number): [number, number, number] {
  const sat = clamp(s, 0, 100) / 100
  const lig = clamp(l, 0, 100) / 100
  const c = (1 - Math.abs(2 * lig - 1)) * sat
  const hp = ((((h % 360) + 360) % 360) / 60)
  const x = c * (1 - Math.abs((hp % 2) - 1))
  let r = 0
  let g = 0
  let b = 0
  if (hp < 1) {
    r = c
    g = x
  } else if (hp < 2) {
    r = x
    g = c
  } else if (hp < 3) {
    g = c
    b = x
  } else if (hp < 4) {
    g = x
    b = c
  } else if (hp < 5) {
    r = x
    b = c
  } else {
    r = c
    b = x
  }
  const m = lig - c / 2
  return [
    Math.round((r + m) * 255),
    Math.round((g + m) * 255),
    Math.round((b + m) * 255),
  ]
}

function rgbCss(rgb: [number, number, number]): string {
  return `${rgb[0]}, ${rgb[1]}, ${rgb[2]}`
}

/** 读取用户主题（无设置或数据损坏时回落默认） */
export function getTheme(): ThemeSetting {
  try {
    const raw = wx.getStorageSync(STORAGE_KEY) as Partial<ThemeSetting> | ''
    if (raw && typeof raw === 'object') {
      const h = Number(raw.h)
      const s = Number(raw.s)
      const l = Number(raw.l)
      if (!Number.isNaN(h) && !Number.isNaN(s) && !Number.isNaN(l)) {
        return { h: clamp(h, 0, 360), s: clamp(s, 0, 100), l: clamp(l, 0, 100) }
      }
    }
  } catch (error) {
    // 读取失败视为无设置
  }
  return { ...DEFAULT_THEME }
}

/** 保存主题 */
export function setTheme(theme: ThemeSetting): void {
  try {
    wx.setStorageSync(STORAGE_KEY, {
      h: Math.round(clamp(theme.h, 0, 360)),
      s: Math.round(clamp(theme.s, 0, 100)),
      l: Math.round(clamp(theme.l, 0, 100)),
    })
  } catch (error) {
    // 存储失败静默
  }
}

/** 恢复默认主题 */
export function resetTheme(): void {
  try {
    wx.removeStorageSync(STORAGE_KEY)
  } catch (error) {
    // 忽略
  }
}

/** 从用户设定派生整套 CSS 变量 */
export function buildThemeVars(theme: ThemeSetting): Record<string, string> {
  // 色相按圆周取模（999° 等价 279°），饱和/亮度钳制在有效区间
  const rawHue = Number.isFinite(theme.h) ? theme.h : DEFAULT_THEME.h
  const h = ((rawHue % 360) + 360) % 360
  const s = clamp(Number.isFinite(theme.s) ? theme.s : DEFAULT_THEME.s, 0, 100)
  const l = clamp(Number.isFinite(theme.l) ? theme.l : DEFAULT_THEME.l, 0, 100)

  const brand = hslToRgb(h, s, l)
  const brandL = hslToRgb(h, Math.min(s + 6, 100), Math.min(l + 8, 72))
  const brandD = hslToRgb(h, Math.min(s * 0.92, 94), Math.max(l - 11, 24))
  const deep = hslToRgb(h, Math.max(s - 22, 16), Math.max(l - 34, 14))
  const ink = hslToRgb(h, Math.min(s * 0.55, 56), 16)
  const ink2 = hslToRgb(h, Math.min(s * 0.42, 42), 24)
  const shadow = hslToRgb(h, Math.min(s * 0.78, 78), Math.max(l - 23, 22))
  const wash = hslToRgb(h, s, 96)

  return {
    '--brand': hslCss(h, s, l),
    '--brand-l': hslCss(h, Math.min(s + 6, 100), Math.min(l + 8, 72)),
    '--brand-d': hslCss(h, Math.min(s * 0.92, 94), Math.max(l - 11, 24)),
    '--brand-deep': hslCss(h, Math.max(s - 22, 16), Math.max(l - 34, 14)),
    '--ink': hslCss(h, Math.min(s * 0.55, 56), 16),
    '--ink-2': hslCss(h, Math.min(s * 0.42, 42), 24),
    '--on-brand': l >= 66 ? '#12253d' : '#ffffff',
    '--brand-rgb': rgbCss(brand),
    '--brand-l-rgb': rgbCss(brandL),
    '--brand-d-rgb': rgbCss(brandD),
    '--deep-rgb': rgbCss(deep),
    '--shadow-rgb': rgbCss(shadow),
    '--wash-rgb': rgbCss(wash),
    '--ink-rgb': rgbCss(ink),
  }
}

/** 生成可挂到页面根节点的内联 style 字符串 */
export function themeStyle(theme?: ThemeSetting): string {
  const vars = buildThemeVars(theme || getTheme())
  return Object.keys(vars).map((key) => `${key}: ${vars[key]}`).join('; ')
}

/**
 * 页面需要注入的主题相关 data。
 * - themeStyle：CSS 变量串（页面根节点 style）
 * - themeBrand：主题色本体，供 `<switch color="">` 这类**原生组件属性**使用（它们不认 CSS 变量）
 */
export function themeData(theme?: ThemeSetting): Record<string, string> {
  const current = theme || getTheme()
  return {
    themeStyle: themeStyle(current),
    themeBrand: previewColor(current),
    themeName: isDefaultTheme(current) ? '默认' : '自定义',
  }
}

/** 当前主题是否就是默认值 */
export function isDefaultTheme(theme?: ThemeSetting): boolean {
  const t = theme || getTheme()
  return Math.abs(t.h - DEFAULT_THEME.h) < 1
    && Math.abs(t.s - DEFAULT_THEME.s) < 1
    && Math.abs(t.l - DEFAULT_THEME.l) < 1
}

/** 给色板色块用的展示色 */
export function previewColor(theme: ThemeSetting): string {
  return hslCss(theme.h, theme.s, theme.l)
}

type AnyOptions = Record<string, unknown>

type PageData = WechatMiniprogram.IAnyObject
type PageCustom = WechatMiniprogram.IAnyObject

/** 包装后的 onShow 里 this 至少要有 setData */
interface ThemedPageInstance {
  setData: (data: Record<string, unknown>) => void
  loadPendingDrafts(): void
  refreshPendingVoice(): void
}
type PageOptions = WechatMiniprogram.Page.Options<PageData, PageCustom> & PageCustom

/**
 * 页面包装器：自动注入主题变量。
 *
 * 用法：`Page(themed({ data: {...}, onShow() {...} }))`
 * 它会：
 *   1. 在 data 里补一个 themeStyle 初值（首屏就带上变量，不会闪默认色）
 *   2. 包装 onShow，每次页面显示时重新读取 storage 并刷新变量
 *      （用户在其他页改了主题，切回来即生效）
 *
 * 注意：返回类型必须写成 `Page.Options<D, C> & C`，
 * 否则 Page() 重载无法拆出 data / 自定义方法，`this.setData` 会报类型错误。
 */
export function themed<D extends PageData, C extends PageCustom>(
  options: WechatMiniprogram.Page.Options<D, C> & C,
): WechatMiniprogram.Page.Options<D, C> & C {
  const opts = options as unknown as AnyOptions
  const baseData = (opts.data || {}) as AnyOptions
  opts.data = { ...baseData, ...themeData() }

  const originalOnShow = opts.onShow as ((...args: unknown[]) => unknown) | undefined
  opts.onShow = function themedOnShow(this: ThemedPageInstance, ...args: unknown[]): unknown {
    this.setData(themeData())
    if (typeof originalOnShow === 'function') {
      return originalOnShow.apply(this, args)
    }
    return undefined
  }

  return options
}
