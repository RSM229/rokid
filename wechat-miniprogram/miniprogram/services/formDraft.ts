import { config } from '../config'

/**
 * 分步表单的本地草稿与表式结构离线缓存（19.x 断网续填）。
 *
 * 草稿按「form_code + docId(0=新建)」隔离，输入即存，保存成功后清除；
 * 表式结构按 form_code 缓存一份，断网重开表单时兜底渲染，配合草稿离线续填。
 */

export interface FormDraft {
  title: string
  values: Record<string, string>
  updatedAt: string
}

function draftKey(code: string, docId: number): string {
  return `gdjl_${config.storageScope}_form_draft_${docId || 0}_${code}`
}

function schemaKey(code: string): string {
  return `gdjl_${config.storageScope}_form_schema_${code}`
}

export function loadFormDraft(code: string, docId: number): FormDraft | null {
  try {
    const raw = wx.getStorageSync(draftKey(code, docId)) as FormDraft | ''
    if (!raw || typeof raw !== 'object' || !raw.values) return null
    return raw
  } catch {
    return null
  }
}

export function saveFormDraft(
  code: string,
  docId: number,
  title: string,
  values: Record<string, string>,
): void {
  try {
    wx.setStorageSync(draftKey(code, docId), {
      title: String(title || ''),
      values: values || {},
      updatedAt: new Date().toISOString(),
    })
  } catch {
    // 本地存储满等异常静默，不阻塞填写
  }
}

export function clearFormDraft(code: string, docId: number): void {
  try {
    wx.removeStorageSync(draftKey(code, docId))
  } catch {
    /* noop */
  }
}

/** 缓存表式结构（已解析的 /provform/schemas/{code}/steps 响应），断网兜底渲染 */
export function cacheFormSchema(code: string, resp: unknown): void {
  try {
    wx.setStorageSync(schemaKey(code), resp)
  } catch {
    /* noop */
  }
}

export function loadCachedFormSchema<T>(code: string): T | null {
  try {
    const raw = wx.getStorageSync(schemaKey(code)) as T | ''
    return raw && typeof raw === 'object' ? raw : null
  } catch {
    return null
  }
}