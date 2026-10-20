<template>
  <div class="pie-chart-wrapper" :style="{ height: height }">
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

interface PieDataItem {
  name: string
  value: number
}

const props = withDefaults(
  defineProps<{
    /** 饼图数据 */
    data: PieDataItem[]
    /** 图表标题 */
    title?: string
    /** 是否显示为环形图 */
    ring?: boolean
    /** 图表高度 */
    height?: string
  }>(),
  {
    title: '',
    ring: false,
    height: '320px',
  },
)

const option = computed(() => {
  if (!props.data?.length) return null
  return {
    title: props.title
      ? { text: props.title, left: 'center', textStyle: { fontSize: 14 } }
      : undefined,
    tooltip: { trigger: 'item', formatter: '{a} <br/>{b}: {c} ({d}%)' },
    legend: { orient: 'vertical', left: 'left', top: props.title ? 30 : 0 },
    series: [
      {
        name: '分布',
        type: 'pie',
        radius: props.ring ? ['40%', '70%'] : '60%',
        center: ['60%', '55%'],
        avoidLabelOverlap: false,
        itemStyle: { borderRadius: 4, borderColor: '#fff', borderWidth: 2 },
        label: { show: true, formatter: '{b}: {d}%' },
        data: props.data,
      },
    ],
  }
})
</script>

<style scoped>
.pie-chart-wrapper {
  width: 100%;
}
.chart {
  width: 100%;
  height: 100%;
}
</style>
