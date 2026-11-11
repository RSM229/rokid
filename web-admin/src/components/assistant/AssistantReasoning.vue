<!-- 思考过程：折叠条 + 纯文本（不渲染 Markdown），自动跟随但尊重用户上翻 -->
<template>
  <div class="reasoning">
    <button type="button" class="reasoning-head" :aria-expanded="open" @click="toggle">
      <span class="reasoning-label">{{ label }}</span>
      <span class="reasoning-toggle">{{ open ? '收起' : '展开' }}</span>
    </button>
    <div v-show="open" ref="bodyRef" class="reasoning-body" @scroll="onScroll">
      <div v-for="(step, index) in steps" :key="`${index}-${step}`" class="step">{{ step }}</div>
      <pre v-if="text" class="reasoning-text">{{ text }}</pre>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'

const props = defineProps<{
  text: string
  steps: string[]
  elapsedMs: number
  done: boolean
  answerStarted: boolean
}>()

const open = ref(true)
const userToggled = ref(false)
const follow = ref(true)
const bodyRef = ref<HTMLElement | null>(null)
const nowMs = ref(Date.now())
const mountedAt = Date.now()
let timer: number | undefined

const liveMs = computed(() => {
  if (props.done) return props.elapsedMs || nowMs.value - mountedAt
  return Math.max(props.elapsedMs, nowMs.value - mountedAt)
})
const seconds = computed(() => Math.max(1, Math.round(liveMs.value / 1000)))

const label = computed(() => {
  if (!props.done && !props.answerStarted) return `思考中… ${seconds.value}s`
  const base = props.text ? '已深度思考' : '处理过程'
  return `${base}（${seconds.value} 秒）`
})

function toggle() {
  userToggled.value = true
  open.value = !open.value
}

function onScroll() {
  const el = bodyRef.value
  if (!el) return
  follow.value = el.scrollHeight - el.scrollTop - el.clientHeight < 28
}

function scrollToBottom() {
  const el = bodyRef.value
  if (!el || !follow.value) return
  el.scrollTop = el.scrollHeight
}

watch(
  () => [props.text.length, props.steps.length],
  () => {
    if (open.value) requestAnimationFrame(scrollToBottom)
  },
)

// 答案开始输出 / 本轮结束 → 自动收起；用户手动展开过则尊重用户
watch(
  () => [props.done, props.answerStarted],
  ([done, started]) => {
    if ((done || started) && !userToggled.value) open.value = false
  },
  { immediate: true },
)

watch(open, (value) => {
  if (!value) return
  follow.value = true
  requestAnimationFrame(scrollToBottom)
})

onMounted(() => {
  timer = window.setInterval(() => {
    if (!props.done) nowMs.value = Date.now()
  }, 1000)
})

onBeforeUnmount(() => {
  if (timer) window.clearInterval(timer)
})
</script>

<style scoped>
.reasoning {
  margin: 2px 0 10px;
}
.reasoning-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
  width: 100%;
  padding: 0;
  border: 0;
  background: none;
  text-align: left;
  cursor: pointer;
}
.reasoning-label {
  font-size: 11.5px;
  color: #8a8f98;
}
.reasoning-head:hover .reasoning-label {
  color: #1f2329;
}
.reasoning-toggle {
  font-size: 10.5px;
  color: #b3b8bf;
}
.reasoning-body {
  max-height: 200px;
  margin-top: 6px;
  padding-left: 8px;
  border-left: 1px solid #f2f2ef;
  overflow-y: auto;
  overscroll-behavior: contain;
}
.step {
  margin-bottom: 3px;
  font-size: 11px;
  line-height: 1.6;
  color: #9a9fa8;
}
.reasoning-text {
  margin: 0;
  font-family: ui-monospace, SFMono-Regular, Consolas, 'Liberation Mono', monospace;
  font-size: 11.5px;
  line-height: 1.7;
  color: #5b616b;
  white-space: pre-wrap;
  word-break: break-word;
}
</style>
