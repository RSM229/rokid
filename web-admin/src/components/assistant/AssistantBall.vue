<!-- 监理小助手：右下角悬浮球 + 对话面板 -->
<template>
  <div class="assistant-root">
    <button
      v-show="!open"
      type="button"
      class="assistant-ball"
      :class="{ 'is-busy': busy }"
      title="监理小助手（Ctrl+K）"
      @click="toggle"
    >
      <el-icon :size="19"><ChatLineRound /></el-icon>
      <span v-if="busy" class="ball-dot" />
    </button>

    <section v-if="open" class="assistant-panel" aria-label="监理小助手">
      <header class="panel-head">
        <div class="head-main">
          <span class="head-title">监理小助手</span>
          <span class="head-context">{{ contextLabel }}</span>
        </div>
        <div class="head-actions">
          <span
            v-if="modelLabel"
            class="head-model"
            :title="`当前文本模型：${modelLabel}（在「系统设置 → 模型调用设置」修改）`"
          >{{ modelLabel }}</span>
          <button type="button" class="icon-btn" :class="{ active: historyOpen }" title="历史会话" @click="toggleHistory">
            <el-icon :size="15"><Clock /></el-icon>
          </button>
          <button type="button" class="icon-btn" title="收起" @click="close">
            <el-icon :size="15"><Close /></el-icon>
          </button>
        </div>
      </header>

      <template v-if="!historyOpen">
        <div ref="listRef" class="panel-body">
          <div v-if="!messages.length" class="empty">
            <p class="empty-title">直接说事</p>
            <div class="suggests">
              <button
                v-for="item in suggestions"
                :key="item"
                type="button"
                class="suggest"
                @click="submitText(item)"
              >
                {{ item }}
              </button>
            </div>
          </div>

          <AssistantMessage
            v-for="message in messages"
            :key="message.id"
            :message="message"
            @send="submitText"
            @retry="retry"
            @undo="undoMessage"
            @action="runAction"
            @card="openCard"
          />

          <div v-if="showPending" class="pending">
            <span class="pending-text">处理中</span>
            <span class="pending-dots"><i /><i /><i /></span>
          </div>
        </div>

        <footer class="panel-foot">
          <div v-if="pendingPreview" class="pending-image">
            <img :src="pendingPreview" alt="" />
            <button type="button" class="pending-image-close" title="移除" @click="clearPendingImage">
              <el-icon :size="11"><Close /></el-icon>
            </button>
          </div>
          <div class="composer">
            <button type="button" class="attach-btn" title="发送图片（自动做隐患识别）" @click="pickImage">
              <el-icon :size="16"><Picture /></el-icon>
            </button>
            <input ref="fileRef" type="file" accept="image/jpeg,image/png,image/webp" class="file-input" @change="onFileChange" />
            <textarea
              ref="inputRef"
              v-model="input"
              class="composer-input"
              rows="1"
              placeholder="说一句话，或发一张现场图"
              @keydown.enter="onEnter"
              @input="autoGrow"
            />
            <button
              type="button"
              class="send-btn"
              :disabled="busy || (!input.trim() && !pendingFile)"
              title="发送"
              @click="submit"
            >
              <el-icon :size="15"><Promotion /></el-icon>
            </button>
          </div>
          <p class="foot-hint">Enter 发送 · Shift+Enter 换行</p>
        </footer>
      </template>

      <template v-else>
        <div class="panel-body">
          <div class="history-head">
            <button type="button" class="link-btn" @click="newConversation">新会话</button>
            <span class="history-count">{{ conversations.length }} 条</span>
          </div>
          <p v-if="conversationsLoading" class="muted-line">加载中</p>
          <p v-else-if="!conversations.length" class="muted-line">还没有历史会话</p>
          <button
            v-for="item in conversations"
            :key="item.conversation_id"
            type="button"
            class="history-row"
            @click="openConversation(item.conversation_id)"
          >
            <span class="history-title">{{ item.title }}</span>
            <span class="history-meta">{{ item.updatedAt || item.updated_at }}</span>
          </button>
        </div>
      </template>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { ChatLineRound, Clock, Close, Picture, Promotion } from '@element-plus/icons-vue'
import AssistantMessage from './AssistantMessage.vue'
import { useAssistant } from '@/composables/useAssistant'

const suggestions = ['打开台账', '今天的记录有几条', '生成今天的监理日志', '未整改的隐患']

const MAX_IMAGE_BYTES = 10 * 1024 * 1024

const {
  messages,
  open,
  busy,
  historyOpen,
  conversations,
  conversationsLoading,
  contextLabel,
  modelLabel,
  send,
  loadModelLabel,
  retry,
  toggle,
  close,
  newConversation,
  runAction,
  openCard,
  undoMessage,
  toggleHistory,
  openConversation,
} = useAssistant()

