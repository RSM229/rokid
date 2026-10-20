import { config } from '../../config'
import { session } from '../../services/session'
import { request } from '../../services/request'
import { UploadError } from '../../services/upload'

export const MAX_VIDEO_SECONDS = 300
export const MAX_VIDEO_BYTES = 200 * 1024 * 1024
export const MAX_SEGMENTS = 20

export interface VideoSegment {
  path: string
  duration: number
  size: number
  width: number
  height: number
}

export async function inspectVideo(path: string): Promise<VideoSegment> {
  const info = await new Promise<WechatMiniprogram.GetVideoInfoSuccessCallbackResult>((resolve, reject) => {
    wx.getVideoInfo({ src: path, success: resolve, fail: reject })
  })
  // getVideoInfo.size is kB; getFileInfo gives the actual byte count.
  const size = await new Promise<number>((resolve, reject) => {
    wx.getFileSystemManager().getFileInfo({ filePath: path, success: r => resolve(r.size), fail: reject })
  })
  if (!Number.isFinite(info.duration) || info.duration <= 0 || info.width <= 0 || info.height <= 0 || size <= 0) {
    throw new Error('无法读取有效的视频信息，请重新选择')
  }
  if (info.duration > MAX_VIDEO_SECONDS || size > MAX_VIDEO_BYTES) {
    throw new Error('视频不能超过300秒或200MB')
  }
  return { path, duration: info.duration, size, width: info.width, height: info.height }
}

export async function mergeSegments(segments: VideoSegment[], recordingId: string): Promise<string> {
  for (let index = 0; index < segments.length; index += 1) {
    await new Promise<void>((resolve, reject) => {
      wx.uploadFile({
        url: `${config.apiBaseUrl}/media/videos/segments/${recordingId}/${index}`,
        filePath: segments[index].path,
        name: 'file',
        header: { Authorization: `Bearer ${session.getToken()}` },
        timeout: 120000,
        success: response => {
          if (response.statusCode >= 200 && response.statusCode < 300) resolve()
          else {
            let detail = '片段上传失败，可重试合并'
            try { detail = JSON.parse(response.data).detail || detail } catch { /* retain fallback */ }
            reject(new UploadError(detail, response.statusCode >= 500))
          }
        },
        fail: reject,
      })
    })
  }
  await request(`/media/videos/segments/${recordingId}/merge`, {
    method: 'POST', data: { count: segments.length }, timeout: 120000,
  })
  return new Promise<string>((resolve, reject) => {
    wx.downloadFile({
      url: `${config.apiBaseUrl}/media/videos/segments/${recordingId}/download`,
      header: { Authorization: `Bearer ${session.getToken()}` },
      timeout: 120000,
      success: response => {
        if (response.statusCode === 200 && response.tempFilePath) resolve(response.tempFilePath)
        else reject(new Error('合并视频下载失败，请重试合并'))
      },
      fail: reject,
    })
  })
}
