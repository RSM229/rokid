import { createIdempotencyKey } from '../utils/idempotency'
import { config } from '../config'
import type { UploadFields } from './upload'
import { UploadError, uploadAudio, uploadImage, uploadVideo } from './upload'
import { session } from './session'

const QUEUE_KEY = `gdjl_${config.storageScope}_media_upload_queue`

/** PRD 19.5：拍照、录像和录音上传失败都要进入本地待同步队列 */
export type UploadMediaType = 'image' | 'video' | 'audio'

export interface UploadQueueItem {
  id: string
  mediaType: UploadMediaType
  filePath: string
  fields: UploadFields
  idempotencyKey: string
  createdAt: string
  lastError?: string
}

/** 入队前把临时文件转存为持久文件，避免小程序重启后 tempFilePath 失效 */
export function persistTempFile(tempFilePath: string): Promise<string> {
  return new Promise((resolve) => {
    if (!tempFilePath || !/^wxfile:\/\/tmp|^http/.test(tempFilePath)) {
      resolve(tempFilePath)
      return
    }
    wx.getFileSystemManager().saveFile({
      tempFilePath,
      success: (result) => resolve(result.savedFilePath || tempFilePath),
      fail: () => resolve(tempFilePath),
    })
  })
}

function readQueue(): UploadQueueItem[] {
  const value = wx.getStorageSync(QUEUE_KEY)
  return Array.isArray(value) ? value : []
}

function writeQueue(items: UploadQueueItem[]): void {
  wx.setStorageSync(QUEUE_KEY, items)
}

export const uploadQueue = {
  list(): UploadQueueItem[] {
    return readQueue()
  },
  enqueue(
    mediaType: UploadMediaType,
    filePath: string,
    fields: UploadFields,
    error?: string,
  ): UploadQueueItem {
    const item: UploadQueueItem = {
      id: createIdempotencyKey(),
      mediaType,
      filePath,
      fields,
      idempotencyKey: createIdempotencyKey(),
      createdAt: new Date().toISOString(),
      lastError: error,
    }
    const items = readQueue()
    items.push(item)
    writeQueue(items.slice(-20))
    return item
  },
  remove(id: string): void {
    writeQueue(readQueue().filter((item) => item.id !== id))
  },
  /**
   * 批量重试。
   * @param skipAudio 语音转写的结果需要页面承接（填入输入框或语音会话），
   *   后台自动补传会把识别文本丢掉，因此全局补传传 true，由对话页显式重试。
   */
  async retryAll(skipAudio = false): Promise<{ succeeded: number; failed: number }> {
    const queue = readQueue()
    const targets = queue.filter((item) => !(skipAudio && item.mediaType === 'audio'))
    const failures = new Map<string, string>()
    const dropped = new Set<string>() // 成功或不可重试出队的条目 id
    let succeeded = 0
    for (const item of targets) {
      // 🔴 token 失效后中止：继续循环只会逐项 401 并连环触发整页 reLaunch（队列上限 20）
      if (!session.getToken()) break
      try {
        await this.uploadOne(item)
        succeeded += 1
        dropped.add(item.id)
      } catch (error) {
        // 不可重试（文件失效/内容不合规）直接丢弃，避免永久卡在队列里
        if (!(error instanceof UploadError) || error.retryable) {
          failures.set(item.id, (error as Error).message)
        } else {
          dropped.add(item.id)
        }
      }
    }
    // 🔴 写回前重读最新队列再按 id 合并，不用起始快照整表覆盖：
    // retryAll 串行耗时可达数分钟，期间 enqueue 的新条目不能丢、remove 的取消项不能复活。
    // 保留规则：只移除 dropped（本轮成功/不可重试出队）的条目——
    // 失败的保留（带 lastError），break 中止未处理的保留，新入队/跳过的也原样保留。
    const latest = readQueue()
    const merged = latest
      .filter((item) => !dropped.has(item.id))
      .map((item) =>
        failures.has(item.id) ? { ...item, lastError: failures.get(item.id) } : item,
      )
    writeQueue(merged)
    return { succeeded, failed: failures.size }
  },
  /** 单项上传；audio 走 STT 转写，返回识别文本 */
  async uploadOne(item: UploadQueueItem): Promise<{ text?: string }> {
    if (item.mediaType === 'image') {
      return (await uploadImage(item.filePath, item.fields, item.idempotencyKey)) as { text?: string }
    }
    if (item.mediaType === 'audio') {
      return (await uploadAudio<{ text: string }>(item.filePath)) as { text?: string }
    }
    return (await uploadVideo(item.filePath, item.fields, item.idempotencyKey)) as { text?: string }
  },
  /** 重试指定项并出队，供页面对单条（尤其是语音）做「重试/取消」 */
  async retryOne(id: string): Promise<{ text?: string }> {
    const items = readQueue()
    const item = items.find((entry) => entry.id === id)
    if (!item) throw new Error('该待上传项已不存在')
    try {
      const result = await this.uploadOne(item)
      this.remove(id)
      return result
    } catch (error) {
      if (!(error instanceof UploadError) || error.retryable) {
        writeQueue(items.map((entry) =>
          entry.id === id ? { ...entry, lastError: (error as Error).message } : entry))
      } else {
        // 不可重试（文件失效/内容不合规）直接出队，避免永久卡在队列里
        this.remove(id)
      }
      throw error
    }
  },
}
