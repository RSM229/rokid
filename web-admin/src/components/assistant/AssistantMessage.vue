<!-- 单条消息：纯文字 + 列表式结果 + 链式动作，不用气泡堆叠/阴影卡片 -->
<template>
  <div class="msg" :class="message.role">
    <div class="msg-role">{{ message.role === 'user' ? '我' : '助手' }}</div>

    <img v-if="message.image" :src="message.image" class="msg-image" alt="" />
    <p v-if="message.text" class="msg-text">{{ message.text }}</p>
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
  </div>
</template>

<script setup lang="ts">
import { defineAsyncComponent } from 'vue'
import { ArrowRight } from '@element-plus/icons-vue'
import type { AssistantMessage } from '@/composables/useAssistant'
import type { AgentAction, AgentCard } from '@/api/assistant'

// 图表组件按需加载：避免 echarts/vue-echarts 被静态并入布局层 chunk，
// 改变 Rolldown 的分块（实测会导致入口 runtime chunk 消失、整站白屏）。
const AssistantChart = defineAsyncComponent(() => import('./AssistantChart.vue'))

defineProps<{ message: AssistantMessage }>()

const emit = defineEmits<{
  send: [text: string]
  retry: [message: AssistantMessage]
  undo: [message: AssistantMessage]
  action: [action: AgentAction]
  card: [card: AgentCard]
}>()

function cardMeta(card: AgentCard): string {
  return [card.sub, card.status, card.time].filter(Boolean).join(' · ')
}
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
</style>
