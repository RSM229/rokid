<script setup lang="ts">
/**
 * 创建日期：2026-07-19
 * 启动 AI 巡检：频率选择（5s/10s/30s）+ 调用 inspectionApi.start
 * 启动成功跳转会话详情，支持停止
 */
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { showSuccessToast, showFailToast, showConfirmDialog } from 'vant'
import { inspectionApi } from '@/api'
import { useAuthStore } from '@/stores/auth'
import { hapticLight } from '@/utils/haptic'

const router = useRouter()
const auth = useAuthStore()

const frequencyOptions = [
  { text: '5 秒 / 次（高频）', value: 5 },
  { text: '10 秒 / 次（标准）', value: 10 },
  { text: '30 秒 / 次（低频）', value: 30 },
]
const showFreqPicker = ref(false)
const frequency = ref<number>(10)
const freqLabel = ref<string>('10 秒 / 次（标准）')
const starting = ref(false)

function onFreqConfirm({ selectedOptions }: { selectedOptions: any[] }) {
  const sel = selectedOptions[0]
  if (sel) {
    frequency.value = sel.value
    freqLabel.value = sel.text
  }
  showFreqPicker.value = false
}

async function onStart() {
  hapticLight()
  await showConfirmDialog({
    title: '启动 AI 巡检',
    message: `将以 ${frequency.value} 秒/次 频率启动 AI 安全巡检，确认开始？`,
  })
  starting.value = true
  try {
    const res: any = await inspectionApi.start({
      frequency: frequency.value,
      project_id: auth.user?.project_id,
    })
    showSuccessToast('巡检已启动')
    const sessionId = res?.id || res?.session_id
    if (sessionId) {
      router.replace(`/inspection/${sessionId}`)
    } else {
      router.replace('/inspection')
    }
  } catch (e: any) {
    showFailToast(e?.response?.data?.detail || '启动失败')
  } finally {
    starting.value = false
  }
}
</script>

<template>
  <div class="page">
    <div class="hero">
      <van-icon name="shield-o" size="56" color="#1989fa" />
      <div class="hero-title">AI 安全隐患智能巡检</div>
      <div class="hero-desc">云端大模型实时识别工地安全隐患</div>
    </div>

    <van-cell-group inset>
      <van-field
        :model-value="freqLabel"
        label="采集频率"
        is-link
        readonly
        placeholder="选择采集频率"
        @click="showFreqPicker = true"
      />
      <van-cell title="当前项目" :value="auth.user?.project_name || '未绑定'" />
    </van-cell-group>

    <div class="tips">
      <van-icon name="info-o" />
      <span>启动后将按设定频率自动采集现场画面并上传云端识别，隐患结果会实时推送到通知中心。</span>
    </div>

    <div class="actions">
      <van-button block type="primary" size="large" :loading="starting" @click="onStart">
        启动巡检
      </van-button>
    </div>

    <van-popup v-model:show="showFreqPicker" position="bottom" round>
      <van-picker
        :columns="frequencyOptions"
        @confirm="onFreqConfirm"
        @cancel="showFreqPicker = false"
      />
    </van-popup>
  </div>
</template>

<style scoped>
.hero {
  text-align: center;
  padding: 32px 16px 24px;
}
.hero-title {
  margin-top: 12px;
  font-size: 18px;
  font-weight: 600;
  color: #323233;
}
.hero-desc {
  margin-top: 6px;
  font-size: 13px;
  color: #969799;
}
.tips {
  margin: 12px 16px;
  padding: 10px 12px;
  background: #ebf2ff;
  border-radius: 8px;
  font-size: 12px;
  color: #1989fa;
  display: flex;
  align-items: flex-start;
  gap: 6px;
  line-height: 1.6;
}
.actions {
  padding: 24px 16px;
}
</style>
