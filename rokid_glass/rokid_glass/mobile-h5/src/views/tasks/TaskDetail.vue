<script setup lang="ts">
/**
 * 创建日期：2026-07-19
 * 任务详情：展示任务信息 + 根据状态提供操作（接单/拒单/开始/完成）
 * 完成时支持输入回执 + 上传成果文件
 */
import { ref, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { showConfirmDialog, showSuccessToast, showFailToast } from 'vant'
import { taskApi } from '@/api'
import {
  formatDateTime,
  getStatusLabel,
  TASK_STATUS_MAP,
  TASK_PRIORITY_MAP,
} from '@/utils/format'
import { hapticLight } from '@/utils/haptic'

const route = useRoute()
const router = useRouter()

const detail = ref<any>(null)
const loading = ref(false)
const id = Number(route.params.id)

// 完成回执
const showCompletePopup = ref(false)
const receipt = ref('')
const resultFiles = ref<File[]>([])
const resultPreviews = ref<string[]>([])
const fileInput = ref<HTMLInputElement | null>(null)
const submitting = ref(false)

async function load() {
  loading.value = true
  try {
    detail.value = await taskApi.detail(id)
  } catch (e: any) {
    showFailToast(e?.response?.data?.detail || '加载失败')
  } finally {
    loading.value = false
  }
}

async function onRespond(action: 'accept' | 'reject') {
  hapticLight()
  let reason: string | undefined
  if (action === 'reject') {
    const r = window.prompt('请输入拒单原因')
    if (r === null) return
    reason = r.trim()
    if (!reason) {
      showFailToast('请填写拒单原因')
      return
    }
  } else {
    await showConfirmDialog({ title: '接单', message: '确认接受该任务？' })
  }
  submitting.value = true
  try {
    await taskApi.respond(id, action, reason)
    showSuccessToast(action === 'accept' ? '已接单' : '已拒单')
    load()
  } catch (e: any) {
    showFailToast(e?.response?.data?.detail || '操作失败')
  } finally {
    submitting.value = false
  }
}

async function onStart() {
  hapticLight()
  await showConfirmDialog({ title: '开始执行', message: '确认开始执行该任务？' })
  try {
    await taskApi.start(id)
    showSuccessToast('已开始')
    load()
  } catch (e: any) {
    showFailToast(e?.response?.data?.detail || '操作失败')
  }
}

function openComplete() {
  receipt.value = ''
  resultFiles.value = []
  resultPreviews.value = []
  showCompletePopup.value = true
}

function pickFile() {
  fileInput.value?.click()
}

function onFileChange(e: Event) {
  const input = e.target as HTMLInputElement
  const files = Array.from(input.files || [])
  files.forEach((f) => {
    resultFiles.value.push(f)
    if (f.type.startsWith('image/')) {
      resultPreviews.value.push(URL.createObjectURL(f))
    } else {
      resultPreviews.value.push('')
    }
  })
  input.value = ''
}

function removeFile(index: number) {
  if (resultPreviews.value[index]) URL.revokeObjectURL(resultPreviews.value[index])
  resultFiles.value.splice(index, 1)
  resultPreviews.value.splice(index, 1)
}

async function onSubmitComplete() {
  if (!receipt.value.trim()) {
    showFailToast('请填写完成回执')
    return
  }
  submitting.value = true
  try {
    await taskApi.complete(id, {
      receipt: receipt.value,
      result_files: resultFiles.value.length ? resultFiles.value : undefined,
    })
    showSuccessToast('已完成')
    showCompletePopup.value = false
    load()
  } catch (e: any) {
    showFailToast(e?.response?.data?.detail || '提交失败')
  } finally {
    submitting.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="page">
    <van-skeleton title :row="6" :loading="loading" v-if="loading" />
    <template v-else-if="detail">
      <van-cell-group inset>
        <van-cell :title="detail.title || '任务'" />
        <van-cell title="状态">
          <van-tag :type="(TASK_STATUS_MAP[detail.status]?.type as any) || 'default'">
            {{ getStatusLabel(TASK_STATUS_MAP, detail.status) }}
          </van-tag>
        </van-cell>
        <van-cell title="优先级">
          <van-tag :type="(TASK_PRIORITY_MAP[detail.priority]?.type as any) || 'default'">
            {{ getStatusLabel(TASK_PRIORITY_MAP, detail.priority) }}
          </van-tag>
        </van-cell>
        <van-cell title="任务类别" :value="detail.category || '-'" />
        <van-cell title="截止时间" :value="formatDateTime(detail.deadline)" />
        <van-cell title="任务地点" :value="detail.location || '-'" />
        <van-cell title="施工区域" :value="detail.construction_area || '-'" />
        <van-cell title="派单人" :value="detail.creator_name || detail.assigner || '-'" />
        <van-cell title="被指派人" :value="detail.assignee_name || detail.assignee || '-'" />
      </van-cell-group>

      <van-cell-group inset style="margin-top: 12px" title="任务描述">
        <div class="content">{{ detail.description || '（无描述）' }}</div>
      </van-cell-group>

      <van-cell-group v-if="detail.attachments?.length" inset style="margin-top: 12px" title="附件">
        <van-cell
          v-for="(a, i) in detail.attachments"
          :key="i"
          :title="a.name || a.filename || `附件${Number(i) + 1}`"
          is-link
        >
          <template #icon>
            <van-icon name="description" style="margin-right: 6px" />
          </template>
        </van-cell>
      </van-cell-group>

      <!-- 操作按钮 -->
      <div class="actions">
        <template v-if="detail.status === 'pending'">
          <van-button block type="primary" :loading="submitting" @click="onRespond('accept')">接单</van-button>
          <van-button block type="danger" plain style="margin-left: 12px" :loading="submitting" @click="onRespond('reject')">拒单</van-button>
        </template>
        <van-button v-else-if="detail.status === 'accepted'" block type="primary" @click="onStart">
          开始执行
        </van-button>
        <van-button v-else-if="detail.status === 'doing'" block type="success" @click="openComplete">
          完成任务
        </van-button>
      </div>
    </template>
    <van-empty v-else description="任务不存在或加载失败" />

    <!-- 完成任务弹窗 -->
    <van-popup v-model:show="showCompletePopup" position="bottom" round :style="{ height: '70%' }">
      <div class="popup-header">完成任务</div>
      <van-cell-group inset>
        <van-field
          v-model="receipt"
          label="完成回执"
          type="textarea"
          rows="4"
          autosize
          placeholder="请填写任务完成情况说明"
          required
        />
      </van-cell-group>
      <van-cell-group inset style="margin-top: 12px" title="成果文件（选填）">
        <div class="file-list">
          <div v-for="(url, i) in resultPreviews" :key="i" class="file-item">
            <van-image v-if="url" :src="url" width="60" height="60" fit="cover" radius="6" />
            <van-icon v-else name="description" size="30" />
            <van-icon name="cross" class="file-del" @click="removeFile(i)" />
          </div>
          <div class="file-add" @click="pickFile">
            <van-icon name="plus" size="20" color="#969799" />
          </div>
        </div>
        <input
          ref="fileInput"
          type="file"
          accept="image/*,application/pdf,.doc,.docx"
          multiple
          style="display: none"
          @change="onFileChange"
        />
      </van-cell-group>
      <div class="actions">
        <van-button block type="primary" :loading="submitting" @click="onSubmitComplete">提交</van-button>
      </div>
    </van-popup>
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
.actions {
  display: flex;
  padding: 16px 12px;
}
.popup-header {
  padding: 16px;
  font-size: 16px;
  font-weight: 600;
  text-align: center;
}
.file-list {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  padding: 12px 16px;
}
.file-item {
  position: relative;
}
.file-del {
  position: absolute;
  top: -6px;
  right: -6px;
  background: #ee0a24;
  color: #fff;
  border-radius: 50%;
  padding: 2px;
  font-size: 12px;
}
.file-add {
  width: 60px;
  height: 60px;
  background: #f7f8fa;
  border: 1px dashed #dcdee0;
  border-radius: 6px;
  display: flex;
  align-items: center;
  justify-content: center;
}
</style>
