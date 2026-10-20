<template>
  <div class="camera-simulator">
    <!-- 顶部工具栏 -->
    <div class="toolbar">
      <div class="voice-delivery-mode">
        <div class="voice-delivery-title">
          <el-icon><Microphone /></el-icon>
          <span>白泽语音交付</span>
          <el-tag size="small" :type="currentTab === 'auto' ? 'info' : 'success'">
            {{ currentTabLabel }}
          </el-tag>
        </div>
        <div class="voice-delivery-desc">
          无需选择业务模块，白泽会根据语音自动归类并执行现场工作。
        </div>
      </div>
      
      <div class="toolbar-right">
        <el-input 
          v-model="projectName" 
          placeholder="工程名称" 
          size="small"
          style="width: 150px; margin-right: 8px;"
        />
        <el-input 
          v-model="sectionName" 
          placeholder="施工区域" 
          size="small"
          style="width: 120px; margin-right: 8px;"
        />
        <el-select v-model="weather" placeholder="天气" size="small" style="width: 100px;">
          <el-option label="晴" value="晴" />
          <el-option label="多云" value="多云" />
          <el-option label="阴" value="阴" />
          <el-option label="雨" value="雨" />
        </el-select>
      </div>
    </div>

    <!-- 主内容区 -->
    <div class="main-content">
      <!-- 左侧：视频流 + 拍照 -->
      <div class="video-section">
        <div class="video-container" ref="videoContainer">
          <video 
            ref="videoEl" 
            autoplay 
            playsinline
            class="video-stream"
          ></video>
          
          <!-- 水印层 -->
          <div class="watermark-overlay">
            <div class="watermark-text">
              <div>工程：{{ projectName || '未设置' }}</div>
              <div>单位工程：{{ unitProjectName || '-' }}</div>
              <div>施工区域：{{ sectionName || '未设置' }}</div>
              <div>拍摄时间：{{ currentTime }}</div>
              <div>天气：{{ weather || '-' }}</div>
              <div>智能体：白泽</div>
            </div>
          </div>

          <!-- 拍照按钮 -->
          <div class="capture-controls">
            <el-button 
              type="primary" 
              circle 
              size="large"
              @click="capturePhoto"
              :disabled="!isCameraActive"
            >
              <el-icon><Camera /></el-icon>
            </el-button>
          </div>

          <!-- 未激活状态 -->
          <div v-if="!isCameraActive" class="camera-inactive">
            <el-icon size="48"><VideoCamera /></el-icon>
            <p>点击"启动摄像头"开始</p>
          </div>

          <!-- 语音识别状态显示 -->
          <div v-if="isListening" class="voice-status">
            <el-icon size="32"><Microphone /></el-icon>
            <p>正在聆听...</p>
          </div>

          <!-- 语音识别结果 -->
          <div v-if="voiceResult" class="voice-result">
            <el-tag type="success">{{ voiceResult }}</el-tag>
          </div>
        </div>

        <!-- 控制按钮 -->
        <div class="controls">
          <el-button 
            type="primary" 
            @click="toggleCamera"
            :loading="cameraLoading"
          >
            {{ isCameraActive ? '停止摄像头' : '启动摄像头' }}
          </el-button>
          
          <el-button 
            @click="toggleVoiceRecognition"
            :type="isListening ? 'warning' : ''"
            :loading="voiceLoading"
          >
            {{ isListening ? '结束并交付语音' : '开始语音交付' }}
          </el-button>

          <el-button @click="clearPhotos">清空照片</el-button>
        </div>

        <!-- 语音指令提示 -->
        <div class="voice-hints">
          <el-alert 
            title="语音指令提示" 
            type="info" 
            show-icon
            :closable="false"
          >
            <template #default>
              <div>当前自动识别业务：{{ currentTabLabel }}</div>
              <div>直接说出工作内容，白泽会自动分类：</div>
              <ul>
                <li>“开始巡视三号楼”“验收地下室防水”“开始旁站混凝土浇筑”</li>
                <li>“记录钢筋见证取样”“发现质量问题”“开始安全巡检”“复查整改闭环”</li>
                <li>“拍张照片”“结束当前工作”“这个流程怎么操作”</li>
              </ul>
              <el-button size="small" type="primary" plain @click="showCurrentWorkflow" style="margin-top: 6px;">
                查看已识别业务流程
              </el-button>
            </template>
          </el-alert>
        </div>
      </div>

      <!-- 右侧：照片列表 -->
      <div class="photos-section">
        <el-card shadow="never" style="height: 100%;">
          <template #header>
            <div class="photos-header">
              <span class="section-title">已拍摄照片 ({{ photos.length }})</span>
              <div class="photos-actions">
                <el-button size="small" type="success" plain @click="triggerImportImages">
                  <el-icon style="margin-right: 4px;"><Upload /></el-icon>导入图片
                </el-button>
                <el-button size="small" type="primary" @click="uploadPhotos">上传到服务器</el-button>
                <input ref="importInputRef" type="file" accept="image/*" multiple style="display:none" @change="handleImportImages" />
              </div>
            </div>
          </template>

          <div class="photos-grid">
            <el-empty v-if="photos.length === 0" description="暂无照片" />
            
            <div 
              v-for="(photo, index) in photos" 
              :key="index"
              class="photo-item"
            >
              <img :src="photo.dataUrl" alt="" class="photo-image" @click="viewPhoto(index)" />
              
              <div class="photo-info">
                <span>{{ photo.time }}</span>
                <el-button 
                  size="small" 
                  type="danger" 
                  link
                  @click="deletePhoto(index)"
                >
                  删除
                </el-button>
              </div>

              <!-- 照片详情弹窗 -->
              <el-dialog 
                v-model="photoDetailVisible" 
                :title="'照片 ' + (index + 1)"
                width="800px"
              >
                <img :src="photos[photoDetailIndex]?.dataUrl" style="width: 100%;" alt="" />
                <div class="photo-details">
                  <el-descriptions :column="1" border size="small">
                    <el-descriptions-item label="拍摄时间">{{ photos[photoDetailIndex]?.time }}</el-descriptions-item>
                    <el-descriptions-item label="工程名称">{{ projectName || '未设置' }}</el-descriptions-item>
                    <el-descriptions-item label="施工区域">{{ sectionName || '未设置' }}</el-descriptions-item>
                    <el-descriptions-item label="天气">{{ weather || '-' }}</el-descriptions-item>
                  </el-descriptions>
                </div>
              </el-dialog>
            </div>
          </div>
        </el-card>
      </div>
    </div>

    <!-- 当前模块信息 -->
    <div class="module-info">
      <el-alert 
        :title="currentModuleInfo.title" 
        :description="currentModuleInfo.description"
        type="info"
        show-icon
      />
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, onBeforeUnmount, computed } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Camera, VideoCamera, Microphone, Upload } from '@element-plus/icons-vue'
import { useAuthStore } from '@/stores/auth'
import { hazardApi } from '@/api'

