<!-- 单条消息：纯文字 + 列表式结果 + 链式动作，不用气泡堆叠/阴影卡片 -->
<template>
  <div class="msg" :class="message.role">
    <div class="msg-role">{{ message.role === 'user' ? '我' : '助手' }}</div>

    <AssistantReasoning
      v-if="message.reasoning || message.steps.length"
      :text="message.reasoning"
      :steps="message.steps"
      :elapsed-ms="message.thinkingMs"
      :done="!message.streaming"
      :answer-started="message.answerStarted"
    />

    <img v-if="message.image" :src="message.image" class="msg-image" alt="" />
    <p v-if="message.text" class="msg-text" :class="{ streaming: message.streaming && message.answerStarted }">
      <AssistantAnswer
        v-if="message.role === 'assistant'"
        :text="message.text"
        @open-cite="openCite"
      />
      <template v-else>{{ message.text }}</template>
    </p>
    <p v-if="message.hint" class="msg-hint">{{ message.hint }}</p>

    <AssistantChart v-if="message.chart" :spec="message.chart" />

    <div v-if="message.cards.length" class="result-list">
      <button
        v-for="card in message.cards"
        :key="`${card.kind}-${card.id}`"
        type="button"
        class="result-row"
        @click="emit('card', card)"
      >
        <span class="result-main">
          <span class="result-title">{{ card.title }}</span>
          <span class="result-meta">{{ cardMeta(card) }}</span>
        </span>
        <el-icon class="result-chevron"><ArrowRight /></el-icon>
      </button>
    </div>

    <div v-if="message.actions.length || message.undo || message.retryText" class="msg-actions">
      <button
        v-for="action in message.actions"
        :key="`${action.type}-${action.label}`"
        type="button"
        class="link-btn"
        @click="emit('action', action)"
      >
        {{ action.label }}
      </button>
      <button v-if="message.undo" type="button" class="link-btn" @click="emit('undo', message)">
        撤销
      </button>
      <button v-if="message.retryText" type="button" class="link-btn" @click="emit('retry', message)">
        重试
      </button>
    </div>

    <div v-if="message.chips.length" class="chips">
      <button
        v-for="chip in message.chips"
        :key="chip"
        type="button"
        class="chip"
        @click="emit('send', chip)"
      >
        {{ chip }}
      </button>
    </div>

    <!-- 文末统一归纳：引用列表 -->
    <div v-if="message.citations.length" class="cite-summary">
      <div class="cite-summary-head">引用 · {{ message.citations.length }} 条</div>
      <button
        v-for="citation in message.citations"
        :key="citation.index"
        type="button"
        class="cite-row"
        @click="openCiteRow(citation, $event)"
      >
        <span class="cite-row-index">{{ citation.index }}</span>
        <span class="cite-row-main">
          <span class="cite-row-title">{{ citation.title }}</span>
          <span class="cite-row-sub">{{ citation.sub || kindLabel(citation.kind) }}</span>
        </span>
      </button>
    </div>

    <Teleport to="body">
      <div v-if="activeCitation" class="cite-pop" :style="popStyle" @mousedown.stop>
        <div class="cite-pop-head">
          <span class="cite-pop-kind">{{ activeCitation.sub || kindLabel(activeCitation.kind) }}</span>
          <button type="button" class="cite-pop-close" @click="activeCitation = null">关闭</button>
        </div>
        <div class="cite-pop-title">{{ activeCitation.title }}</div>
        <p v-if="activeCitation.snippet" class="cite-pop-snippet">{{ activeCitation.snippet }}</p>
        <div class="cite-pop-actions">
          <button
            v-if="activeCitation.target"
            type="button"
            class="link-btn"
            @click="goCitation(activeCitation)"
          >打开来源</button>
        </div>
      </div>
    </Teleport>
  </div>
</template>

<script setup lang="ts">
import { defineAsyncComponent, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ArrowRight } from '@element-plus/icons-vue'
import AssistantReasoning from './AssistantReasoning.vue'
import AssistantAnswer from './AssistantAnswer.vue'
import type { AssistantMessage } from '@/composables/useAssistant'
import type { AgentAction, AgentCard, Citation } from '@/api/assistant'

