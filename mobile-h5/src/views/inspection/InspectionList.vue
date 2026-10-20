<script setup lang="ts">
/**
 * 创建日期：2026-07-19
 * AI 巡检会话列表：项目、开始时间、状态、采集数、隐患数
 * 下拉刷新，右下角浮动按钮启动新巡检
 */
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { inspectionApi } from '@/api'
import {
  formatDateTime,
  getStatusLabel,
  INSPECTION_STATUS_MAP,
} from '@/utils/format'
import { hapticLight } from '@/utils/haptic'

const router = useRouter()
const list = ref<any[]>([])
const loading = ref(false)
const refreshing = ref(false)

async function load() {
  loading.value = true
  try {
    const res: any = await inspectionApi.history({ page: 1, page_size: 50 })
    list.value = res?.items || res?.list || (Array.isArray(res) ? res : [])
  } catch {
    list.value = []
  } finally {
    loading.value = false
    refreshing.value = false
  }
}

function onRefresh() {
  refreshing.value = true
  load()
}

function goDetail(id: number) {
  router.push(`/inspection/${id}`)
}

function goStart() {
  hapticLight()
  router.push('/inspection/start')
}

onMounted(load)
</script>

<template>
  <div class="page">
    <van-pull-refresh v-model="refreshing" @refresh="onRefresh">
      <div v-if="loading && !list.length" style="padding: 16px">
        <van-skeleton title :row="3" />
      </div>
      <van-empty v-else-if="!list.length" description="暂无巡检记录">
        <van-button type="primary" size="small" @click="goStart">启动巡检</van-button>
      </van-empty>
      <van-cell-group inset style="margin-top: 8px" v-else>
        <van-cell
          v-for="item in list"
          :key="item.id"
          is-link
          @click="goDetail(item.id)"
        >
          <template #title>
            <div class="item-title van-ellipsis">
              {{ item.project_name || item.title || 'AI 巡检会话' }}
            </div>
            <div class="item-meta">
              <van-tag :type="(INSPECTION_STATUS_MAP[item.status]?.type as any) || 'default'">
                {{ getStatusLabel(INSPECTION_STATUS_MAP, item.status) }}
              </van-tag>
              <span class="item-stat">采集 {{ item.total_captures ?? item.capture_count ?? 0 }}</span>
              <span class="item-stat text-danger">隐患 {{ item.total_hazards ?? item.hazard_count ?? 0 }}</span>
            </div>
          </template>
          <template #value>
            <div class="item-time">{{ formatDateTime(item.started_at || item.created_at) }}</div>
          </template>
        </van-cell>
      </van-cell-group>
    </van-pull-refresh>

    <van-floating-bubble icon="play-circle-o" @click="goStart" />
  </div>
</template>

<style scoped>
.item-title {
  font-size: 14px;
  font-weight: 500;
  color: #323233;
}
.item-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 4px;
  font-size: 12px;
  color: #969799;
}
.item-stat {
  font-size: 11px;
}
.item-time {
  font-size: 11px;
  color: #969799;
  text-align: right;
}
.text-danger {
  color: #ee0a24;
}
</style>
