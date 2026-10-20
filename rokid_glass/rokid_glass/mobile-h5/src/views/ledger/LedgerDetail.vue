<script setup lang="ts">
/**
 * 创建日期：2026-07-19
 * 台账详情：单条台账详情 + 关联照片/文书跳转
 */
import { ref, computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { showFailToast } from 'vant'
import { ledgerApi } from '@/api'
import {
  formatDateTime,
  LEDGER_TYPE_MAP,
} from '@/utils/format'

const route = useRoute()
const router = useRouter()

const detail = ref<any>(null)
const loading = ref(false)

const id = Number(route.params.id)
const type = route.params.type as string

const typeLabel = computed(
  () => LEDGER_TYPE_MAP[type]?.label || '台账',
)

const photos = computed<any[]>(() => detail.value?.photos || detail.value?.images || [])
const relatedDocs = computed<any[]>((() => detail.value?.related_documents || detail.value?.documents || [])())

async function load() {
  loading.value = true
  try {
    detail.value = await ledgerApi.detail(type, id)
  } catch (e: any) {
    showFailToast(e?.response?.data?.detail || '加载失败')
  } finally {
    loading.value = false
  }
}

function goDocument(docId: number) {
  router.push(`/documents/${docId}`)
}

function goHazard(hazardId: number) {
  router.push(`/hazards/${hazardId}`)
}

onMounted(load)
</script>

<template>
  <div class="page">
    <van-skeleton title :row="6" :loading="loading" v-if="loading" />
    <template v-else-if="detail">
      <van-cell-group inset>
        <van-cell :title="detail.title || detail.name || `${typeLabel}记录`" />
        <van-cell title="台账类型">
          <van-tag :type="(LEDGER_TYPE_MAP[type]?.type as any) || 'default'">
            {{ typeLabel }}
          </van-tag>
        </van-cell>
        <van-cell v-if="detail.doc_no" title="文书编号" :value="detail.doc_no" />
        <van-cell v-if="detail.area || detail.construction_area" title="施工区域" :value="detail.area || detail.construction_area" />
        <van-cell v-if="detail.location" title="位置" :value="detail.location" />
        <van-cell v-if="detail.creator_name" title="创建人" :value="detail.creator_name" />
        <van-cell title="时间" :value="formatDateTime(detail.created_at || detail.date)" />
      </van-cell-group>

      <!-- 详情内容 -->
      <van-cell-group v-if="detail.content || detail.description" inset style="margin-top: 12px" title="内容详情">
        <div class="content">{{ detail.content || detail.description }}</div>
      </van-cell-group>

      <!-- 关联照片 -->
      <van-cell-group v-if="photos.length" inset style="margin-top: 12px" title="关联照片">
        <div class="photo-row">
          <van-image
            v-for="(p, i) in photos"
            :key="i"
            :src="p.url || p"
            width="100"
            height="100"
            fit="cover"
            radius="8"
          />
        </div>
      </van-cell-group>

      <!-- 关联文书 -->
      <van-cell-group v-if="relatedDocs.length" inset style="margin-top: 12px" title="关联文书">
        <van-cell
          v-for="(d, i) in relatedDocs"
          :key="i"
          :title="d.title || d.doc_type || `文书 #${d.id}`"
          is-link
          @click="goDocument(d.id)"
        >
          <template #icon>
            <van-icon name="description" style="margin-right: 6px" />
          </template>
        </van-cell>
      </van-cell-group>

      <!-- 关联隐患 -->
      <van-cell-group v-if="detail.hazards?.length" inset style="margin-top: 12px" title="关联隐患">
        <van-cell
          v-for="(h, i) in detail.hazards"
          :key="i"
          :title="h.title || h.description || `隐患 #${h.id}`"
          is-link
          @click="goHazard(h.id)"
        >
          <template #icon>
            <van-icon name="warning-o" style="margin-right: 6px" color="#ee0a24" />
          </template>
        </van-cell>
      </van-cell-group>
    </template>
    <van-empty v-else description="台账不存在或加载失败" />
  </div>
</template>

<style scoped>
.content {
  padding: 12px 16px;
  font-size: 14px;
  line-height: 1.7;
  white-space: pre-wrap;
  color: #323233;
}
.photo-row {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  padding: 12px 16px;
}
</style>