// 图表组件按需加载：避免 echarts/vue-echarts 被静态并入布局层 chunk，
// 改变 Rolldown 的分块（实测会导致入口 runtime chunk 消失、整站白屏）。
const AssistantChart = defineAsyncComponent(() => import('./AssistantChart.vue'))

const props = defineProps<{ message: AssistantMessage }>()

const emit = defineEmits<{
  send: [text: string]
  retry: [message: AssistantMessage]
  undo: [message: AssistantMessage]
  action: [action: AgentAction]
  card: [card: AgentCard]
}>()

const router = useRouter()
const activeCitation = ref<Citation | null>(null)
const popStyle = ref<Record<string, string>>({})

const KIND_LABEL: Record<string, string> = {
  regulation: '规范',
  document: '文书',
  work_record: '记录',
  hazard: '隐患',
  knowledge: '知识库',
  note: '笔记',
}

function kindLabel(kind: string): string {
  return KIND_LABEL[kind] || '资料'
}

function cardMeta(card: AgentCard): string {
  return [card.sub, card.status, card.time].filter(Boolean).join(' · ')
}

/** 把浮层放在角标下方；放不下就翻到上方，并夹在视口内 */
function positionPop(rect: DOMRect) {
  const width = 300
  const estimatedHeight = 230
  const left = Math.min(Math.max(8, rect.left), window.innerWidth - width - 8)
  const below = rect.bottom + 8
  const top = below + estimatedHeight > window.innerHeight
    ? Math.max(8, rect.top - estimatedHeight - 8)
    : below
  popStyle.value = { left: `${left}px`, top: `${top}px`, width: `${width}px` }
}

function openCite(index: number, event: MouseEvent) {
  const citation = props.message.citations.find((item) => item.index === index)
  if (!citation) return
  positionPop((event.currentTarget as HTMLElement).getBoundingClientRect())
  activeCitation.value = citation
}

function openCiteRow(citation: Citation, event: MouseEvent) {
  positionPop((event.currentTarget as HTMLElement).getBoundingClientRect())
  activeCitation.value = citation
}

function goCitation(citation: Citation) {
  const target = citation.target || ''
  if (!target.startsWith('/') || target.startsWith('/pages')) return
  activeCitation.value = null
  void router.push(target)
}

function onDocumentMouseDown(event: MouseEvent) {
  if (!activeCitation.value) return
  const target = event.target as HTMLElement | null
  if (target && (target.closest('.cite-pop') || target.closest('.cite-inline') || target.closest('.cite-row'))) {
    return
  }
  activeCitation.value = null
}

function onDocumentKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape') activeCitation.value = null
}

onMounted(() => {
  document.addEventListener('mousedown', onDocumentMouseDown)
  document.addEventListener('keydown', onDocumentKeydown)
})

onBeforeUnmount(() => {
  document.removeEventListener('mousedown', onDocumentMouseDown)
  document.removeEventListener('keydown', onDocumentKeydown)
})
</script>

