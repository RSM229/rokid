import { ref, onBeforeUnmount } from 'vue'

export interface UseSpeechRecognitionOptions {
  lang?: string
  continuous?: boolean
  onResult?: (text: string, isFinal: boolean) => void
  onError?: (error: string) => void
}

/**
 * 语音识别 composable，基于后端 Qwen2.5-Omni-7B 端到端模型。
 *
 * 工作流程：
 * 1. start() -> 通过 MediaRecorder 录制麦克风音频
 * 2. 每 5 秒自动分段：停止录音 -> 上传后端 Omni 识别 -> 重新开始录音
 * 3. 识别结果通过 onResult(text, true) 回调返回
 * 4. stop() -> 停止录音并清理资源
 *
 * - isListening: 是否正在录音识别
 * - isSupported: 当前浏览器是否支持（需要 MediaRecorder + getUserMedia）
 * - start(): 开始录音识别
 * - stop(): 停止录音识别
 */
export function useSpeechRecognition(options: UseSpeechRecognitionOptions = {}) {
  const {
    onResult,
    onError,
  } = options

  const isListening = ref(false)
  const isSupported = typeof MediaRecorder !== 'undefined'
    && !!navigator.mediaDevices?.getUserMedia

  let mediaStream: MediaStream | null = null
  let mediaRecorder: MediaRecorder | null = null
  let audioChunks: Blob[] = []
  let segmentTimer: ReturnType<typeof setInterval> | null = null
  let restarting = false // 防止 onstop 中重复触发

  /** 分段间隔（毫秒） */
  const SEGMENT_INTERVAL = 5000

  async function start() {
    if (!isSupported) {
      onError?.('not-supported')
      return
    }
    if (isListening.value) return

    try {
      mediaStream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      })

      const mimeType = MediaRecorder.isTypeSupported('audio/webm')
        ? 'audio/webm'
        : MediaRecorder.isTypeSupported('audio/mp4')
          ? 'audio/mp4'
          : ''

      mediaRecorder = new MediaRecorder(mediaStream, mimeType ? { mimeType } : undefined)
      audioChunks = []

      mediaRecorder.ondataavailable = (event: BlobEvent) => {
        if (event.data.size > 0) {
          audioChunks.push(event.data)
        }
      }

      // 分段录音：每次 stop 后上传识别，然后自动重新开始
      mediaRecorder.onstop = async () => {
        if (audioChunks.length === 0) return

        const blob = new Blob(audioChunks, { type: mediaRecorder?.mimeType || 'audio/webm' })
        audioChunks = []

        // 上传到后端 STT 识别（不阻塞下一段录音）
        transcribeBlob(blob)

        // 如果仍在监听，继续录制下一段
        if (isListening.value && !restarting) {
          restarting = true
          try {
            audioChunks = []
            mediaRecorder?.start()
          } catch {
            // 启动失败
          } finally {
            restarting = false
          }
        }
      }

      mediaRecorder.start()
      isListening.value = true

      // 定时触发分段停止
      if (segmentTimer) clearInterval(segmentTimer)
      segmentTimer = setInterval(() => {
        if (mediaRecorder && mediaRecorder.state === 'recording' && !restarting) {
          try { mediaRecorder.stop() } catch {}
        }
      }, SEGMENT_INTERVAL)

    } catch (err: any) {
      const errName = err.name || 'UnknownError'
      let hint = ''
      if (errName === 'NotAllowedError') hint = '麦克风权限被拒绝，请在浏览器地址栏允许麦克风访问'
      else if (errName === 'NotFoundError') hint = '未找到麦克风设备'
      else if (errName === 'NotReadableError') hint = '麦克风被其他程序占用'
      else if (errName === 'SecurityError') hint = '需要 HTTPS 或 localhost 环境才能访问麦克风'
      onError?.(hint || err.message || errName)
    }
  }

  async function transcribeBlob(blob: Blob) {
    const formData = new FormData()
    formData.append('file', blob, `audio_${Date.now()}.webm`)

    try {
      const controller = new AbortController()
      const timeoutId = setTimeout(() => controller.abort(), 120000)

      const response = await fetch('/api/omni/transcribe?language=zh', {
        method: 'POST',
        headers: { 'Authorization': `Bearer ${localStorage.getItem('token') || ''}` },
        body: formData,
        signal: controller.signal,
      })
      clearTimeout(timeoutId)

      if (response.ok) {
        const data = await response.json()
        if (data.text) {
          onResult?.(data.text, true)
        }
      } else if (response.status === 401) {
        onError?.('登录已过期，请重新登录')
      } else if (response.status === 503) {
        onError?.('语音识别服务暂不可用')
      }
    } catch (err: any) {
      if (err.name !== 'AbortError') {
        console.error('[STT] 请求失败:', err)
      }
    }
  }

  function stop() {
    if (segmentTimer) {
      clearInterval(segmentTimer)
      segmentTimer = null
    }
    // 最后一段录音也上传识别
    if (mediaRecorder && mediaRecorder.state !== 'inactive') {
      try { mediaRecorder.stop() } catch {}
    }
    if (mediaStream) {
      mediaStream.getTracks().forEach(t => t.stop())
      mediaStream = null
    }
    isListening.value = false
  }

  onBeforeUnmount(() => {
    stop()
  })

  return { isListening, isSupported, start, stop }
}
