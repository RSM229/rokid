<script setup lang="ts">
/**
 * 创建日期：2026-07-19
 * 工作记录列表：巡视/验收/旁站/见证四类 Tab 切换
 * 下拉刷新 + 上拉加载，右下角浮动按钮新建
 */
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { workRecordApi } from '@/api'
import {
  formatDateTime,
  getStatusLabel,
  WORK_RECORD_STATUS_MAP,
  WORK_RECORD_TYPE_MAP,
} from '@/utils/format'
import { hapticLight } from '@/utils/haptic'

const router = useRouter()
const list = ref<any[]>([])
const loading = ref(false)
const refreshing = ref(false)
const finished = ref(false)
const page = ref(1)
const activeType = ref<string>('inspection')

const typeTabs = [
  { label: '巡视', value: 'inspection' },
  { label: '验收', value: 'acceptance' },
  { label: '旁站', value: 'station' },
  { label: '见证', value: 'witness' },
]

async function load(reset = false) {
  if (reset) {
    page.value = 1
    list.value = []
    finished.value = false
  }
  loading.value = true
  try {
    const res: any = await workRecordApi.list({
      page: page.value,
      page_size: 20,
      type: activeType.value,
    })
    const items = res?.items || res?.records || res?.list || res || []
    list.value.push(...(Array.isArray(items) ? items : []))
    if (items.length < 20) finished.value = true
    else page.value++
  } catch {
    finished.value = true
  } finally {
    loading.value = false
    refreshing.value = false
  }
}

function onRefresh() {
  refreshing.value = true
  load(true)
}

function onTabChange() {
  hapticLight()
  load(true)
}

function goDetail(id: number) {
  router.push(`/work-records/${id}`)
}

function goCreate() {
  hapticLight()
  router.push({ path: '/work-records/create', query: { type: activeType.value } })
}

onMounted(() => load(true))
</script>

<template>
  <div class="page">
    <van-tabs v-model:active="activeType" @change="onTabChange" sticky offset-top="46">
      <van-tab v-for="t in typeTabs" :key="t.value" :name="t.value" :title="t.label" />
    </van-tabs>

    <van-pull-refresh v-model="refreshing" @refresh="onRefresh">
      <van-list
        v-model:loading="loading"
        :finished="finished"
        finished-text="没有更多了"
        @load="() => load(false)"
      >
        <van-empty v-if="!list.length && !loading" description="暂无工作记录">
          <van-button type="primary" size="small" @click="goCreate">新建记录</van-button>
        </van-empty>
        <van-cell-group inset style="margin-top: 8px">
          <van-cell
            v-for="item in list"
            :key="item.id"
            is-link
            @click="goDetail(item.id)"
          >
            <template #title>
              <div class="item-title van-ellipsis">
                {{ item.title || item.location || WORK_RECORD_TYPE_MAP[activeType]?.label + '记录' }}
              </div>
              <div class="item-meta">
                <van-tag plain :type="(WORK_RECORD_TYPE_MAP[item.type || activeType]?.type as any) || 'default'">
                  {{ WORK_RECORD_TYPE_MAP[item.type || activeType]?.label || item.type }}
                </van-tag>
                <span class="item-area">{{ item.area || item.construction_area || item.section || '-' }}</span>
              </div>
            </template>
            <template #value>
              <van-tag :type="(WORK_RECORD_STATUS_MAP[item.status]?.type as any) || 'default'">
                {{ getStatusLabel(WORK_RECORD_STATUS_MAP, item.status) }}
              </van-tag>
              <div class="item-time">{{ formatDateTime(item.created_at) }}</div>
            </template>
          </van-cell>
        </van-cell-group>
      </van-list>
    </van-pull-refresh>

    <van-floating-bubble icon="plus" @click="goCreate" />
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
.item-area {
  max-width: 120px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.item-time {
  margin-top: 4px;
  font-size: 11px;
  color: #969799;
  text-align: right;
}
</style>
