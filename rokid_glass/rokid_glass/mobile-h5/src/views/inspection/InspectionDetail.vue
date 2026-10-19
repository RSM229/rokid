<script setup lang="ts">
/**
 * 创建日期：2026-07-19
 * 巡检会话详情：状态、频率、起止时间、统计 + 采集记录列表
 * 点击缩略图查看大图，进行中可停止
 */
import { ref, computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { showConfirmDialog, showSuccessToast, showFailToast } from 'vant'
import { inspectionApi } from '@/api'
import {
  formatDateTime,
  getStatusLabel,
  INSPECTION_STATUS_MAP,
} from '@/utils/format'
import { hapticLight } from '@/utils/haptic'

const route = useRoute()
const router = useRouter()

const detail = ref<any>(null)
const loading = ref(false)
const previewVisible = ref(false)
const previewIndex = ref(0)

const id = Number(route.params.id)

const captures = computed<any[]>(() => detail.value?.captures || detail.value?.records || [])
const isRunning = computed(() => ['active', 'running'].includes(detail.value?.status))

async function load() {
  loading.value = true
  try {
    detail.value = await inspectionApi.detail(id)
  } catch (e: any) {
    showFailToast(e?.response?.data?.detail || '加载失败')
  } finally {
    loading.value = false
  }
}

async function onStop() {
  hapticLight()
  await showConfirmDialog({ title: '停止巡检', message: '确认停止当前 AI 巡检会话？' })
  try {
    await inspectionApi.stop(id)
    showSuccessToast('已停止')
    load()
  } catch (e: any) {
    showFailToast(e?.response?.data?.detail || '停止失败')
  }
}

function previewCapture(index: number) {
  previewIndex.value = index
  previewVisible.value = true
}

function riskTagType(level: string): string {
  if (level === 'high' || level === 'danger') return 'danger'
  if (level === 'medium' || level === 'warning') return 'warning'
  return 'success'
}

function riskLabel(item: any): string {
  const level = item.risk_level || item.level
  if (level === 'high' || level === 'danger') return '有隐患'
  if (level === 'medium' || level === 'warning') return '需关注'
  return '正常'
}

onMounted(load)
</script>

<template>
  <div class="page">
    <van-skeleton title :row="6" :loading="loading" v-if="loading" />
    <template v-else-if="detail">
      <!-- 会话基本信息 -->
      <van-cell-group inset>
        <van-cell title="巡检会话">
          <van-tag :type="(INSPECTION_STATUS_MAP[detail.status]?.type as any) || 'default'">
            {{ getStatusLabel(INSPECTION_STATUS_MAP, detail.status) }}
          </van-tag>
        </van-cell>
        <van-cell title="所属项目" :value="detail.project_name || '-'" />
        <van-cell
          title="采集频率"
          :value="(detail.frequency_seconds ?? detail.frequency) ? `${detail.frequency_seconds ?? detail.frequency} 秒/次` : '-'"
        />
        <van-cell title="开始时间" :value="formatDateTime(detail.started_at || detail.created_at)" />
        <van-cell title="结束时间" :value="formatDateTime(detail.ended_at || detail.stopped_at)" />
      </van-cell-group>

      <!-- 统计 -->
      <div class="stat-row">
        <div class="stat-box">
          <div class="stat-num">{{ detail.total_captures ?? detail.capture_count ?? captures.length ?? 0 }}</div>
          <div class="stat-label">采集数</div>
        </div>
        <div class="stat-box">
          <div class="stat-num text-danger">{{ detail.total_hazards ?? detail.hazard_count ?? 0 }}</div>
          <div class="stat-label">隐患数</div>
        </div>
        <div class="stat-box">
          <div class="stat-num text-success">{{ detail.normal_count || 0 }}</div>
          <div class="stat-label">正常</div>
        </div>
      </div>

      <!-- 采集记录 -->
      <div class="section-title">采集记录</div>
      <van-empty v-if="!captures.length" description="暂无采集记录" />
      <div v-else class="capture-grid">
        <div
          v-for="(c, i) in captures"
          :key="i"
          class="capture-card"
          @click="previewCapture(i)"
        >
          <van-image
            :src="c.image_url || c.thumbnail || c.url"
            width="100%"
            height="100"
            fit="cover"
            radius="6"
          >
            <template #error>
              <div class="img-fallback"><van-icon name="photo-fail" /></div>
            </template>
          </van-image>
          <div class="capture-meta">
            <van-tag :type="riskTagType(c.risk_level || c.level) as any">
              {{ riskLabel(c) }}
            </van-tag>
            <span class="capture-time">{{ formatDateTime(c.captured_at || c.created_at) }}</span>
          </div>
          <div v-if="c.result || c.hazards?.length" class="capture-result van-ellipsis">
            {{ c.result || (c.hazards || []).map((h: any) => h.name || h.type).join('、') }}
          </div>
        </div>
      </div>

      <!-- 停止按钮 -->
      <div class="actions" v-if="isRunning">
        <van-button block type="danger" plain @click="onStop">停止巡检</van-button>
      </div>

      <!-- 大图预览 -->
      <van-image-preview
        v-model:show="previewVisible"
        :images="captures.map((c) => c.image_url || c.thumbnail || c.url)"
        :start-position="previewIndex"
      />
    </template>
    <van-empty v-else description="巡检会话不存在或加载失败" />
  </div>
</template>

<style scoped>
.stat-row {
  display: flex;
  gap: 8px;
  padding: 0 12px;
  margin-top: 12px;
}
.stat-box {
  flex: 1;
  background: #fff;
  border-radius: 10px;
  padding: 12px;
  text-align: center;
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.04);
}
.stat-num {
  font-size: 22px;
  font-weight: 700;
  color: #1989fa;
}
.stat-label {
  margin-top: 4px;
  font-size: 12px;
  color: #969799;
}
.text-danger {
  color: #ee0a24;
}
.text-success {
  color: #07c160;
}
.section-title {
  margin: 18px 16px 8px;
  font-size: 14px;
  font-weight: 600;
}
.capture-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 10px;
  padding: 0 12px;
}
.capture-card {
  background: #fff;
  border-radius: 10px;
  overflow: hidden;
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.04);
}
.img-fallback {
  width: 100%;
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  background: #f7f8fa;
  color: #dcdee0;
  font-size: 28px;
}
.capture-meta {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 6px 8px 2px;
}
.capture-time {
  font-size: 10px;
  color: #969799;
}
.capture-result {
  padding: 0 8px 8px;
  font-size: 11px;
  color: #646566;
}
.actions {
  padding: 24px 16px;
}
</style>
