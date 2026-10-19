<script setup lang="ts">
/**
 * 创建日期：2026-07-18
 * 通知中心：AI 巡检提醒 / 派单通知列表，支持清空
 */
import { ref, onMounted } from 'vue'
import { showConfirmDialog, showSuccessToast } from 'vant'
import { taskApi, inspectionApi } from '@/api'
import { formatFromNow } from '@/utils/format'
import { hapticLight } from '@/utils/haptic'

const list = ref<any[]>([])
const loading = ref(false)

async function load() {
  loading.value = true
  try {
    const res: any = await taskApi.notifications()
    list.value = res?.notifications || res?.items || []
  } catch {
    list.value = []
  } finally {
    loading.value = false
  }
}

function onClear() {
  hapticLight()
  showConfirmDialog({ title: '清空通知', message: '确定清空全部通知吗？' })
    .then(async () => {
      await inspectionApi.clearNotifications()
      list.value = []
      showSuccessToast('已清空')
    })
    .catch(() => {})
}

onMounted(load)
</script>

<template>
  <div class="page">
    <van-cell-group inset>
      <van-skeleton title :row="3" :loading="loading" v-if="loading && !list.length" />
      <van-empty v-else-if="!list.length" description="暂无通知" />
      <template v-else>
        <van-swipe-cell v-for="n in list" :key="n.id">
          <van-cell :title="n.title || n.message || '通知'" :label="formatFromNow(n.created_at || n.ts)">
            <template #icon>
              <van-tag v-if="!n.read" type="danger" style="margin-right: 6px">新</van-tag>
            </template>
            <template #value>
              <van-tag v-if="n.type" plain type="primary">{{ n.type }}</van-tag>
            </template>
          </van-cell>
        </van-swipe-cell>
      </template>
    </van-cell-group>

    <div v-if="list.length" class="clear-wrap">
      <van-button block round plain type="danger" @click="onClear">清空通知</van-button>
    </div>
  </div>
</template>

<style scoped>
.clear-wrap {
  padding: 16px;
}
</style>