const auth = useAuthStore()

// 现场工作全部由语音自动识别，不再由用户手工选择。
const workModules = [
  { value: 'inspection', label: '巡视记录' },
  { value: 'acceptance', label: '验收记录' },
  { value: 'station', label: '旁站记录' },
  { value: 'witness', label: '见证记录' },
  { value: 'quality', label: '质量问题' },
  { value: 'other', label: '其他工作' },
  { value: 'hazard', label: '安全巡检' },
  { value: 'rectify', label: '整改跟踪' },
]

const currentTab = ref('auto')
const projectName = ref('广东监理示范工程')
const unitProjectName = ref('广东监理')
const sectionName = ref('主体结构')
const weather = ref('晴')

// 视频流相关
const videoEl = ref<HTMLVideoElement | null>(null)
const videoContainer = ref<HTMLElement | null>(null)
let stream: MediaStream | null = null
let audioStream: MediaStream | null = null
const isCameraActive = ref(false)
const isAudioActive = ref(false)
const cameraLoading = ref(false)

// 语音识别相关
let mediaRecorder: MediaRecorder | null = null
let audioChunks: Blob[] = []
const isListening = ref(false)
const voiceLoading = ref(false)
const voiceResult = ref('')

// 照片列表
interface PhotoItem {
  dataUrl: string
  time: string
  blob?: Blob
}

const photos = ref<PhotoItem[]>([])
const photoDetailVisible = ref(false)
const photoDetailIndex = ref(0)

