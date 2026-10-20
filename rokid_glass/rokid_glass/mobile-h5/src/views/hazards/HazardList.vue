<script setup lang="ts">
/**
 * 创建日期：2026-07-18
 * 隐患整改列表：按状态筛选，跳转详情复查
 */
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { hazardApi } from '@/api'
import { formatDateTime, getStatusLabel, HAZARD_STATUS_MAP } from '@/utils/format'

const router = useRouter()
const list = ref<any[]>([])
const loading = ref(false)
const refreshing = ref(false)
const finished = ref(false)
const page = ref(1)
const activeStatus = ref<string>('')

const statusTabs = [
  { label: '全部', value: '' },
  { label: '待整改', value: 'open' },
  { label: '整改中', value: 'rectifying' },
  { label: '待复查', value: 'rechecking' },
  { label: '已闭环', value: 'closed' },
]

async function load(reset = false) {
  if (reset) {
    page.value = 1
    list.value = []
    finished.value = false
  }
  loading.value = true
  try {
    const res: any = await hazardApi.list({ page: page.value, page_size: 20, status: activeStatus.value || undefined })
    const items = res?.items || res?.list || res || []
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
  load(true)
}

function goDetail(id: number) {
  router.push(`/hazards/${id}`)
}

onMounted(() => load(true))
</script>

<template>
  <div class="page">
    <van-tabs v-model:active="activeStatus" @change="onTabChange" sticky offset-top="46">
      <van-tab v-for="t in statusTabs" :key="t.value" :name="t.value" :title="t.label" />
    </van-tabs>

    <van-pull-refresh v-model="refreshing" @refresh="onRefresh">
      <van-list
        v-model:loading="loading"
        :finished="finished"
        finished-text="没有更多了"
        @load="() => load(false)"
      >
        <van-empty v-if="!list.length && !loading" description="暂无隐患" />
        <van-cell-group inset style="margin-top: 8px">
          <van-cell
            v-for="item in list"
            :key="item.id"
            :title="item.title || item.description || '隐患'"
            :label="formatDateTime(item.created_at)"
            is-link
            @click="goDetail(item.id)"
          >
            <template #value>
              <van-tag :type="(HAZARD_STATUS_MAP[item.status]?.type as any) || 'default'">
                {{ getStatusLabel(HAZARD_STATUS_MAP, item.status) }}
              </van-tag>
            </template>
          </van-cell>
        </van-cell-group>
      </van-list>
    </van-pull-refresh>
  </div>
</template>
