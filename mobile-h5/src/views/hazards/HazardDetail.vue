<script setup lang="ts">
/**
 * 创建日期：2026-07-18
 * 隐患详情与复查：展示隐患信息、整改图片上传、复查通过/不通过
 */
import { ref, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { showConfirmDialog, showSuccessToast, showFailToast } from 'vant'
import { hazardApi } from '@/api'
import { formatDateTime, getStatusLabel, HAZARD_STATUS_MAP } from '@/utils/format'
import { hapticLight } from '@/utils/haptic'

const route = useRoute()
const router = useRouter()

const detail = ref<any>(null)
const loading = ref(false)
const rectifyFile = ref<HTMLInputElement | null>(null)
const remark = ref('')

const id = Number(route.params.id)

async function load() {
  loading.value = true
  try {
    detail.value = await hazardApi.detail(id)
  } catch (e: any) {
    showFailToast(e?.response?.data?.detail || '加载失败')
  } finally {
    loading.value = false
  }
}

function pickRectifyImage() {
  rectifyFile.value?.click()
}

async function onRectifyFile(e: Event) {
  const input = e.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  try {
    await hazardApi.uploadRectifyImage(id, file)
    showSuccessToast('已上传整改图片')
    load()
  } catch (err: any) {
    showFailToast(err?.response?.data?.detail || '上传失败')
  }
  input.value = ''
}

async function onRecheck(passed: boolean) {
  hapticLight()
  let r = ''
  if (!passed) {
    const res = window.prompt('请输入复查不通过说明')
    if (res === null) return
    r = res.trim()
    if (!r) {
      showFailToast('请填写复查说明')
      return
    }
  }
  try {
    await hazardApi.recheck(id, passed, r)
    showSuccessToast(passed ? '复查通过' : '已驳回')
    load()
  } catch (e: any) {
    showFailToast(e?.response?.data?.detail || '操作失败')
  }
}

onMounted(load)
</script>

<template>
  <div class="page">
    <van-skeleton title :row="6" :loading="loading" v-if="loading" />
    <template v-else-if="detail">
      <van-cell-group inset>
        <van-cell :title="detail.title || detail.description || '隐患'" />
        <van-cell title="状态">
          <van-tag :type="(HAZARD_STATUS_MAP[detail.status]?.type as any) || 'default'">
            {{ getStatusLabel(HAZARD_STATUS_MAP, detail.status) }}
          </van-tag>
        </van-cell>
        <van-cell title="发现时间" :value="formatDateTime(detail.created_at)" />
        <van-cell title="发现人" :value="detail.reporter || detail.creator_name" />
        <van-cell title="位置" :value="detail.location || '-'" />
      </van-cell-group>

      <van-cell-group inset title="隐患描述" style="margin-top: 12px">
        <div class="content">{{ detail.description || detail.remark || '（无描述）' }}</div>
      </van-cell-group>

      <van-cell-group inset title="现场图片" style="margin-top: 12px" v-if="detail.image_url || detail.photos?.length">
        <div class="img-row">
          <van-image v-if="detail.image_url" :src="detail.image_url" width="100" height="100" fit="cover" radius="8" />
          <van-image
            v-for="(p, i) in detail.photos || []"
            :key="i"
            :src="p.url || p"
            width="100"
            height="100"
            fit="cover"
            radius="8"
          />
        </div>
      </van-cell-group>

      <van-cell-group inset title="整改图片" style="margin-top: 12px">
        <div class="img-row">
          <van-image
            v-for="(p, i) in detail.rectify_images || []"
            :key="i"
            :src="p.url || p"
            width="100"
            height="100"
            fit="cover"
            radius="8"
          />
          <div class="upload-btn" @click="pickRectifyImage">
            <van-icon name="plus" size="24" color="#969799" />
          </div>
        </div>
        <input ref="rectifyFile" type="file" accept="image/*" capture="environment" style="display:none" @change="onRectifyFile" />
      </van-cell-group>

      <div class="actions" v-if="detail.status === 'rechecking'">
        <van-button block type="success" @click="onRecheck(true)">复查通过</van-button>
        <van-button block type="danger" plain style="margin-left: 12px" @click="onRecheck(false)">不通过</van-button>
      </div>
    </template>
    <van-empty v-else description="隐患不存在或加载失败" />
  </div>
</template>

<style scoped>
.content {
  padding: 12px 16px;
  font-size: 14px;
  line-height: 1.7;
  white-space: pre-wrap;
}
.img-row {
  display: flex;
  gap: 8px;
  padding: 12px 16px;
  overflow-x: auto;
}
.upload-btn {
  width: 100px;
  height: 100px;
  border-radius: 8px;
  background: #f7f8fa;
  border: 1px dashed #dcdee0;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}
.actions {
  display: flex;
  padding: 16px 12px;
}
</style>
