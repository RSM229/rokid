<script setup lang="ts">
/**
 * 创建日期：2026-07-19
 * 工作记录详情：展示记录信息 + 状态流转（提交/审批/驳回/归档）+ 关联照片
 */
import { ref, computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { showConfirmDialog, showSuccessToast, showFailToast } from 'vant'
import { workRecordApi } from '@/api'
import { useAuthStore } from '@/stores/auth'
import {
  formatDateTime,
  getStatusLabel,
  WORK_RECORD_STATUS_MAP,
  WORK_RECORD_TYPE_MAP,
} from '@/utils/format'
import { isChiefOrDeputy, isChief } from '@/utils/permission'
import { hapticLight } from '@/utils/haptic'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()

const detail = ref<any>(null)
const loading = ref(false)
const previewVisible = ref(false)
const previewIndex = ref(0)

const id = Number(route.params.id)

const typeLabel = computed(
  () => WORK_RECORD_TYPE_MAP[detail.value?.type]?.label || detail.value?.type || '记录',
)

const canApprove = computed(() => isChiefOrDeputy())
const canArchive = computed(() => isChief())

const photos = computed<any[]>(() => detail.value?.photos || detail.value?.images || [])

async function load() {
  loading.value = true
  try {
    detail.value = await workRecordApi.detail(id)
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
    await workRecordApi.submit(id)
    showSuccessToast('已提交')
    load()
  } catch (e: any) {
    showFailToast(e?.response?.data?.detail || '提交失败')
  }
}

async function onApprove() {
  await showConfirmDialog({ title: '审批通过', message: '确认审批通过该工作记录？' })
  try {
    await workRecordApi.approve(id)
    showSuccessToast('已审批通过')
    load()
  } catch (e: any) {
    showFailToast(e?.response?.data?.detail || '操作失败')
  }
}

async function onReject() {
  const reason = window.prompt('请输入驳回原因')
  if (reason === null) return
  const r = reason.trim()
  if (!r) {
    showFailToast('请填写驳回原因')
    return
  }
  try {
    await workRecordApi.reject(id, r)
    showSuccessToast('已驳回')
    load()
  } catch (e: any) {
    showFailToast(e?.response?.data?.detail || '操作失败')
  }
}

async function onArchive() {
  hapticLight()
  await showConfirmDialog({ title: '归档', message: '确认归档该工作记录？归档后不可修改。' })
  try {
    await workRecordApi.archive(id)
    showSuccessToast('已归档')
    load()
  } catch (e: any) {
    showFailToast(e?.response?.data?.detail || '归档失败')
  }
}

function previewPhoto(index: number) {
  previewIndex.value = index
  previewVisible.value = true
}

onMounted(load)
</script>

<template>
  <div class="page">
    <van-skeleton title :row="6" :loading="loading" v-if="loading" />
    <template v-else-if="detail">
      <!-- 基本信息 -->
      <van-cell-group inset>
        <van-cell :title="detail.title || typeLabel + '记录'" />
        <van-cell title="记录类型">
          <van-tag :type="(WORK_RECORD_TYPE_MAP[detail.type]?.type as any) || 'default'">
            {{ typeLabel }}
          </van-tag>
        </van-cell>
        <van-cell title="状态">
          <van-tag :type="(WORK_RECORD_STATUS_MAP[detail.status]?.type as any) || 'default'">
            {{ getStatusLabel(WORK_RECORD_STATUS_MAP, detail.status) }}
          </van-tag>
        </van-cell>
        <van-cell title="创建人" :value="detail.creator_name || detail.author || '-'" />
        <van-cell title="创建时间" :value="formatDateTime(detail.created_at)" />
      </van-cell-group>

      <!-- 动态字段（巡视） -->
      <van-cell-group v-if="detail.type === 'inspection'" inset style="margin-top: 12px" title="巡视信息">
        <van-cell title="施工区域" :value="detail.construction_area || '-'" />
        <van-cell title="位置" :value="detail.location || '-'" />
        <van-cell title="施工内容" :value="detail.construction_content || '-'" />
        <van-cell title="天气" :value="detail.weather || '-'" />
        <van-cell v-if="detail.remark" title="备注" :value="detail.remark" />
      </van-cell-group>

      <!-- 动态字段（验收） -->
      <van-cell-group v-else-if="detail.type === 'acceptance'" inset style="margin-top: 12px" title="验收信息">
        <van-cell title="验收类型" :value="detail.acceptance_type || '-'" />
        <van-cell title="施工区域" :value="detail.construction_area || '-'" />
        <van-cell title="位置" :value="detail.location || '-'" />
        <van-cell title="验收内容" :value="detail.acceptance_content || '-'" />
        <van-cell title="验收结果">
          <van-tag v-if="detail.result" :type="detail.result === 'fail' ? 'danger' : detail.result === 'pending' ? 'warning' : 'success'">
            {{ detail.result === 'pass' ? '合格' : detail.result === 'fail' ? '不合格' : '待整改' }}
          </van-tag>
          <span v-else>-</span>
        </van-cell>
      </van-cell-group>

      <!-- 动态字段（旁站） -->
      <van-cell-group v-else-if="detail.type === 'station'" inset style="margin-top: 12px" title="旁站信息">
        <van-cell title="旁站工程" :value="detail.project_name || '-'" />
        <van-cell title="旁站部位" :value="detail.position || '-'" />
        <van-cell title="开始时间" :value="formatDateTime(detail.start_time)" />
        <van-cell title="结束时间" :value="formatDateTime(detail.end_time)" />
        <van-cell title="情况描述" :value="detail.description || '-'" />
        <van-cell title="旁站结论" :value="detail.conclusion || '-'" />
      </van-cell-group>

      <!-- 动态字段（见证） -->
      <van-cell-group v-else inset style="margin-top: 12px" title="见证信息">
        <van-cell title="材料名称" :value="detail.material_name || '-'" />
        <van-cell title="规格型号" :value="detail.specification || '-'" />
        <van-cell title="施工区域" :value="detail.construction_area || '-'" />
        <van-cell title="位置" :value="detail.location || '-'" />
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
            @click="previewPhoto(i)"
          />
        </div>
      </van-cell-group>

      <!-- 状态流转操作 -->
      <div class="actions">
        <van-button v-if="detail.status === 'draft' || detail.status === 'rejected'" block type="primary" @click="onSubmit">
          提交审批
        </van-button>
        <template v-if="detail.status === 'submitted' && canApprove">
          <van-button block type="success" @click="onApprove">审批通过</van-button>
          <van-button block type="danger" plain style="margin-left: 12px" @click="onReject">驳回</van-button>
        </template>
        <van-button
          v-if="detail.status === 'approved' && canArchive"
          block
          type="primary"
          plain
          @click="onArchive"
        >
          归档
        </van-button>
      </div>

      <!-- 照片预览 -->
      <van-image-preview
        v-model:show="previewVisible"
        :images="photos.map((p) => p.url || p)"
        :start-position="previewIndex"
      />
    </template>
    <van-empty v-else description="记录不存在或加载失败" />
  </div>
</template>

<style scoped>
.photo-row {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  padding: 12px 16px;
}
.actions {
  display: flex;
  padding: 16px 12px;
}
</style>
