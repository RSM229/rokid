<script setup lang="ts">
/**
 * 创建日期：2026-07-18
 * 文书详情：展示文书内容，提供审批/驳回（总监）/提交操作
 */
import { computed, ref, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { showConfirmDialog, showSuccessToast, showFailToast } from 'vant'
import { documentApi } from '@/api'
import { useAuthStore } from '@/stores/auth'
import { formatDateTime, getStatusLabel, DOCUMENT_STATUS_MAP } from '@/utils/format'
import { isChief } from '@/utils/permission'
import { hapticLight } from '@/utils/haptic'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()

const doc = ref<any>(null)
const loading = ref(false)

const id = Number(route.params.id)
const contentText = computed(() => {
  const content = doc.value?.content ?? doc.value?.body
  if (!content) return '（无内容）'
  if (typeof content === 'string') return content
  if (content.summary) return content.summary
  return JSON.stringify(content, null, 2)
})

async function load() {
  loading.value = true
  try {
    doc.value = await documentApi.detail(id)
  } catch (e: any) {
    showFailToast(e?.response?.data?.detail || '加载失败')
  } finally {
    loading.value = false
  }
}

async function onSubmit() {
  hapticLight()
  await showConfirmDialog({ title: '提交审批', message: '提交后将进入审批流程，是否继续？' })
  try {
    await documentApi.submit(id)
    showSuccessToast('已提交')
    load()
  } catch (e: any) {
    showFailToast(e?.response?.data?.detail || '提交失败')
  }
}

async function onApprove() {
  await showConfirmDialog({ title: '审批通过', message: '确认审批通过该文书？' })
  try {
    await documentApi.approve(id)
    showSuccessToast('已审批通过')
    load()
  } catch (e: any) {
    showFailToast(e?.response?.data?.detail || '操作失败')
  }
}

async function onReject() {
  const reason = window.prompt('请输入驳回原因')
  if (reason === null) return
  const normalizedReason = reason.trim()
  if (!normalizedReason) {
    showFailToast('请填写驳回原因')
    return
  }
  try {
    await documentApi.reject(id, normalizedReason)
    showSuccessToast('已驳回')
    load()
  } catch (e: any) {
    showFailToast(e?.response?.data?.detail || '操作失败')
  }
}

const canApprove = isChief()

onMounted(load)
</script>

<template>
  <div class="page">
    <van-skeleton title :row="6" :loading="loading" v-if="loading" />
    <template v-else-if="doc">
      <van-cell-group inset>
        <van-cell :title="doc.title || doc.doc_type" />
        <van-cell title="状态">
          <van-tag :type="(DOCUMENT_STATUS_MAP[doc.status]?.type as any) || 'default'">
            {{ getStatusLabel(DOCUMENT_STATUS_MAP, doc.status) }}
          </van-tag>
        </van-cell>
        <van-cell title="类型" :value="doc.doc_type" />
        <van-cell title="创建人" :value="doc.creator_name || doc.author" />
        <van-cell title="创建时间" :value="formatDateTime(doc.created_at)" />
      </van-cell-group>

      <van-cell-group inset title="文书内容" style="margin-top: 12px">
        <div class="doc-content">{{ contentText }}</div>
      </van-cell-group>

      <div class="actions">
        <van-button v-if="doc.status === 'draft'" block type="primary" @click="onSubmit">提交审批</van-button>
        <template v-if="doc.status === 'submitted' && canApprove">
          <van-button block type="success" @click="onApprove">通过</van-button>
          <van-button block type="danger" plain style="margin-left: 12px" @click="onReject">驳回</van-button>
        </template>
      </div>
    </template>
    <van-empty v-else description="文书不存在或加载失败" />
  </div>
</template>

<style scoped>
.doc-content {
  padding: 12px 16px;
  font-size: 14px;
  line-height: 1.7;
  white-space: pre-wrap;
  color: #323233;
}
.actions {
  display: flex;
  padding: 16px 12px;
}
</style>
