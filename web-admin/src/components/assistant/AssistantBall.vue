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

          <div v-if="busy" class="pending">
            <span class="pending-text">处理中</span>
            <span class="pending-dots"><i /><i /><i /></span>
          </div>
        </div>

        <footer class="panel-foot">
          <div class="composer">
            <textarea
              ref="inputRef"
              v-model="input"
              class="composer-input"
              rows="1"
              placeholder="说一句话，或点上面的示例"
              @keydown.enter="onEnter"
              @input="autoGrow"
            />
            <button
              type="button"
              class="send-btn"
              :disabled="busy || !input.trim()"
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
import { nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { ChatLineRound, Clock, Close, Promotion } from '@element-plus/icons-vue'
import AssistantMessage from './AssistantMessage.vue'
import { useAssistant } from '@/composables/useAssistant'

const suggestions = ['打开台账', '今天的记录有几条', '生成今天的监理日志', '未整改的隐患']

const {
  messages,
  open,
  busy,
  historyOpen,
  conversations,
  conversationsLoading,
  contextLabel,
  send,
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
  if (!text || busy.value) return
  submitText(text)
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

onMounted(() => window.addEventListener('keydown', onWindowKeydown))
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
