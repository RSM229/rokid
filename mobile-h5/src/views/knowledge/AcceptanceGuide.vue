<script setup lang="ts">
/**
 * 创建日期：2026-07-18
 * 验收流程指引：输入验收事项关键词，获取流程指引
 */
import { ref } from 'vue'
import { knowledgeApi } from '@/api'
import { showFailToast } from 'vant'

const keyword = ref('')
const guide = ref('')
const loading = ref(false)

const hotKeywords = ['钢筋验收', '混凝土浇筑', '防水工程', '脚手架', '电气安装']

async function onQuery(kw?: string) {
  const q = kw || keyword.value
  if (!q.trim()) return
  keyword.value = q
  loading.value = true
  guide.value = ''
  try {
    const res: any = await knowledgeApi.acceptanceGuide(q)
    guide.value = res?.guide || res?.content || res?.answer || (typeof res === 'string' ? res : JSON.stringify(res, null, 2))
  } catch (e: any) {
    showFailToast(e?.response?.data?.detail || '查询失败')
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="page">
    <van-search v-model="keyword" placeholder="输入验收事项，如：钢筋验收" @search="() => onQuery()" />

    <div class="hot">
      <span class="hot-label">热门：</span>
      <van-tag
        v-for="k in hotKeywords"
        :key="k"
        plain
        type="primary"
        size="medium"
        class="hot-tag"
        @click="onQuery(k)"
      >
        {{ k }}
      </van-tag>
    </div>

    <div class="guide-box">
      <van-loading v-if="loading" size="20px" vertical>生成中...</van-loading>
      <div v-else-if="guide" class="guide-content">{{ guide }}</div>
      <van-empty v-else description="输入验收事项查看流程指引" />
    </div>
  </div>
</template>

<style scoped>
.hot {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  padding: 8px 16px;
}
.hot-label {
  font-size: 12px;
  color: #969799;
}
.hot-tag {
  cursor: pointer;
}
.guide-box {
  margin: 8px 12px;
  padding: 16px;
  background: #fff;
  border-radius: 12px;
  min-height: 200px;
}
.guide-content {
  font-size: 14px;
  line-height: 1.8;
  white-space: pre-wrap;
  color: #323233;
}
</style>
