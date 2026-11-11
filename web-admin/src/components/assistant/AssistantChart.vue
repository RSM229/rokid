<!-- 图表卡：后端下发白名单 spec，这里只渲染，不执行任意配置 -->
<template>
  <div v-if="spec" class="a-chart">
    <div class="a-chart-head">
      <span class="a-chart-title">{{ spec.title }}</span>
      <span v-if="spec.meta" class="a-chart-meta">{{ spec.meta }}</span>
    </div>
    <VChart class="a-chart-canvas" :option="option" autoresize />
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import VChart from 'vue-echarts'
import type { ChartSpec } from '@/api/assistant'

const props = defineProps<{ spec: ChartSpec | null }>()

const PALETTE = ['#2f5d50', '#7c8f85', '#b9b2a0', '#a4703a', '#8a8f98']

const option = computed(() => {
  const spec = props.spec
  if (!spec) return {}
  const textStyle = { color: '#6b7280', fontSize: 11 }
  if (spec.chartType === 'pie') {
    const first = spec.series?.[0]
    const names = spec.xAxis || []
    return {
      color: PALETTE,
      tooltip: { trigger: 'item', borderColor: '#e5e5e2', textStyle: { color: '#1f2329' } },
      legend: { bottom: 0, icon: 'circle', itemWidth: 6, itemHeight: 6, textStyle },
      series: [
        {
          type: 'pie',
          radius: ['42%', '68%'],
          center: ['50%', '44%'],
          label: { color: '#6b7280', fontSize: 11 },
          labelLine: { lineStyle: { color: '#d9d9d4' } },
          itemStyle: { borderColor: '#fff', borderWidth: 1 },
          data: (first?.data || []).map((value, index) => ({
            name: names[index] ?? String(index + 1),
            value,
          })),
        },
      ],
    }
  }
  return {
    color: PALETTE,
    tooltip: {
      trigger: 'axis',
      borderColor: '#e5e5e2',
      textStyle: { color: '#1f2329' },
      axisPointer: { lineStyle: { color: '#d9d9d4' } },
    },
    legend: { top: 0, left: 0, icon: 'circle', itemWidth: 6, itemHeight: 6, textStyle },
    grid: { left: 4, right: 8, top: 28, bottom: 4, containLabel: true },
    xAxis: {
      type: 'category',
      data: spec.xAxis || [],
      axisLine: { lineStyle: { color: '#e5e5e2' } },
      axisTick: { show: false },
      axisLabel: { color: '#8a8f98', fontSize: 11 },
    },
    yAxis: {
      type: 'value',
      splitLine: { lineStyle: { color: '#f2f2ef' } },
      axisLabel: { color: '#8a8f98', fontSize: 11 },
    },
    series: (spec.series || []).map((item) => ({
      name: item.name,
      type: spec.chartType,
      data: item.data,
      barMaxWidth: 18,
      itemStyle: { borderRadius: spec.chartType === 'bar' ? [2, 2, 0, 0] : 0 },
      smooth: false,
    })),
  }
})
</script>

<style scoped>
.a-chart {
  margin-top: 12px;
  padding-top: 12px;
  border-top: 1px solid #f2f2ef;
}
.a-chart-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
}
.a-chart-title {
  font-size: 12.5px;
  color: #1f2329;
}
.a-chart-meta {
  font-size: 10.5px;
  color: #8a8f98;
  white-space: nowrap;
}
.a-chart-canvas {
  height: 210px;
  margin-top: 4px;
}
</style>
