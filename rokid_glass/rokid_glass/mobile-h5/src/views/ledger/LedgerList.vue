<script setup lang="ts">
/**
 * 创建日期：2026-07-19
 * 台账列表：8 类台账 Tab 切换 + 时间范围筛选 + 下拉刷新/上拉加载
 */
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { ledgerApi } from '@/api'
import {
  formatDateTime,
  formatDate,
  LEDGER_TYPE_MAP,
} from '@/utils/format'
import { hapticLight } from '@/utils/haptic'

const router = useRouter()
const list = ref<any[]>([])
const loading = ref(false)
const refreshing = ref(false)
const finished = ref(false)
const page = ref(1)
const activeType = ref<string>('image')

const typeTabs = Object.entries(LEDGER_TYPE_MAP).map(([value, info]) => ({
  label: info.label,
  value,
}))

// 时间范围
const showDateStart = ref(false)
const showDateEnd = ref(false)
const startDate = ref('')
const endDate = ref('')
const tempDate = ref('')

const dateRangeLabel = computed(() => {
  if (!startDate.value && !endDate.value) return '全部时间'
  return `${startDate.value || '开始'} ~ ${endDate.value || '至今'}`
})

async function load(reset = false) {
  if (reset) {
    page.value = 1
    list.value = []
    finished.value = false
  }
  loading.value = true
  try {
    const res: any = await ledgerApi.list({
      page: page.value,
      page_size: 20,
      ledger_type: activeType.value,
      start_date: startDate.value || undefined,
      end_date: endDate.value || undefined,
    })
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
  hapticLight()
  load(true)
}

function onDateConfirm(date: Date) {
  tempDate.value = formatDate(date)
}

function confirmStartDate(date: Date) {
  startDate.value = formatDate(date)
  showDateStart.value = false
  load(true)
}

function confirmEndDate(date: Date) {
  endDate.value = formatDate(date)
  showDateEnd.value = false
  load(true)
}

function clearDateRange() {
  startDate.value = ''
  endDate.value = ''
  load(true)
}

function goDetail(item: any) {
  router.push(`/ledger/${activeType.value}/${item.id}`)
}

onMounted(() => load(true))
</script>

<template>
  <div class="page">
    <van-tabs v-model:active="activeType" @change="onTabChange" sticky offset-top="46">
      <van-tab v-for="t in typeTabs" :key="t.value" :name="t.value" :title="t.label" />
    </van-tabs>

    <!-- 时间范围筛选 -->
    <div class="filter-bar">
      <van-cell
        :title="dateRangeLabel"
        is-link
        icon="calendar-o"
        @click="showDateStart = true"
      />
      <van-button v-if="startDate || endDate" size="small" plain type="danger" @click="clearDateRange">
        清除
      </van-button>
    </div>

    <van-pull-refresh v-model="refreshing" @refresh="onRefresh">
      <van-list
        v-model:loading="loading"
        :finished="finished"
        finished-text="没有更多了"
        @load="() => load(false)"
      >
        <van-empty v-if="!list.length && !loading" description="暂无台账数据" />
        <van-cell-group inset style="margin-top: 8px">
          <van-cell
            v-for="item in list"
            :key="item.id"
            is-link
            @click="goDetail(item)"
          >
            <template #title>
              <div class="item-title van-ellipsis">
                {{ item.title || item.name || item.doc_no || `台账记录 #${item.id}` }}
              </div>
              <div class="item-meta">
                <van-tag :type="(LEDGER_TYPE_MAP[activeType]?.type as any) || 'default'">
                  {{ LEDGER_TYPE_MAP[activeType]?.label }}
                </van-tag>
                <span v-if="item.area || item.construction_area" class="item-area">
                  {{ item.area || item.construction_area }}
                </span>
              </div>
            </template>
            <template #value>
              <div class="item-time">{{ formatDateTime(item.created_at || item.date) }}</div>
            </template>
          </van-cell>
        </van-cell-group>
      </van-list>
    </van-pull-refresh>

    <van-popup v-model:show="showDateStart" position="bottom" round>
      <van-date-picker
        title="开始日期"
        @confirm="confirmStartDate"
        @cancel="showDateStart = false"
      />
    </van-popup>
    <van-popup v-model:show="showDateEnd" position="bottom" round>
      <van-date-picker
        title="结束日期"
        @confirm="confirmEndDate"
        @cancel="showDateEnd = false"
      />
    </van-popup>
  </div>
</template>

<style scoped>
.filter-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 0 12px;
  margin-top: 8px;
}
.filter-bar :deep(.van-cell) {
  flex: 1;
  border-radius: 8px;
  background: #fff;
}
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
  font-size: 11px;
  color: #969799;
  text-align: right;
}
</style>