// 展示白泽最近一次自动判断的业务类型。
const currentModuleInfo = computed(() => {
  const info: Record<string, { title: string; description: string }> = {
    auto: {
      title: '综合现场识别',
      description: '直接通过语音交付工作，白泽将自动判断业务类型并关联照片、流程指引和语音回执。',
    },
    inspection: {
      title: '巡视记录',
      description: '根据巡检指引对每个环节进行拍照，照片自动添加水印（工程名称、施工区域、时间等）',
    },
    acceptance: {
      title: '验收记录',
      description: '根据知识图谱中的验收流程生成省统表，拍照记录验收过程',
    },
    station: {
      title: '旁站记录',
      description: '对关键工序进行全程旁站监督，拍照记录施工过程',
    },
    witness: {
      title: '见证记录',
      description: '拍照记录材料信息，生成见证报告（格式后续添加）',
    },
    quality: {
      title: '质量问题',
      description: '记录现场发现的质量问题，拍照取证并生成整改建议',
    },
    other: {
      title: '其他工作',
      description: '记录会议、发文、闭合记录等其他监理工作事项',
    },
    hazard: {
      title: '安全巡检',
      description: '调用 AI 模型自动识别现场安全隐患，生成整改建议',
    },
    rectify: {
      title: '整改跟踪',
      description: '跟踪隐患整改进度，复查整改结果，形成闭环管理',
    },
  }
  return info[currentTab.value] || info.auto
})

// 当前模块标签
const currentTabLabel = computed(() => {
  const module = workModules.find(item => item.value === currentTab.value)
  return module?.label || '等待语音自动识别'
})

// 当前时间（每秒更新）
const currentTime = ref('')
let timer: ReturnType<typeof setInterval> | null = null

onMounted(() => {
  updateCurrentTime()
  timer = setInterval(updateCurrentTime, 1000)
})

onBeforeUnmount(() => {
  stopCamera()
  stopVoiceRecognition()
  stopAudioInput()
  if (timer) clearInterval(timer)
})

function updateCurrentTime() {
  const now = new Date()
  currentTime.value = now.toLocaleString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  })
}

async function toggleCamera() {
  if (isCameraActive.value) {
    stopCamera()
  } else {
    await startCamera()
  }
}

async function startCamera() {
  cameraLoading.value = true
  try {
    stream = await navigator.mediaDevices.getUserMedia({ 
      video: { facingMode: 'environment' },
      audio: false,
    })
    
    if (videoEl.value) {
      videoEl.value.srcObject = stream
      isCameraActive.value = true
      ElMessage.success('摄像头已启动')
    }
  } catch (e: any) {
    ElMessage.error('无法访问摄像头：' + e.message)
  } finally {
    cameraLoading.value = false
  }
}

function stopCamera() {
  if (stream) {
    stream.getTracks().forEach(track => track.stop())
    stream = null
  }
  if (videoEl.value) {
    videoEl.value.srcObject = null
  }
  isCameraActive.value = false
}

async function startAudioInput(): Promise<boolean> {
  if (audioStream) return true
  try {
    audioStream = await navigator.mediaDevices.getUserMedia({ audio: true })
    isAudioActive.value = true
    return true
  } catch (e: any) {
    ElMessage.error('无法访问麦克风：' + e.message)
    return false
  }
}

function stopAudioInput() {
  if (audioStream) {
    audioStream.getTracks().forEach(track => track.stop())
    audioStream = null
  }
  isAudioActive.value = false
}

// 语音识别功能
async function toggleVoiceRecognition() {
  if (isListening.value) {
    stopVoiceRecognition()
  } else {
    await startVoiceRecognition()
  }
}

