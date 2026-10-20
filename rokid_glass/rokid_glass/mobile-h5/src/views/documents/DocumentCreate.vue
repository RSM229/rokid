<script setup lang="ts">
/**
 * 创建日期：2026-07-18
 * 新建文书：选择文书类型 + 语音输入（后端 faster-whisper STT）+ LLM 辅助生成
 */
import { ref, onMounted, onBeforeUnmount } from 'vue'
import { useRouter } from 'vue-router'
import { showSuccessToast, showFailToast } from 'vant'
import { documentApi, knowledgeApi } from '@/api'
import http from '@/api/client'

const router = useRouter()

const docType = ref('inspection')
const docTypeOptions = [
  { text: '巡视记录', value: 'inspection' },
  { text: '监理日志', value: 'daily_log' },
  { text: '旁站记录', value: 'station' },
  { text: '见证取样', value: 'witness' },
  { text: '监理通知单', value: 'notice' },
  { text: '整改通知单', value: 'rectification' },
]
const showTypePicker = ref(false)
const title = ref('')
const content = ref('')
const recognizing = ref(false)
const saving = ref(false)
const generatedDocId = ref<number | null>(null)

// 后端 STT 相关状态
let mediaStream: MediaStream | null = null
let mediaRecorder: MediaRecorder | null = null
let audioChunks: Blob[] = []
let segmentTimer: ReturnType<typeof setInterval> | null = null
let restarting = false

function onTypeConfirm({ selectedOptions }: { selectedOptions: any[] }) {
  const sel = selectedOptions[0]
  if (sel) {
    docType.value = sel.value
    generatedDocId.value = null
    if (!title.value) title.value = sel.text
  }
  showTypePicker.value = false
}

async function toggleVoice() {
  if (recognizing.value) {
    stopVoice()
    return
  }
  if (typeof MediaRecorder === 'undefined' || !navigator.mediaDevices?.getUserMedia) {
    showFailToast('当前浏览器不支持语音录制')
    return
  }
  try {
    mediaStream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
    })
    const mimeType = MediaRecorder.isTypeSupported('audio/webm') ? 'audio/webm' : ''
    mediaRecorder = new MediaRecorder(mediaStream, mimeType ? { mimeType } : undefined)
    audioChunks = []

    mediaRecorder.ondataavailable = (e: BlobEvent) => {
      if (e.data.size > 0) audioChunks.push(e.data)
    }
    mediaRecorder.onstop = async () => {
      if (audioChunks.length === 0) return
      const blob = new Blob(audioChunks, { type: mediaRecorder?.mimeType || 'audio/webm' })
      audioChunks = []
      transcribeSegment(blob)
      if (recognizing.value && !restarting) {
        restarting = true
        try { audioChunks = []; mediaRecorder?.start() } catch {} finally { restarting = false }
      }
    }

    mediaRecorder.start()
    recognizing.value = true
    segmentTimer = setInterval(() => {
      if (mediaRecorder && mediaRecorder.state === 'recording' && !restarting) {
        try { mediaRecorder.stop() } catch {}
      }
    }, 5000)
  } catch (e: any) {
    showFailToast('麦克风开启失败：' + (e.message || e.name))
  }
}

function stopVoice() {
  if (segmentTimer) { clearInterval(segmentTimer); segmentTimer = null }
  if (mediaRecorder && mediaRecorder.state !== 'inactive') {
    try { mediaRecorder.stop() } catch {}
  }
  if (mediaStream) { mediaStream.getTracks().forEach(t => t.stop()); mediaStream = null }
  recognizing.value = false
}

async function transcribeSegment(blob: Blob) {
  const formData = new FormData()
  formData.append('file', blob, `audio_${Date.now()}.webm`)
  try {
    const resp = await fetch('/api/omni/transcribe?language=zh', {
      method: 'POST',
      headers: { Authorization: `Bearer ${localStorage.getItem('token') || ''}` },
      body: formData,
    })
    if (resp.ok) {
      const data = await resp.json()
      if (data.text) content.value = content.value + data.text
    }
  } catch {}
}

async function onLlmGenerate() {
  if (!content.value.trim()) {
    showFailToast('请先输入要点或语音')
    return
  }
  saving.value = true
  try {
    const res: any = await documentApi.generateWithLlm({
      doc_type: docType.value,
      points: content.value,
      title: title.value,
    })
    if (res?.content) content.value = res.content
    if (res?.title) title.value = res.title
    generatedDocId.value = Number(res?.doc_id || res?.id) || null
    showSuccessToast('已生成草稿，可继续编辑')
  } catch (e: any) {
    showFailToast(e?.response?.data?.detail || '生成失败')
  } finally {
    saving.value = false
  }
}

async function onSave() {
  if (!title.value || !content.value) {
    showFailToast('请填写标题与内容')
    return
  }
  saving.value = true
  try {
    const res: any = generatedDocId.value
      ? await documentApi.update(generatedDocId.value, {
          title: title.value,
          content: content.value,
        })
      : await documentApi.create({
          doc_type: docType.value,
          title: title.value,
          content: content.value,
        })
    const documentId = generatedDocId.value || Number(res?.doc_id || res?.id)
    showSuccessToast('已保存')
    router.replace(`/documents/${documentId}`)
  } catch (e: any) {
    showFailToast(e?.response?.data?.detail || '保存失败')
  } finally {
    saving.value = false
  }
}

onMounted(() => {})

onBeforeUnmount(() => {
  stopVoice()
})
</script>

<template>
  <div class="page">
    <van-cell-group inset>
      <van-field
        :model-value="docTypeOptions.find(o => o.value === docType)?.text"
        is-link
        readonly
        label="文书类型"
        placeholder="选择文书类型"
        @click="showTypePicker = true"
      />
      <van-field v-model="title" label="标题" placeholder="请输入标题" clearable />
    </van-cell-group>

    <van-cell-group inset style="margin-top: 12px" title="内容（可语音输入）">
      <van-field
        v-model="content"
        type="textarea"
        rows="6"
        autosize
        placeholder="点击麦克风语音输入，或手动编辑现场要点..."
        maxlength="-1"
        show-word-limit
      />
      <div class="voice-bar">
        <van-button
          size="small"
          :type="recognizing ? 'danger' : 'primary'"
          :icon="recognizing ? 'stop-circle-o' : 'volume-o'"
          @click="toggleVoice"
        >
          {{ recognizing ? '停止' : '语音输入' }}
        </van-button>
        <van-button size="small" plain type="primary" icon="magic-wand" :loading="saving" @click="onLlmGenerate">
          AI 润色
        </van-button>
      </div>
    </van-cell-group>

    <div class="actions">
      <van-button block type="primary" :loading="saving" @click="onSave">保存草稿</van-button>
    </div>

    <van-popup v-model:show="showTypePicker" position="bottom" round>
      <van-picker
        :columns="docTypeOptions.map(o => ({ text: o.text, value: o.value }))"
        @confirm="onTypeConfirm"
        @cancel="showTypePicker = false"
      />
    </van-popup>
  </div>
</template>

<style scoped>
.voice-bar {
  display: flex;
  gap: 8px;
  padding: 8px 16px 12px;
}
.actions {
  padding: 16px 12px;
}
</style>
