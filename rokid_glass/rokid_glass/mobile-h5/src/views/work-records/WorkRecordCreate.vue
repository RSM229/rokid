<script setup lang="ts">
/**
 * 创建日期：2026-07-19
 * 新建工作记录：类型选择 + 动态字段表单 + 照片上传（最多 9 张）
 * 提交时调用 workRecordApi.create
 */
import { ref, computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { showSuccessToast, showFailToast } from 'vant'
import { workRecordApi } from '@/api'
import { useAuthStore } from '@/stores/auth'
import { hapticLight } from '@/utils/haptic'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()

const typeOptions = [
  { text: '巡视记录', value: 'inspection' },
  { text: '验收记录', value: 'acceptance' },
  { text: '旁站记录', value: 'station' },
  { text: '见证记录', value: 'witness' },
]
const showTypePicker = ref(false)
const recordType = ref<string>((route.query.type as string) || 'inspection')
const typeLabel = computed(
  () => typeOptions.find((o) => o.value === recordType.value)?.text || '巡视记录',
)

const saving = ref(false)

// 巡视字段
const inspectionForm = ref({
  construction_area: '',
  location: '',
  construction_content: '',
  weather: '',
  remark: '',
})
// 验收字段
const acceptanceForm = ref({
  acceptance_type: '',
  construction_area: '',
  location: '',
  acceptance_content: '',
  result: '',
  remark: '',
})
// 旁站字段
const stationForm = ref({
  project_name: '',
  position: '',
  start_time: '',
  end_time: '',
  description: '',
  conclusion: '',
})
// 见证字段
const witnessForm = ref({
  material_name: '',
  specification: '',
  construction_area: '',
  location: '',
  remark: '',
})

// 照片
const photos = ref<File[]>([])
const photoPreviews = ref<string[]>([])
const fileInput = ref<HTMLInputElement | null>(null)

const acceptanceResultOptions = [
  { text: '合格', value: 'pass' },
  { text: '不合格', value: 'fail' },
  { text: '待整改', value: 'pending' },
]
const showResultPicker = ref(false)

const weatherOptions = ['晴', '阴', '雨', '雪', '雾', '大风']
const showWeatherPicker = ref(false)

function onTypeConfirm({ selectedOptions }: { selectedOptions: any[] }) {
  const sel = selectedOptions[0]
  if (sel) recordType.value = sel.value
  showTypePicker.value = false
}

function onResultConfirm({ selectedOptions }: { selectedOptions: any[] }) {
  const sel = selectedOptions[0]
  if (sel) acceptanceForm.value.result = sel.value
  showResultPicker.value = false
}

function onWeatherConfirm({ selectedOptions }: { selectedOptions: any[] }) {
  const sel = selectedOptions[0]
  if (sel) inspectionForm.value.weather = sel.value
  showWeatherPicker.value = false
}

function pickPhoto() {
  if (photos.value.length >= 9) {
    showFailToast('最多上传 9 张照片')
    return
  }
  fileInput.value?.click()
}

function onPhotoChange(e: Event) {
  const input = e.target as HTMLInputElement
  const files = Array.from(input.files || [])
  const remain = 9 - photos.value.length
  const adding = files.slice(0, remain)
  adding.forEach((f) => {
    photos.value.push(f)
    photoPreviews.value.push(URL.createObjectURL(f))
  })
  if (files.length > remain) {
    showFailToast(`仅添加前 ${remain} 张，已达上限`)
  }
  input.value = ''
}

function removePhoto(index: number) {
  URL.revokeObjectURL(photoPreviews.value[index])
  photos.value.splice(index, 1)
  photoPreviews.value.splice(index, 1)
}

function buildPayload(): any {
  const base: any = { type: recordType.value }
  if (recordType.value === 'inspection') {
    return { ...base, ...inspectionForm.value }
  }
  if (recordType.value === 'acceptance') {
    return { ...base, ...acceptanceForm.value }
  }
  if (recordType.value === 'station') {
    return { ...base, ...stationForm.value }
  }
  return { ...base, ...witnessForm.value }
}

function validate(): boolean {
  const t = recordType.value
  if (t === 'inspection') {
    if (!inspectionForm.value.construction_area || !inspectionForm.value.construction_content) {
      showFailToast('请填写施工区域和施工内容')
      return false
    }
  } else if (t === 'acceptance') {
    if (!acceptanceForm.value.acceptance_type || !acceptanceForm.value.construction_area) {
      showFailToast('请填写验收类型和施工区域')
      return false
    }
  } else if (t === 'station') {
    if (!stationForm.value.project_name || !stationForm.value.start_time) {
      showFailToast('请填写旁站工程和开始时间')
      return false
    }
  } else if (t === 'witness') {
    if (!witnessForm.value.material_name) {
      showFailToast('请填写材料名称')
      return false
    }
  }
  return true
}

async function onSave() {
  if (!validate()) return
  hapticLight()
  saving.value = true
  try {
    const payload = buildPayload()
    const res: any = photos.value.length
      ? await workRecordApi.createWithPhotos(payload, photos.value)
      : await workRecordApi.create(payload)
    const recordId = Number(res?.record_id || res?.id)
    if (!recordId) {
      if (res?.queued) {
        showSuccessToast('已加入离线同步队列')
        router.replace('/work-records')
        return
      }
      throw new Error('后端未返回工作记录 ID')
    }
    showSuccessToast('已保存')
    router.replace(`/work-records/${recordId}`)
  } catch (e: any) {
    showFailToast(e?.response?.data?.detail || '保存失败')
  } finally {
    saving.value = false
  }
}

onMounted(() => {
  // 预填项目名（旁站工程）
  if (!stationForm.value.project_name) {
    stationForm.value.project_name = auth.user?.project_name || ''
  }
})
</script>

<template>
  <div class="page">
    <!-- 类型选择 -->
    <van-cell-group inset>
      <van-field
        :model-value="typeLabel"
        is-link
        readonly
        label="记录类型"
        placeholder="选择记录类型"
        @click="showTypePicker = true"
      />
    </van-cell-group>

    <!-- 巡视表单 -->
    <van-cell-group v-if="recordType === 'inspection'" inset style="margin-top: 12px" title="巡视信息">
      <van-field v-model="inspectionForm.construction_area" label="施工区域" placeholder="请输入施工区域" required />
      <van-field v-model="inspectionForm.location" label="位置" placeholder="请输入位置" />
      <van-field
        v-model="inspectionForm.construction_content"
        label="施工内容"
        type="textarea"
        rows="2"
        autosize
        placeholder="请输入施工内容"
        required
      />
      <van-field
        :model-value="inspectionForm.weather"
        label="天气"
        is-link
        readonly
        placeholder="选择天气"
        @click="showWeatherPicker = true"
      />
      <van-field v-model="inspectionForm.remark" label="巡视备注" type="textarea" rows="2" autosize placeholder="选填" />
    </van-cell-group>

    <!-- 验收表单 -->
    <van-cell-group v-else-if="recordType === 'acceptance'" inset style="margin-top: 12px" title="验收信息">
      <van-field v-model="acceptanceForm.acceptance_type" label="验收类型" placeholder="如：分项工程验收" required />
      <van-field v-model="acceptanceForm.construction_area" label="施工区域" placeholder="请输入施工区域" required />
      <van-field v-model="acceptanceForm.location" label="位置" placeholder="请输入位置" />
      <van-field
        v-model="acceptanceForm.acceptance_content"
        label="验收内容"
        type="textarea"
        rows="2"
        autosize
        placeholder="请输入验收内容"
      />
      <van-field
        :model-value="acceptanceResultOptions.find(o => o.value === acceptanceForm.result)?.text"
        label="验收结果"
        is-link
        readonly
        placeholder="选择验收结果"
        @click="showResultPicker = true"
      />
      <van-field v-model="acceptanceForm.remark" label="备注" type="textarea" rows="2" autosize placeholder="选填" />
    </van-cell-group>

    <!-- 旁站表单 -->
    <van-cell-group v-else-if="recordType === 'station'" inset style="margin-top: 12px" title="旁站信息">
      <van-field v-model="stationForm.project_name" label="旁站工程" placeholder="请输入旁站工程名称" required />
      <van-field v-model="stationForm.position" label="旁站部位" placeholder="请输入旁站部位" />
      <van-field v-model="stationForm.start_time" label="开始时间" placeholder="YYYY-MM-DD HH:mm" required />
      <van-field v-model="stationForm.end_time" label="结束时间" placeholder="YYYY-MM-DD HH:mm" />
      <van-field
        v-model="stationForm.description"
        label="情况描述"
        type="textarea"
        rows="3"
        autosize
        placeholder="请输入旁站情况描述"
      />
      <van-field
        v-model="stationForm.conclusion"
        label="旁站结论"
        type="textarea"
        rows="2"
        autosize
        placeholder="请输入旁站结论"
      />
    </van-cell-group>

    <!-- 见证表单 -->
    <van-cell-group v-else inset style="margin-top: 12px" title="见证信息">
      <van-field v-model="witnessForm.material_name" label="材料名称" placeholder="请输入材料名称" required />
      <van-field v-model="witnessForm.specification" label="规格型号" placeholder="请输入规格型号" />
      <van-field v-model="witnessForm.construction_area" label="施工区域" placeholder="请输入施工区域" />
      <van-field v-model="witnessForm.location" label="位置" placeholder="请输入位置" />
      <van-field v-model="witnessForm.remark" label="备注" type="textarea" rows="2" autosize placeholder="选填" />
    </van-cell-group>

    <!-- 照片上传 -->
    <van-cell-group inset style="margin-top: 12px" title="现场照片（最多 9 张）">
      <div class="photo-grid">
        <div v-for="(url, i) in photoPreviews" :key="i" class="photo-item">
          <van-image :src="url" width="100%" height="80" fit="cover" radius="6" />
          <van-icon name="cross" class="photo-del" @click="removePhoto(i)" />
        </div>
        <div v-if="photos.length < 9" class="photo-add" @click="pickPhoto">
          <van-icon name="photograph" size="22" color="#969799" />
          <span>{{ photos.length }}/9</span>
        </div>
      </div>
      <input
        ref="fileInput"
        type="file"
        accept="image/*"
        capture="environment"
        multiple
        style="display: none"
        @change="onPhotoChange"
      />
    </van-cell-group>

    <div class="actions">
      <van-button block type="primary" :loading="saving" @click="onSave">保存</van-button>
    </div>

    <van-popup v-model:show="showTypePicker" position="bottom" round>
      <van-picker
        :columns="typeOptions"
        @confirm="onTypeConfirm"
        @cancel="showTypePicker = false"
      />
    </van-popup>
    <van-popup v-model:show="showResultPicker" position="bottom" round>
      <van-picker
        :columns="acceptanceResultOptions"
        @confirm="onResultConfirm"
        @cancel="showResultPicker = false"
      />
    </van-popup>
    <van-popup v-model:show="showWeatherPicker" position="bottom" round>
      <van-picker
        :columns="weatherOptions.map(w => ({ text: w, value: w }))"
        @confirm="onWeatherConfirm"
        @cancel="showWeatherPicker = false"
      />
    </van-popup>
  </div>
</template>

<style scoped>
.photo-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 8px;
  padding: 12px 16px;
}
.photo-item {
  position: relative;
}
.photo-del {
  position: absolute;
  top: -6px;
  right: -6px;
  background: #ee0a24;
  color: #fff;
  border-radius: 50%;
  padding: 2px;
  font-size: 12px;
  z-index: 1;
}
.photo-add {
  height: 80px;
  background: #f7f8fa;
  border: 1px dashed #dcdee0;
  border-radius: 6px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 4px;
  font-size: 11px;
  color: #969799;
}
.actions {
  padding: 16px 12px;
}
</style>