<style scoped>
.msg {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.msg + .msg {
  margin-top: 20px;
}
.msg.user {
  align-items: flex-end;
}
.msg-role {
  font-size: 10.5px;
  letter-spacing: 0.06em;
  color: #9a9fa8;
}
.msg-text {
  margin: 0;
  font-size: 13.5px;
  line-height: 1.72;
  color: #1f2329;
  white-space: pre-wrap;
  word-break: break-word;
}
.msg.user .msg-text {
  max-width: 86%;
  padding: 8px 12px;
  border-radius: 10px;
  background: #f4f4f1;
}
.msg-text.streaming::after {
  content: '▍';
  margin-left: 1px;
  color: #c2c6cc;
  animation: caret-blink 1s steps(2, start) infinite;
}
@keyframes caret-blink {
  50% { opacity: 0; }
}
.msg-hint {
  margin: 0;
  font-size: 11.5px;
  line-height: 1.6;
  color: #9a9fa8;
}
.msg-image {
  display: block;
  max-width: 220px;
  max-height: 160px;
  border-radius: 8px;
  object-fit: cover;
}

.result-list {
  margin-top: 6px;
  border-top: 1px solid #f2f2ef;
}
.result-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  width: 100%;
  padding: 9px 0;
  border: 0;
  border-bottom: 1px solid #f6f6f2;
  background: none;
  text-align: left;
  cursor: pointer;
}
.result-main {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}
.result-title {
  font-size: 12.5px;
  color: #1f2329;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.result-meta {
  font-size: 10.5px;
  color: #9a9fa8;
}
.result-chevron {
  flex: none;
  font-size: 12px;
  color: #c2c6cc;
}
.result-row:hover .result-title {
  color: #2f5d50;
}
.result-row:hover .result-chevron {
  color: #2f5d50;
}

.msg-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 14px;
  margin-top: 2px;
}
.link-btn {
  padding: 0;
  border: 0;
  background: none;
  font-size: 12.5px;
  color: #2f5d50;
  cursor: pointer;
}
.link-btn:hover {
  text-decoration: underline;
}

.chips {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 4px;
}
.chip {
  padding: 4px 10px;
  border: 1px solid #e2e2de;
  border-radius: 999px;
  background: none;
  font-size: 11.5px;
  color: #5b616b;
  cursor: pointer;
}
.chip:hover {
  border-color: #b9b9b4;
  color: #1f2329;
}

/* ---------- 引用：文末归纳 + 行内浮层 ---------- */
.cite-summary {
  margin-top: 12px;
  padding-top: 10px;
  border-top: 1px solid #f2f2ef;
}
.cite-summary-head {
  margin-bottom: 4px;
  font-size: 10.5px;
  letter-spacing: 0.04em;
  color: #b3b8bf;
}
.cite-row {
  display: flex;
  align-items: baseline;
  gap: 8px;
  width: 100%;
  padding: 5px 0;
  border: 0;
  background: none;
  text-align: left;
  cursor: pointer;
}
.cite-row + .cite-row {
  border-top: 1px solid #f8f8f5;
}
.cite-row-index {
  flex: none;
  min-width: 16px;
  height: 16px;
  padding: 0 3px;
  border: 1px solid #e2e2de;
  border-radius: 4px;
  font-size: 9.5px;
  line-height: 15px;
  color: #8a8f98;
  text-align: center;
}
.cite-row-main {
  display: flex;
  align-items: baseline;
  gap: 8px;
  min-width: 0;
}
.cite-row-title {
  font-size: 12px;
  color: #1f2329;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.cite-row-sub {
  flex: none;
  font-size: 10px;
  color: #9a9fa8;
}
.cite-row:hover .cite-row-title {
  color: #2f5d50;
}
.cite-row:hover .cite-row-index {
  border-color: #b9b9b4;
  color: #1f2329;
}

.cite-pop {
  position: fixed;
  z-index: 3000;
  padding: 12px 14px;
  border: 1px solid #e0e0dc;
  border-radius: 10px;
  background: #fff;
}
.cite-pop-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.cite-pop-kind {
  font-size: 10px;
  color: #8a8f98;
}
.cite-pop-close {
  padding: 0;
  border: 0;
  background: none;
  font-size: 10.5px;
  color: #9a9fa8;
  cursor: pointer;
}
.cite-pop-close:hover {
  color: #1f2329;
}
.cite-pop-title {
  margin-top: 4px;
  font-size: 12.5px;
  color: #1f2329;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.cite-pop-snippet {
  margin: 6px 0 0;
  max-height: 150px;
  overflow-y: auto;
  font-size: 11.5px;
  line-height: 1.65;
  color: #5b616b;
  white-space: pre-wrap;
  word-break: break-word;
}
.cite-pop-actions {
  display: flex;
  gap: 12px;
  margin-top: 8px;
}
</style>
