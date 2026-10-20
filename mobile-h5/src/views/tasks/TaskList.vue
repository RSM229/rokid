<script setup lang="ts">
/**
 * 创建日期：2026-07-19
 * 派单任务列表：待接单/进行中/已完成/我派发的四类 Tab
 * 下拉刷新，跳转详情
 */
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { taskApi } from '@/api'
import {
  formatDateTime,
  getStatusLabel,
  TASK_STATUS_MAP,
  TASK_PRIORITY_MAP,
} from '@/utils/format'
import { hapticLight } from '@/utils/haptic'

const router = useRouter()
const list = ref<any[]>([])
const loading = ref(false)
const refreshing = ref(false)
const activeTab = ref<string>('pending')

const tabs = [
  { label: '待接单', value: 'pending' },
  { label: '进行中', value: 'doing' },
  { label: '已完成', value: 'done' },
  { label: '我派发的', value: 'mine' },
]

async function load() {
  loading.value = true
  try {
    const params: any = { status: activeTab.value === 'mine' ? undefined : activeTab.value }
    if (activeTab.value === 'mine') params.mine = true
    const res: any = await taskApi.myTasks(params)
    list.value = res?.items || res?.tasks || res?.list || (Array.isArray(res) ? res : [])
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

function onTabChange() {
  hapticLight()
  load()
}

function goDetail(id: number) {
  router.push(`/tasks/${id}`)
}

onMounted(load)
</script>

<template>
  <div class="page">
    <van-tabs v-model:active="activeTab" @change="onTabChange" sticky offset-top="46">
      <van-tab v-for="t in tabs" :key="t.value" :name="t.value" :title="t.label" />
    </van-tabs>

    <van-pull-refresh v-model="refreshing" @refresh="onRefresh">
      <div v-if="loading && !list.length" style="padding: 16px">
        <van-skeleton title :row="3" />
      </div>
      <van-empty v-else-if="!list.length" description="暂无任务" />
      <van-cell-group inset style="margin-top: 8px" v-else>
        <van-cell
          v-for="item in list"
          :key="item.id"
          is-link
          @click="goDetail(item.id)"
        >
          <template #title>
            <div class="item-title van-ellipsis">{{ item.title || '任务' }}</div>
            <div class="item-meta">
              <van-tag :type="(TASK_PRIORITY_MAP[item.priority]?.type as any) || 'default'">
                {{ getStatusLabel(TASK_PRIORITY_MAP, item.priority) }}
              </van-tag>
              <span v-if="item.category" class="item-cat">{{ item.category }}</span>
            </div>
          </template>
          <template #value>
            <van-tag :type="(TASK_STATUS_MAP[item.status]?.type as any) || 'default'">
              {{ getStatusLabel(TASK_STATUS_MAP, item.status) }}
            </van-tag>
            <div class="item-deadline">截止：{{ formatDateTime(item.deadline) }}</div>
          </template>
        </van-cell>
      </van-cell-group>
    </van-pull-refresh>
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
  gap: 6px;
  margin-top: 4px;
  font-size: 12px;
  color: #969799;
}
.item-cat {
  max-width: 120px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.item-deadline {
  margin-top: 4px;
  font-size: 11px;
  color: #969799;
  text-align: right;
}
</style>