async function startVoiceRecognition() {
  voiceLoading.value = true
  try {
    if (!await startAudioInput()) {
      voiceLoading.value = false
      return
    }

    // 创建 MediaRecorder 录制音频
    const mediaStream = audioStream

    if (!mediaStream) {
      ElMessage.error('音频流未启动')
      voiceLoading.value = false
      return
    }

    // 创建 MediaRecorder
    const options = MediaRecorder.isTypeSupported('audio/webm')
      ? { mimeType: 'audio/webm' }
      : MediaRecorder.isTypeSupported('audio/mp4')
        ? { mimeType: 'audio/mp4' }
        : {}

    mediaRecorder = new MediaRecorder(mediaStream, options)
    audioChunks = []

    mediaRecorder.ondataavailable = (event: BlobEvent) => {
      if (event.data.size > 0) {
        audioChunks.push(event.data)
      }
    }

    mediaRecorder.onstop = async () => {
      // 停止录音后发送到后端进行语音识别
      const audioBlob = new Blob(audioChunks, { type: mediaRecorder?.mimeType || 'audio/webm' })

      // 录音已停止，进入识别阶段（保持 loading）
      isListening.value = false
      ElMessage.info('正在识别语音...')

      try {
        const formData = new FormData()
        formData.append('file', audioBlob, 'audio.webm')
        formData.append('language', 'zh')

        const controller = new AbortController()
        const timeoutId = setTimeout(() => controller.abort(), 60000)

        const response = await fetch('/api/omni/transcribe', {
          method: 'POST',
          headers: { 'Authorization': `Bearer ${localStorage.getItem('token') || ''}` },
          body: formData,
          signal: controller.signal,
        })
        clearTimeout(timeoutId)

        if (response.ok) {
          const data = await response.json()
          voiceResult.value = data.text || ''

          if (!voiceResult.value) {
            ElMessage.warning('未识别到语音内容，请尝试大声或靠近麦克风说话')
          } else {
            // 根据识别结果执行相应操作
            processVoiceCommand(voiceResult.value)
          }
        } else {
          const errorData = await response.json().catch(() => ({}))
          ElMessage.error('语音识别失败：' + (errorData.detail || '未知错误'))
        }
      } catch (e: any) {
        if (e.name === 'AbortError') {
          ElMessage.error('语音识别请求超时')
        } else {
          ElMessage.error('语音识别请求失败：' + e.message)
        }
      } finally {
        voiceLoading.value = false
      }
    }

    // 开始录音
    mediaRecorder.start()
    isListening.value = true
    voiceResult.value = ''
    voiceLoading.value = false
    ElMessage.success('白泽正在聆听，请直接说出现场工作...')

  } catch (e: any) {
    ElMessage.error('启动语音识别失败：' + e.message)
    voiceLoading.value = false
  }
}

function stopVoiceRecognition() {
  if (mediaRecorder && mediaRecorder.state !== 'inactive') {
    voiceLoading.value = true
    mediaRecorder.stop()
  } else {
    // 无活跃录音器时强制重置状态，防止卡死
    isListening.value = false
    voiceLoading.value = false
  }
}

// 处理语音指令
async function processVoiceCommand(text: string) {
  // 后端根据语音自动判断业务模块，页面不再要求用户手动选择。
  try {
    const resp = await fetch('/api/local/voice-command', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${localStorage.getItem('token') || ''}`,
      },
      body: JSON.stringify({ text, module: currentTab.value }),
    })
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
    const result = await resp.json()
    const action = result.action
    const reply = result.reply || ''
    const workflow = result.workflow || ''
    const resolvedModule = normalizeModule(result.module || result.params?.target_module)
    if (resolvedModule) currentTab.value = resolvedModule

    switch (action) {
      case 'capture_photo':
        capturePhoto()
        if (reply) {
          ElMessage.success(reply)
          void speakReply(reply)
        }
        break
      case 'start':
        ElMessage.success(reply || `已开始${tabLabel(currentTab.value)}语音交付`)
        void speakReply(reply || `已开始${tabLabel(currentTab.value)}语音交付`)
        if (workflow) showWorkflowGuide(workflow)
        break
      case 'end':
        ElMessage.success(reply || `已结束${tabLabel(currentTab.value)}语音交付`)
        void speakReply(reply || `已结束${tabLabel(currentTab.value)}语音交付`)
        break
      case 'guidance':
        showWorkflowGuide(workflow || result.reply || '暂无流程指引')
        void speakReply(reply || `${tabLabel(currentTab.value)}流程已展示`)
        break
      case 'query':
        ElMessage.info({ message: reply || `识别到语音："${text}"`, duration: 8000 })
        void speakReply(reply)
        break
      case 'switch_module':
        ElMessage.success(reply || `已自动识别为${tabLabel(currentTab.value)}`)
        void speakReply(reply || `已自动识别为${tabLabel(currentTab.value)}`)
        break
      default:
        ElMessage.info(reply || `识别到语音："${text}"`)
        void speakReply(reply)
    }
  } catch (e) {
    console.error('LLM 指令匹配失败，降级到关键字匹配', e)
    fallbackKeywordMatch(text)
  }
}

function tabLabel(tab: string): string {
  return workModules.find(item => item.value === tab)?.label || '待语音识别'
}

function normalizeModule(module: unknown): string | null {
  const normalized = module === 'quality_issue' ? 'quality' : String(module || '')
  return workModules.some(item => item.value === normalized) ? normalized : null
}

function inferLocalModule(text: string): string | null {
  const rules: Array<[string, string[]]> = [
    ['rectify', ['整改', '复查', '复验', '闭环', '销项']],
    ['station', ['旁站', '关键工序']],
    ['witness', ['见证', '取样', '送检']],
    ['acceptance', ['验收', '检验批', '隐蔽工程']],
    ['quality', ['质量问题', '质量缺陷', '不合格']],
    ['hazard', ['安全巡检', '安全检查', '安全隐患', '危险源']],
    ['inspection', ['巡视', '巡查', '现场检查']],
    ['other', ['其他工作', '会议', '发文', '联系单', '备忘']],
  ]
  return rules.find(([, keywords]) => keywords.some(keyword => text.includes(keyword)))?.[0] || null
}

async function speakReply(text: string) {
  const content = text.trim().slice(0, 500)
  if (!content) return
  try {
    const response = await fetch('/api/local/tts/synthesize', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${localStorage.getItem('token') || ''}`,
      },
      body: JSON.stringify({ text: content }),
    })
    if (!response.ok) return
    const data = await response.json()
    if (data.audio_url) {
      const audio = new Audio(data.audio_url)
      await audio.play()
    }
  } catch (error) {
    console.warn('白泽语音回执播放失败', error)
  }
}

