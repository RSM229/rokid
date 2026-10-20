/**
 * 创建日期：2026-07-18
 * 震动反馈封装：基于 navigator.vibrate，移动端触摸反馈
 */

/** 轻触反馈（10ms） */
export function hapticLight(): void {
  vibrate(10)
}

/** 中等反馈（20ms） */
export function hapticMedium(): void {
  vibrate(20)
}

/** 成功反馈：短-短 */
export function hapticSuccess(): void {
  vibrate([10, 30, 10])
}

/** 警告反馈：长 */
export function hapticWarning(): void {
  vibrate(40)
}

/** 错误反馈：长-长-长 */
export function hapticError(): void {
  vibrate([40, 60, 40, 60, 40])
}

function vibrate(pattern: number | number[]): void {
  try {
    if (typeof navigator !== 'undefined' && typeof navigator.vibrate === 'function') {
      navigator.vibrate(pattern)
    }
  } catch {
    // iOS 部分浏览器不支持，静默忽略
  }
}
