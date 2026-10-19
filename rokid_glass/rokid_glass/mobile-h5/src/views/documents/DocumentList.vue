<script setup lang="ts">
/**
 * 创建日期：2026-07-18
 * 监理文书列表：下拉刷新 + 上拉加载，跳转详情 / 新建
 */
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { documentApi } from '@/api'
import { formatDateTime, getStatusLabel, DOCUMENT_STATUS_MAP } from '@/utils/format'

const router = useRouter()
const list = ref<any[]>([])
const loading = ref(false)
const refreshing = ref(false)
const finished = ref(false)
const page = ref(1)
const activeStatus = ref<string>('')

const statusTabs = [
  { label: '全部', value: '' },
  { label: '草稿', value: 'draft' },
  { label: '待审批', value: 'submitted' },
  { label: '已审批', value: 'approved' },
]

async function load(reset = false) {
  if (reset) {
    page.value = 1
    list.value = []
    finished.value = false
  }
  loading.value = true
  try {
    const res: any = await documentApi.list({ page: page.value, page_size: 20, status: activeStatus.value || undefined })
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
  router.push(`/documents/${id}`)
}

function goCreate() {
  router.push('/documents/create')
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
        <van-empty v-if="!list.length && !loading" description="暂无文书">
          <van-button type="primary" size="small" @click="goCreate">新建文书</van-button>
        </van-empty>
        <van-cell-group inset style="margin-top: 8px">
          <van-cell
            v-for="item in list"
            :key="item.id"
            :title="item.title || item.doc_type"
            :label="formatDateTime(item.created_at)"
            is-link
            @click="goDetail(item.id)"
          >
            <template #value>
              <van-tag :type="(DOCUMENT_STATUS_MAP[item.status]?.type as any) || 'default'">
                {{ getStatusLabel(DOCUMENT_STATUS_MAP, item.status) }}
              </van-tag>
            </template>
          </van-cell>
        </van-cell-group>
      </van-list>
    </van-pull-refresh>

    <van-floating-bubble icon="plus" @click="goCreate" />
  </div>
</template>
