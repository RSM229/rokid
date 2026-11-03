import { config } from '../config'
import { request } from './request'
import { session } from './session'
import { createIdempotencyKey } from '../utils/idempotency'

export interface UploadFields {
  [key: string]: string | number | boolean | undefined
}

export class UploadError extends Error {
  constructor(message: string, public retryable: boolean) {
    super(message)
    this.name = 'UploadError'
  }
}

/**
 * 把服务端错误映射成对用户友好的提示。
 * 401/413/429 等单独给出明确文案；502/503/504 多为视觉模型（VLM）网关暂时不可用；
 * 其余 5xx 归为「服务器暂时不可用」。优先回传服务端 detail，避免直接暴露「HTTP 502」这类让人摸不着头脑的报错。
 */
export function friendlyServerError(statusCode: number, detail?: string): string {
  if (statusCode === 401) return '登录已失效，请重新登录'
  if (statusCode === 413) return '图片超过大小限制，请选择较小的图片'
  if (statusCode === 429) return '请求过于频繁，请稍后重试'
  if (statusCode === 502 || statusCode === 503 || statusCode === 504) {
    return '视觉模型服务暂时不可用，请稍后重试'
  }
  if (statusCode >= 500) return '服务器暂时不可用，请稍后重试'
  return detail || `上传失败（HTTP ${statusCode}）`
}

function upload<T>(
  path: string,
  filePath: string,
  name: string,
  fields: UploadFields,
  idempotencyKey = createIdempotencyKey(),
): Promise<T> {
  const formData: Record<string, string> = {}
  Object.keys(fields).forEach((key) => {
    const value = fields[key]
    if (value !== undefined) formData[key] = String(value)
  })
  formData.idempotency_key = idempotencyKey
  return new Promise((resolve, reject) => {
    wx.uploadFile({
      url: `${config.apiBaseUrl}${path}`,
      filePath,
      name,
      formData,
      header: {
        Authorization: `Bearer ${session.getToken()}`,
        'Idempotency-Key': idempotencyKey,
      },
      timeout: 120000,
      success(response) {
        let data: unknown
        try {
          data = JSON.parse(response.data)
        } catch {
          reject(new UploadError('服务器返回了无效数据', false))
          return
        }
        if (response.statusCode >= 200 && response.statusCode < 300) {
          resolve(data as T)
        } else {
          const detail = (data as { detail?: string }).detail
          if (response.statusCode === 401) {
            session.clear()
            wx.reLaunch({ url: '/pages/login/index' })
          }
          const retryable = response.statusCode === 408
            || response.statusCode === 429
            || response.statusCode >= 500
          reject(new UploadError(friendlyServerError(response.statusCode, detail), retryable))
        }
      },
      fail(error) {
        const message = error.errMsg || '上传失败'
        const missingLocalFile = /no such file|not found|does not exist/i.test(message)
        reject(new UploadError(message, !missingLocalFile))
      },
    })
  })
}

export function uploadImage<T>(
  filePath: string,
  fields: UploadFields,
  idempotencyKey?: string,
): Promise<T> {
  return upload<T>('/media/images', filePath, 'file', fields, idempotencyKey)
}

export function uploadVideo<T>(
  filePath: string,
  fields: UploadFields,
  idempotencyKey?: string,
): Promise<T> {
  return upload<T>('/media/videos', filePath, 'file', fields, idempotencyKey)
}

/**
 * 拍照/导入后，带现场描述一次性保存工作记录与取证照片。
 *
 * 后端入口 POST /work-record/create-with-photos（multipart，文件字段 photos），
 * record_type 为工作记录类型（如 待定），payload_json 携带现场描述字段。
 */
export function uploadWorkRecordPhoto<T>(
  filePath: string,
  recordType: string,
  fields: UploadFields = {},
  idempotencyKey?: string,
): Promise<T> {
  const payload: UploadFields = {
    record_type: recordType,
    source: 'miniprogram',
    ...fields,
  }
  return upload<T>('/work-record/create-with-photos', filePath, 'photos', payload, idempotencyKey)
}

/**
 * 创建待定工作记录（不带照片），用于视频等无法通过 create-with-photos 保存的场景。
 */
export function createWorkRecordOther<T>(fields: Record<string, unknown>): Promise<T> {
  return request<T>('/work-record/other', {
    method: 'POST',
    data: { source: 'miniprogram', ...fields },
  })
}

export function uploadAudio<T>(filePath: string): Promise<T> {
  return upload<T>('/media/audio/transcribe', filePath, 'file', {
    source: 'miniprogram',
    language: 'zh',
  })
}

/** 上传微信聊天/本地文档（pdf/docx/txt），后端提取纯文本返回 */
export function uploadDocument<T>(filePath: string): Promise<T> {
  return upload<T>('/media/document/parse', filePath, 'file', {})
}

export interface InspectionCaptureFields extends UploadFields {
  section?: string
  location?: string
  weather?: string
}

/**
 * 将现场照片直接提交到巡检会话并触发 VLM 识别。
 *
 * 后端入口为 POST /inspection/{session_id}/capture（multipart，文件字段名 image），
 * 与媒体上传不同，这里不落 MediaRecord，仅做识别、取证与会话计数。
 */
export function uploadInspectionCapture<T>(
  sessionId: number,
  filePath: string,
  fields: InspectionCaptureFields = {},
): Promise<T> {
  return upload<T>(
    `/inspection/${sessionId}/capture`,
    filePath,
    'image',
    fields,
  )
}
