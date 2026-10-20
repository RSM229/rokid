<script setup lang="ts">
/**
 * 创建日期：2026-07-18
 * 规范库查询：关键词检索 + 分类筛选 + LLM 智能问答
 */
import { ref, onMounted } from 'vue'
import { knowledgeApi } from '@/api'
import { showFailToast } from 'vant'

const keyword = ref('')
const category = ref('')
const categories = ref<string[]>([])
const showCategoryPicker = ref(false)
const list = ref<any[]>([])
const loading = ref(false)
const llmAnswer = ref('')
const llmLoading = ref(false)

async function loadCategories() {
  try {
    const res: any = await knowledgeApi.categories()
    categories.value = Array.isArray(res) ? res : res?.categories || []
  } catch {
    categories.value = []
  }
}

async function onSearch() {
  if (!keyword.value.trim()) {
    list.value = []
    return
  }
  loading.value = true
  llmAnswer.value = ''
  try {
    const res: any = await knowledgeApi.searchRegulations(keyword.value, category.value || undefined)
    list.value = res?.items || res?.list || (Array.isArray(res) ? res : [])
  } catch (e: any) {
    showFailToast(e?.response?.data?.detail || '查询失败')
  } finally {
    loading.value = false
  }
}

async function onLlmQuery() {
  if (!keyword.value.trim()) return
  llmLoading.value = true
  llmAnswer.value = ''
  try {
    const res: any = await knowledgeApi.llmQuery(keyword.value, true)
    llmAnswer.value = res?.answer || res?.content || res?.result || (typeof res === 'string' ? res : '')
  } catch (e: any) {
    showFailToast(e?.response?.data?.detail || '智能问答失败')
  } finally {
    llmLoading.value = false
  }
}

function onCategoryConfirm({ selectedOptions }: { selectedOptions: any[] }) {
  category.value = selectedOptions[0]?.value || ''
  showCategoryPicker.value = false
  onSearch()
}

onMounted(loadCategories)
</script>

<template>
  <div class="page">
    <van-search
      v-model="keyword"
      show-action
      placeholder="搜索规范条文 / 关键词"
      @search="onSearch"
      @clear="list = []"
    >
      <template #action>
        <div @click="onLlmQuery">智能问答</div>
      </template>
    </van-search>

    <van-cell-group inset>
      <van-cell
        :title="category ? `分类：${category}` : '选择分类'"
        is-link
        @click="showCategoryPicker = true"
      />
    </van-cell-group>

    <div v-if="llmLoading || llmAnswer" class="llm-box">
      <div class="llm-title"><van-icon name="chat-o" /> AI 解答</div>
      <van-loading v-if="llmLoading" size="18px">生成中...</van-loading>
      <div v-else class="llm-content">{{ llmAnswer }}</div>
    </div>

    <van-cell-group inset style="margin-top: 12px" v-if="list.length">
      <van-cell v-for="(item, i) in list" :key="i" :title="item.name || item.title" :label="item.snippet || item.summary || ''" />
    </van-cell-group>
    <van-empty v-else-if="!loading && keyword" description="无匹配规范" />

    <van-popup v-model:show="showCategoryPicker" position="bottom" round>
      <van-picker
        :columns="[{ text: '全部分类', value: '' }, ...categories.map(c => ({ text: c, value: c }))]"
        @confirm="onCategoryConfirm"
        @cancel="showCategoryPicker = false"
      />
    </van-popup>
  </div>
</template>

<style scoped>
.llm-box {
  margin: 12px 16px;
  padding: 12px;
  background: #ebf2ff;
  border-radius: 10px;
}
.llm-title {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  font-weight: 600;
  color: #1989fa;
  margin-bottom: 8px;
}
.llm-content {
  font-size: 13px;
  line-height: 1.7;
  white-space: pre-wrap;
  color: #323233;
}
</style>
