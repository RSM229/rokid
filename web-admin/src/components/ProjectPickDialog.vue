<template>
  <el-dialog
    :model-value="pickerVisible"
    title="选择写入的项目"
    width="440px"
    append-to-body
    @update:model-value="onVisibleChange"
  >
    <div class="pick-tip">
      当前是「全部项目」视角，看到的记录来自所有项目；{{ pickerAction }}需要指定归属项目，
      数据会写入你选择的这个项目。
    </div>
    <el-select
      v-model="pickValue"
      class="pick-select"
      placeholder="请选择项目"
      filterable
    >
      <el-option
        v-for="p in pickerProjects"
        :key="p.id"
        :label="p.name"
        :value="p.id"
      />
    </el-select>
    <template #footer>
      <el-button @click="closePicker(null)">取消</el-button>
      <el-button type="primary" :disabled="!pickerValue" @click="closePicker(pickerValue)">
        继续
      </el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import {
  closePicker,
  pickerAction,
  pickerProjects,
  pickerValue,
  pickerVisible,
} from '@/composables/useAllProjectsGuard'

/** 用可写 computed 承接下拉，避免模板里直接写导入的 ref。 */
const pickValue = computed({
  get: () => pickerValue.value,
  set: (v: number | null) => { pickerValue.value = v },
})

/** 点右上角关闭 / 点遮罩时 dialog 会发 false，等同于取消。 */
function onVisibleChange(v: boolean | string | number) {
  if (!v) closePicker(null)
}
</script>

<style scoped>
.pick-tip {
  margin-bottom: 12px;
  color: var(--el-text-color-secondary);
  font-size: 13px;
  line-height: 1.6;
}
.pick-select {
  width: 100%;
}
</style>
