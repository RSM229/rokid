<template>
  <div class="line-chart-wrapper" :style="{ height: height }">
    <v-chart
      v-if="option"
      :option="option"
      :autoresize="true"
      class="chart"
    />
    <el-empty v-else description="暂无数据" :image-size="80" />
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'

interface SeriesItem {
  name: string
  data: (number | null)[]
  yAxisIndex?: number
  color?: string
  smooth?: boolean
}

const props = withDefaults(
  defineProps<{
    /** X 轴标签 */
    categories: string[]
    /** 系列数据 */
    series: SeriesItem[]
    /** 图表标题 */
    title?: string
    /** 是否显示双 Y 轴 */
    dualAxis?: boolean
    /** 左 Y 轴名称 */
    leftAxisName?: string
    /** 右 Y 轴名称 */
    rightAxisName?: string
    /** 图表高度 */
    height?: string
  }>(),
  {
    title: '',
    dualAxis: false,
    leftAxisName: '',
    rightAxisName: '',
    height: '320px',
  },
)

const option = computed(() => {
  if (!props.categories.length || !props.series.length) return null
  const yAxis = props.dualAxis
    ? [
        {
          type: 'value',
          name: props.leftAxisName,
          position: 'left',
          axisLine: { show: true },
        },
        {
          type: 'value',
          name: props.rightAxisName,
          position: 'right',
          axisLine: { show: true },
          splitLine: { show: false },
        },
      ]
    : [{ type: 'value', name: props.leftAxisName }]

  return {
    title: props.title ? { text: props.title, left: 'center', textStyle: { fontSize: 14 } } : undefined,
    tooltip: { trigger: 'axis' },
    legend: {
      data: props.series.map((s) => s.name),
      top: props.title ? 30 : 0,
    },
    grid: { left: 50, right: props.dualAxis ? 60 : 30, top: props.title ? 60 : 40, bottom: 30 },
    xAxis: { type: 'category', data: props.categories, boundaryGap: false },
    yAxis,
    series: props.series.map((s) => ({
      name: s.name,
      type: 'line',
      data: s.data,
      yAxisIndex: s.yAxisIndex ?? 0,
      smooth: s.smooth ?? true,
      itemStyle: s.color ? { color: s.color } : undefined,
      areaStyle: { opacity: 0.1 },
    })),
  }
})
</script>

<style scoped>
.line-chart-wrapper {
  width: 100%;
}
.chart {
  width: 100%;
  height: 100%;
}
</style>