function showWorkflowGuide(text: string) {
  ElMessageBox.alert(text, '工作流程指引', {
    confirmButtonText: '我知道了',
    customClass: 'workflow-guide-dialog',
  }).catch(() => {})
}

async function showCurrentWorkflow() {
  if (currentTab.value === 'auto') {
    ElMessage.info('请先通过语音说明要进行的现场工作，白泽会自动识别对应流程')
    return
  }
  try {
    const resp = await fetch(`/api/local/workflow-guide?module=${currentTab.value}`, {
      headers: { 'Authorization': `Bearer ${localStorage.getItem('token') || ''}` },
    })
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
    const data = await resp.json()
    showWorkflowGuide(data.workflow || '暂无流程指引')
  } catch (e) {
    ElMessage.error('获取流程指引失败')
  }
}

function fallbackKeywordMatch(text: string) {
  const lowerText = text.toLowerCase()
  const inferred = inferLocalModule(lowerText)
  if (inferred) currentTab.value = inferred
  if (lowerText.includes('拍照') || lowerText.includes('拍张')) {
    capturePhoto()
    return
  }
  const label = tabLabel(currentTab.value)
  if (lowerText.includes('开始')) {
    ElMessage.success(`已开始${label}语音交付`)
  } else if (lowerText.includes('结束') || lowerText.includes('完成')) {
    ElMessage.success(`已结束${label}语音交付`)
  } else {
    ElMessage.info(`已按${label}接收语音：“${text}”`)
  }
}

