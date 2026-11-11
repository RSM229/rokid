<!-- 答案正文：把 [[n]] 渲染成可点击的引用角标，其余按纯文本原样输出 -->
<template>
  <template v-for="(segment, index) in segments" :key="index">
    <span v-if="segment.type === 'text'">{{ segment.text }}</span>
    <button
      v-else
      type="button"
      class="cite-inline"
      :title="`引用 ${segment.index}`"
      @click.stop="emit('open-cite', segment.index as number, $event)"
    >{{ segment.index }}</button>
  </template>
</template>

<script setup lang="ts">
import { computed } from 'vue'

interface Segment {
  type: 'text' | 'cite'
  text?: string
  index?: number
}

const props = defineProps<{ text: string }>()

const emit = defineEmits<{ 'open-cite': [index: number, event: MouseEvent] }>()

const segments = computed<Segment[]>(() => {
  const out: Segment[] = []
  const pattern = /\[\[(\d+)\]\]/g
  let cursor = 0
  let match: RegExpExecArray | null
  while ((match = pattern.exec(props.text)) !== null) {
    if (match.index > cursor) {
      out.push({ type: 'text', text: props.text.slice(cursor, match.index) })
    }
    out.push({ type: 'cite', index: Number(match[1]) })
    cursor = match.index + match[0].length
  }
  if (cursor < props.text.length) {
    out.push({ type: 'text', text: props.text.slice(cursor) })
  }
  if (!out.length) out.push({ type: 'text', text: props.text })
  return out
})
</script>

<style scoped>
.cite-inline {
  display: inline;
  margin: 0 2px;
  padding: 0 5px;
  border: 1px solid #e0e0dc;
  border-radius: 6px;
  background: #f6f6f3;
  color: #5b616b;
  font-size: 10px;
  line-height: 1.5;
  vertical-align: 1px;
  cursor: pointer;
  font-family: inherit;
}
.cite-inline:hover {
  border-color: #b9b9b4;
  background: #fff;
  color: #1f2329;
}
</style>
