<script setup lang="ts">
/**
 * 创建日期：2026-07-18
 * 影像采集列表：展示统一媒体服务中的现场拍照取证记录
 * 业务功能阶段 2 完善
 */
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { mediaApi } from '@/api'
import { formatDateTime } from '@/utils/format'

const router = useRouter()
const list = ref<any[]>([])
const loading = ref(false)
const refreshing = ref(false)
const finished = ref(false)
const page = ref(1)

async function load(reset = false) {
  if (reset) {
    page.value = 1
    list.value = []
    finished.value = false
  }
  loading.value = true
  try {
    const res: any = await mediaApi.list({ media_type: 'image', limit: 100 })
    const items = res?.items || []
    list.value.push(...(Array.isArray(items) ? items : []))
    finished.value = true
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

function goCapture() {
  router.push('/images/capture')
}

onMounted(() => load(true))
</script>

<template>
  <div class="page">
    <van-pull-refresh v-model="refreshing" @refresh="onRefresh">
      <van-list
        v-model:loading="loading"
        :finished="finished"
        finished-text="没有更多了"
        @load="() => load(false)"
      >
        <van-empty v-if="!list.length && !loading" description="暂无影像记录">
          <van-button type="primary" size="small" @click="goCapture">去拍照取证</van-button>
        </van-empty>
        <div class="img-grid">
          <div v-for="item in list" :key="item.id" class="img-card">
            <van-image
              width="100%"
              height="100"
              fit="cover"
              radius="8"
              :src="item.url || item.original_url || item.thumbnail"
            />
            <div class="img-meta">
              <div class="img-title van-ellipsis">
                {{ item.business_type === 'hazard' ? '隐患影像' : '现场取证' }}
              </div>
              <div class="img-time">{{ formatDateTime(item.created_at) }}</div>
            </div>
          </div>
        </div>
      </van-list>
    </van-pull-refresh>

    <van-floating-bubble icon="photograph" @click="goCapture" />
  </div>
</template>

<style scoped>
.img-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 10px;
  padding: 0 12px;
}
.img-card {
  background: #fff;
  border-radius: 10px;
  overflow: hidden;
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.04);
}
.img-meta {
  padding: 8px;
}
.img-title {
  font-size: 13px;
  font-weight: 500;
}
.img-time {
  margin-top: 4px;
  font-size: 11px;
  color: #969799;
}
</style>