const input = ref('')
const listRef = ref<HTMLElement | null>(null)
const inputRef = ref<HTMLTextAreaElement | null>(null)
const fileRef = ref<HTMLInputElement | null>(null)
const pendingFile = ref<File | null>(null)
const pendingPreview = ref('')

// 流式输出时消息自己会显示"思考中/正在生成"，底部就不再重复一个"处理中"
const showPending = computed(
  () => busy.value && !messages.value.some((message) => message.streaming),
)

function pickImage() {
  fileRef.value?.click()
}

function clearPendingImage() {
  if (pendingPreview.value) URL.revokeObjectURL(pendingPreview.value)
  pendingPreview.value = ''
  pendingFile.value = null
  if (fileRef.value) fileRef.value.value = ''
}

function onFileChange(event: Event) {
  const target = event.target as HTMLInputElement
  const file = target.files?.[0]
  if (!file) return
  if (!file.type.startsWith('image/')) return
  if (file.size > MAX_IMAGE_BYTES) {
    window.alert('图片不要超过 10MB')
    clearPendingImage()
    return
  }
  if (pendingPreview.value) URL.revokeObjectURL(pendingPreview.value)
  pendingFile.value = file
  pendingPreview.value = URL.createObjectURL(file)
  inputRef.value?.focus()
}

function scrollToBottom() {
  const el = listRef.value
  if (el) el.scrollTop = el.scrollHeight
}

function submitText(text: string) {
  input.value = ''
  autoGrow()
  void send(text)
}

function submit() {
  const text = input.value.trim()
  const image = pendingFile.value
  if (busy.value || (!text && !image)) return
  input.value = ''
  autoGrow()
  const file = image || undefined
  clearPendingImage()
  void send(text, { image: file })
}

function onEnter(event: KeyboardEvent) {
  if (event.shiftKey || event.isComposing) return
  event.preventDefault()
  submit()
}

function autoGrow() {
  const el = inputRef.value
  if (!el) return
  el.style.height = 'auto'
  el.style.height = `${Math.min(el.scrollHeight, 104)}px`
}

function onWindowKeydown(event: KeyboardEvent) {
  if ((event.ctrlKey || event.metaKey) && String(event.key).toLowerCase() === 'k') {
    event.preventDefault()
    toggle()
    nextTick(() => inputRef.value?.focus())
    return
  }
  if (event.key === 'Escape' && open.value) close()
}

watch(
  () => [messages.value.length, busy.value, open.value],
  () => nextTick(scrollToBottom),
)

watch(open, (value) => {
  if (!value) return
  nextTick(() => {
    scrollToBottom()
    inputRef.value?.focus()
  })
})

onMounted(() => {
  window.addEventListener('keydown', onWindowKeydown)
  void loadModelLabel()
})
onUnmounted(() => window.removeEventListener('keydown', onWindowKeydown))
</script>

<style scoped>
.assistant-root {
  --ink: #1f2329;
  --muted: #8a8f98;
  --line: #e6e6e2;
  --hairline: #f2f2ef;
  --hover: #f6f6f3;
  --accent: #2f5d50;
  position: relative;
  z-index: 2200;
}

/* ---------- 悬浮球 ---------- */
.assistant-ball {
  position: fixed;
  right: 24px;
  bottom: 24px;
  z-index: 2200;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 44px;
  height: 44px;
  border: 1px solid #dcdcd8;
  border-radius: 50%;
  background: #fff;
  color: var(--ink);
  cursor: pointer;
  transition: border-color 0.16s ease, color 0.16s ease;
}
.assistant-ball:hover {
  border-color: #b9b9b4;
}
.ball-dot {
  position: absolute;
  right: 4px;
  bottom: 5px;
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--accent);
  animation: ball-pulse 1.4s ease-in-out infinite;
}
@keyframes ball-pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.35; }
}

/* ---------- 面板 ---------- */
.assistant-panel {
  position: fixed;
  right: 24px;
  bottom: 84px;
  z-index: 2190;
  display: flex;
  flex-direction: column;
  width: 384px;
  height: min(580px, calc(100vh - 140px));
  border: 1px solid var(--line);
  border-radius: 12px;
  background: #fff;
  overflow: hidden;
}

