<script setup lang="ts">
/**
 * 创建日期：2026-07-18
 * 拍照取证：调用相机 + 自动水印（位置/时间/项目/人员）
 * 使用 @vueuse/core useGeolocation 获取定位
 */
import { ref, computed } from 'vue'
import { useRouter } from 'vue-router'
import { showSuccessToast, showFailToast } from 'vant'
import { useGeolocation } from '@vueuse/core'
import { useAuthStore } from '@/stores/auth'
import { workRecordApi } from '@/api'
import { formatDateTime } from '@/utils/format'
import { hapticSuccess, hapticError } from '@/utils/haptic'

const router = useRouter()
const auth = useAuthStore()
const { coords, locatedAt, error: locationError } = useGeolocation()
const located = computed(() =>
  !!locatedAt.value
  && Number.isFinite(coords.value?.longitude)
  && Number.isFinite(coords.value?.latitude),
)
const locationStatus = computed(() => {
  if (located.value) return '已定位'
  return locationError.value ? '定位失败' : '定位中'
})

function formatCoordinate(value: number | undefined): string {
  return Number.isFinite(value) ? Number(value).toFixed(6) : ''
}

const fileInput = ref<HTMLInputElement | null>(null)
const preview = ref<string>('')
const uploading = ref(false)
const remark = ref('')

const watermarkParams = computed(() => ({
  project: auth.user?.project_name || '',
  operator: auth.user?.name || '',
  longitude: formatCoordinate(coords.value?.longitude),
  latitude: formatCoordinate(coords.value?.latitude),
  timestamp: formatDateTime(new Date()),
}))

function pickImage() {
  fileInput.value?.click()
}

async function onFileChange(e: Event) {
  const input = e.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  preview.value = URL.createObjectURL(file)
  await uploadWithWatermark(file)
  input.value = ''
}

async function uploadWithWatermark(file: File) {
  uploading.value = true
  try {
    const locationStr = [watermarkParams.value.longitude, watermarkParams.value.latitude]
      .filter(Boolean)
      .join(',')
    // 单请求原子保存，保证离线重放时记录和照片仍能正确关联。
    const record: any = await workRecordApi.createInspectionWithPhoto(file, {
      location: locationStr || undefined,
      project_name: watermarkParams.value.project,
      remark: remark.value,
    })
    if (record?.image_url) preview.value = record.image_url
    hapticSuccess()
    showSuccessToast(record?.queued ? '已离线保存，联网后自动同步' : '已保存')
  } catch (e: any) {
    hapticError()
    showFailToast(e?.response?.data?.detail || '上传失败')
  } finally {
    uploading.value = false
  }
}

function goList() {
  router.push('/images')
}
</script>

<template>
  <div class="page">
    <div class="capture-preview" @click="pickImage">
      <van-image v-if="preview" :src="preview" fit="cover" width="100%" height="240" radius="12" />
      <div v-else class="capture-placeholder">
        <van-icon name="photograph" size="48" color="#c8c9cc" />
        <p>点击拍照 / 选择图片</p>
      </div>
      <van-loading v-if="uploading" class="loading-mask" size="24px" vertical>上传中...</van-loading>
    </div>

    <input
      ref="fileInput"
      type="file"
      accept="image/*"
      capture="environment"
      style="display: none"
      @change="onFileChange"
    />

    <van-cell-group inset style="margin-top: 12px">
      <van-cell title="定位状态">
        <template #value>
          <van-tag :type="located ? 'success' : 'warning'">
            {{ locationStatus }}
          </van-tag>
        </template>
      </van-cell>
      <van-cell title="经度" :value="watermarkParams.longitude || '-'" />
      <van-cell title="纬度" :value="watermarkParams.latitude || '-'" />
      <van-cell title="时间" :value="watermarkParams.timestamp" />
      <van-field v-model="remark" label="备注" placeholder="选填，现场情况说明" type="textarea" rows="2" autosize />
    </van-cell-group>

    <div class="actions">
      <van-button block plain type="primary" @click="goList">查看记录</van-button>
      <van-button block type="primary" :loading="uploading" @click="pickImage" style="margin-left: 12px">
        重新拍照
      </van-button>
    </div>
  </div>
</template>

<style scoped>
.capture-preview {
  position: relative;
  margin: 0 12px;
  border-radius: 12px;
  overflow: hidden;
  background: #fff;
}
.capture-placeholder {
  height: 240px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 10px;
  color: #969799;
  font-size: 13px;
}
.loading-mask {
  position: absolute;
  inset: 0;
  background: rgba(0, 0, 0, 0.4);
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
}
.actions {
  display: flex;
  padding: 16px 12px;
}
</style>