function capturePhoto() {
  if (!isCameraActive.value || !videoEl.value) {
    ElMessage.warning('请先启动摄像头')
    return
  }

  // 创建 canvas 并绘制视频帧 + 水印
  const canvas = document.createElement('canvas')
  const ctx = canvas.getContext('2d')
  
  if (!ctx) {
    ElMessage.error('无法创建画布')
    return
  }

  // 设置 canvas 尺寸与视频一致
  canvas.width = videoEl.value.videoWidth
  canvas.height = videoEl.value.videoHeight

  // 绘制视频帧
  ctx.drawImage(videoEl.value, 0, 0)

  // 添加水印（左下角）
  const watermarkText = [
    `工程：${projectName.value || '未设置'}`,
    `单位工程：${unitProjectName.value || '-'}`,
    `施工区域：${sectionName.value || '未设置'}`,
    `时间：${currentTime.value}`,
    `天气：${weather.value || '-'}`,
    '智能体：白泽',
  ].join('\n')

  ctx.fillStyle = 'rgba(255, 255, 255, 0.8)'
  ctx.font = 'bold 16px Arial'
  
  const lineHeight = 20
  const padding = 10
  
  // 计算水印总高度
  const lines = watermarkText.split('\n')
  const totalHeight = lines.length * lineHeight
  
  // 绘制半透明背景
  ctx.fillStyle = 'rgba(0, 0, 0, 0.5)'
  ctx.fillRect(padding - 5, canvas.height - totalHeight - padding * 2, 
    ctx.measureText(watermarkText).width + padding * 4, totalHeight + padding * 2)

  // 绘制文字
  ctx.fillStyle = '#ffffff'
  lines.forEach((line, i) => {
    ctx.fillText(line, padding, canvas.height - totalHeight + (i + 1) * lineHeight - padding)
  })

  // 转换为图片
  const dataUrl = canvas.toDataURL('image/jpeg', 0.9)
  
  // 创建 Blob（用于上传）
  canvas.toBlob((blob) => {
    if (blob) {
      photos.value.push({
        dataUrl,
        time: currentTime.value,
        blob,
      })
    }
  }, 'image/jpeg', 0.9)

  ElMessage.success('照片已拍摄')
}

function deletePhoto(index: number) {
  photos.value.splice(index, 1)
}

function clearPhotos() {
  if (photos.value.length === 0) return
  
  photos.value = []
  ElMessage.success('已清空所有照片')
}

async function uploadPhotos() {
  if (photos.value.length === 0) {
    ElMessage.warning('暂无照片可上传')
    return
  }

  const validPhotos = photos.value.filter(p => p.blob)
  if (validPhotos.length === 0) {
    ElMessage.warning('照片数据不完整，请重新拍摄')
    return
  }

  try {
    const formData = new FormData()
    validPhotos.forEach((photo, index) => {
      formData.append('photos', photo.blob!, `photo_${index + 1}.jpg`)
    })
    formData.append('module', currentTab.value === 'auto' ? 'other' : currentTab.value)
    formData.append('project_name', projectName.value)
    formData.append('section_name', sectionName.value)

    const response = await fetch('/api/watermark/upload-photos', {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${auth.token}`,
      },
      body: formData,
    })

    if (response.ok) {
      ElMessage.success(`成功上传 ${validPhotos.length} 张照片`)
      photos.value = []
    } else {
      const errorData = await response.json().catch(() => ({}))
      ElMessage.error(errorData.detail || '上传失败')
    }
  } catch (e: any) {
    ElMessage.error('上传失败：' + e.message)
  }
}

function viewPhoto(index: number) {
  photoDetailIndex.value = index
  photoDetailVisible.value = true
}

// ===== 手工导入图片功能 =====
const importInputRef = ref<HTMLInputElement | null>(null)
const importingImages = ref(false)

function triggerImportImages() {
  importInputRef.value?.click()
}

async function handleImportImages(event: Event) {
  const input = event.target as HTMLInputElement
  if (!input.files || input.files.length === 0) return

  importingImages.value = true
  const files = Array.from(input.files)

  try {
    for (const file of files) {
      // 读取图片为 dataURL 和 blob
      const dataUrl = await fileToDataUrl(file)
      const now = new Date()
      const timeStr = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')} ${String(now.getHours()).padStart(2, '0')}:${String(now.getMinutes()).padStart(2, '0')}:${String(now.getSeconds()).padStart(2, '0')}`

      photos.value.push({
        dataUrl,
        time: timeStr,
        blob: file,
      })
    }

    ElMessage.success(`成功导入 ${files.length} 张图片`)

    // 如果是隐患识别模块，自动调用AI识别
    if (currentTab.value === 'hazard' || currentTab.value === 'quality') {
      await detectImportedImages(files)
    }
  } catch (e: any) {
    ElMessage.error('导入失败：' + e.message)
  } finally {
    importingImages.value = false
    input.value = '' // 清空input，允许再次选择同样的文件
  }
}

function fileToDataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(reader.result as string)
    reader.onerror = reject
    reader.readAsDataURL(file)
  })
}