.panel-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 13px 16px;
  border-bottom: 1px solid var(--hairline);
}
.head-main {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}
.head-title {
  font-size: 13.5px;
  font-weight: 600;
  color: var(--ink);
}
.head-context {
  font-size: 10.5px;
  color: var(--muted);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.head-actions {
  display: flex;
  align-items: center;
  gap: 2px;
}
.head-model {
  max-width: 118px;
  margin-right: 6px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 10.5px;
  color: #b3b8bf;
}
.attach-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border: 0;
  border-radius: 7px;
  background: none;
  color: #8a8f98;
  cursor: pointer;
}
.attach-btn:hover {
  background: var(--hover);
  color: var(--ink);
}
.file-input {
  display: none;
}
.pending-image {
  position: relative;
  display: inline-block;
  margin-bottom: 8px;
}
.pending-image img {
  display: block;
  max-width: 120px;
  max-height: 90px;
  border: 1px solid #ececea;
  border-radius: 8px;
  object-fit: cover;
}
.pending-image-close {
  position: absolute;
  top: -6px;
  right: -6px;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 18px;
  height: 18px;
  padding: 0;
  border: 1px solid #dcdcd8;
  border-radius: 50%;
  background: #fff;
  color: #5b616b;
  cursor: pointer;
}
.icon-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 26px;
  height: 26px;
  border: 0;
  border-radius: 6px;
  background: none;
  color: var(--muted);
  cursor: pointer;
}
.icon-btn:hover,
.icon-btn.active {
  background: var(--hover);
  color: var(--ink);
}

/* ---------- 消息区 ---------- */
.panel-body {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 16px;
  scroll-behavior: smooth;
}
.empty {
  padding: 4px 0;
}
.empty-title {
  margin: 0 0 10px;
  font-size: 12.5px;
  color: var(--muted);
}
.suggests {
  display: flex;
  flex-direction: column;
  border-top: 1px solid var(--hairline);
}
.suggest {
  padding: 9px 0;
  border: 0;
  border-bottom: 1px solid var(--hairline);
  background: none;
  text-align: left;
  font-size: 12.5px;
  color: #5b616b;
  cursor: pointer;
}
.suggest:hover {
  color: var(--ink);
}

.pending {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 8px;
  font-size: 11.5px;
  color: var(--muted);
}
.pending-dots {
  display: inline-flex;
  gap: 3px;
}
.pending-dots i {
  width: 3px;
  height: 3px;
  border-radius: 50%;
  background: #b9b9b4;
  animation: dot-blink 1.2s ease-in-out infinite;
}
.pending-dots i:nth-child(2) { animation-delay: 0.2s; }
.pending-dots i:nth-child(3) { animation-delay: 0.4s; }
@keyframes dot-blink {
  0%, 100% { opacity: 0.25; }
  50% { opacity: 1; }
}

/* ---------- 历史 ---------- */
.history-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding-bottom: 10px;
  border-bottom: 1px solid var(--hairline);
}
.history-count {
  font-size: 10.5px;
  color: var(--muted);
}
.history-row {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
  width: 100%;
  padding: 10px 0;
  border: 0;
  border-bottom: 1px solid var(--hairline);
  background: none;
  text-align: left;
  cursor: pointer;
}
.history-title {
  font-size: 12.5px;
  color: var(--ink);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.history-meta {
  flex: none;
  font-size: 10.5px;
  color: var(--muted);
}
.history-row:hover .history-title {
  color: var(--accent);
}
.muted-line {
  margin: 10px 0 0;
  font-size: 11.5px;
  color: var(--muted);
}
.link-btn {
  padding: 0;
  border: 0;
  background: none;
  font-size: 12.5px;
  color: var(--accent);
  cursor: pointer;
}
.link-btn:hover {
  text-decoration: underline;
}

/* ---------- 输入 ---------- */
.panel-foot {
  padding: 10px 16px 12px;
  border-top: 1px solid var(--hairline);
}
.composer {
  display: flex;
  align-items: flex-end;
  gap: 8px;
  padding: 7px 8px 7px 12px;
  border: 1px solid #e2e2de;
  border-radius: 10px;
}
.composer:focus-within {
  border-color: #b9b9b4;
}
.composer-input {
  flex: 1;
  min-height: 22px;
  max-height: 104px;
  padding: 1px 0;
  border: 0;
  outline: none;
  resize: none;
  background: transparent;
  font-size: 13.5px;
  line-height: 1.6;
  color: var(--ink);
  font-family: inherit;
}
.composer-input::placeholder {
  color: #b3b8bf;
}
.send-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border: 0;
  border-radius: 7px;
  background: var(--ink);
  color: #fff;
  cursor: pointer;
}
.send-btn:hover:not(:disabled) {
  background: var(--accent);
}
.send-btn:disabled {
  background: #e4e4e0;
  color: #b3b8bf;
  cursor: not-allowed;
}
.foot-hint {
  margin: 6px 0 0;
  font-size: 10.5px;
  color: #b3b8bf;
  text-align: right;
}

@media (max-width: 560px) {
  .assistant-panel {
    right: 12px;
    left: 12px;
    width: auto;
    bottom: 78px;
    height: calc(100vh - 120px);
  }
  .assistant-ball {
    right: 16px;
    bottom: 16px;
  }
}
</style>