// 对导入的图片调用AI隐患识别
async function detectImportedImages(files: File[]) {
  if (files.length === 0) return

  try {
    ElMessage.info('正在进行AI识别...')
    let identified = 0
    let failed = 0
    for (const file of files) {
      try {
        const result: any = await hazardApi.detectAndSave(file, {
          section: sectionName.value,
          weather: weather.value,
          remark: `PC批量导入：${currentTab.value}`,
        })
        if (result.has_hazard) identified += 1
      } catch {
        failed += 1
      }
    }
    if (failed > 0) {
      ElMessage.warning(`AI识别完成：发现 ${identified} 张隐患图片，${failed} 张识别失败`)
    } else if (identified > 0) {
      ElMessage.success(`AI识别完成，共识别 ${identified} 张图片中的隐患`)
    } else {
      ElMessage.success('AI识别完成，未发现隐患')
    }
  } catch (e: any) {
    ElMessage.warning('AI识别请求失败：' + e.message)
  }
}

</script>

<style scoped>
.camera-simulator {
  height: calc(100vh - 120px);
  display: flex;
  flex-direction: column;
}

.toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 0;
  border-bottom: 1px solid #e8e8e8;
}

.voice-delivery-mode {
  min-width: 360px;
}

.voice-delivery-title {
  display: flex;
  align-items: center;
  gap: 8px;
  color: #0d3b66;
  font-size: 16px;
  font-weight: 700;
}

.voice-delivery-desc {
  margin-top: 4px;
  color: #7a8795;
  font-size: 12px;
}

:global(.workflow-guide-dialog .el-message-box__message) {
  white-space: pre-line;
  line-height: 1.7;
}

.toolbar-right {
  display: flex;
  align-items: center;
  gap: 8px;
}

.main-content {
  flex: 1;
  display: flex;
  gap: 20px;
  padding-top: 16px;
  overflow: hidden;
}

.video-section {
  flex: 3;
  display: flex;
  flex-direction: column;
}

.video-container {
  position: relative;
  background: #000;
  border-radius: 8px;
  overflow: hidden;
  flex: 1;
  min-height: 400px;
}

.video-stream {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.watermark-overlay {
  position: absolute;
  bottom: 0;
  left: 0;
  right: 0;
  padding: 20px;
  pointer-events: none;
}

.watermark-text {
  color: #fff;
  font-size: 14px;
  line-height: 1.6;
  text-shadow: 1px 1px 2px rgba(0, 0, 0, 0.8);
}

.capture-controls {
  position: absolute;
  bottom: 20px;
  left: 50%;
  transform: translateX(-50%);
}

.camera-inactive {
  position: absolute;
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%);
  text-align: center;
  color: #999;
}

.camera-inactive p {
  margin-top: 12px;
  font-size: 14px;
}

.voice-status {
  position: absolute;
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%);
  text-align: center;
  color: #409eff;
}

.voice-status p {
  margin-top: 12px;
  font-size: 14px;
}

.voice-result {
  position: absolute;
  bottom: 80px;
  left: 50%;
  transform: translateX(-50%);
}

.controls {
  display: flex;
  gap: 8px;
  padding-top: 12px;
}

.voice-hints {
  margin-top: 12px;
}

.photos-section {
  flex: 2;
  min-width: 300px;
}

.photos-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.photos-actions {
  display: flex;
  gap: 8px;
  align-items: center;
}

.photos-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
  gap: 12px;
  max-height: calc(100vh - 300px);
  overflow-y: auto;
}

.photo-item {
  border: 1px solid #e8e8e8;
  border-radius: 4px;
  overflow: hidden;
}

.photo-image {
  width: 100%;
  height: 120px;
  object-fit: cover;
  cursor: pointer;
}

.photo-info {
  padding: 8px;
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 12px;
}

.photo-details {
  margin-top: 16px;
}

.module-info {
  padding: 12px 0;
}

.section-title {
  font-weight: 600;
  font-size: 14px;
}

/* 移动端适配 */
@media (max-width: 768px) {
  .main-content {
    flex-direction: column;
  }

  .photos-section {
    max-height: 300px;
  }

  .toolbar {
    flex-direction: column;
    gap: 8px;
  }

  .toolbar-right {
    width: 100%;
  }

  .toolbar-right input,
  .toolbar-right select {
    flex: 1;
  }

  ul {
    padding-left: 20px;
    margin: 8px 0;
  }

  ul li {
    margin-bottom: 4px;
  }
}
</style>
