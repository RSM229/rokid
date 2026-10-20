<template>
  <div>
    <el-card>
      <template #header>
        <div class="card-header">
          <span>验收流程AI智能指引</span>
        </div>
      </template>

      <el-input v-model="keyword" placeholder="输入验收项目关键词，如：钢筋、混凝土、脚手架、安全帽..." style="width: 400px; margin-right: 8px" @keyup.enter="query" />
      <el-button type="primary" @click="query" :loading="loading">查询指引</el-button>

      <div v-if="guide" style="margin-top: 24px">
        <el-descriptions :column="1" border>
          <el-descriptions-item label="验收项目">{{ guide.keyword }}</el-descriptions-item>
        </el-descriptions>

        <el-card style="margin-top: 16px" header="验收步骤" shadow="never">
          <el-steps :active="guide.steps.length" align-center>
            <el-step v-for="(step, i) in guide.steps" :key="i" :title="step" />
          </el-steps>
        </el-card>

        <el-row :gutter="16" style="margin-top: 16px">
          <el-col :span="12">
            <el-card header="必查管控要点" shadow="never">
              <ul>
                <li v-for="(p, i) in guide.key_points" :key="i">{{ p }}</li>
              </ul>
            </el-card>
          </el-col>
          <el-col :span="12">
            <el-card header="常见违规问题" shadow="never">
              <ul>
                <li v-for="(v, i) in guide.common_violations" :key="i" style="color: #F56C6C">{{ v }}</li>
              </ul>
            </el-card>
          </el-col>
        </el-row>

        <el-card style="margin-top: 16px" header="规范依据" shadow="never">
          <ul>
            <li v-for="(r, i) in guide.regulation_basis" :key="i">{{ r }}</li>
          </ul>
        </el-card>

        <el-card v-if="guide.matched_docs?.length" style="margin-top: 16px" header="关联规范文档" shadow="never">
          <el-tag v-for="(d, i) in guide.matched_docs" :key="i" style="margin: 4px">{{ d }}</el-tag>
        </el-card>

        <el-card v-if="guide.content_matches?.length" style="margin-top: 16px" header="内容匹配" shadow="never">
          <div v-for="(m, i) in guide.content_matches" :key="i" class="snippet-item">
            <strong>{{ m.name }}</strong>
            <p class="snippet-text">{{ m.snippet }}</p>
          </div>
        </el-card>
      </div>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { ElMessage } from 'element-plus'
import { knowledgeApi } from '@/api'

const keyword = ref('')
const guide = ref<any>(null)
const loading = ref(false)

async function query() {
  if (!keyword.value) {
    ElMessage.warning('请输入关键词')
    return
  }
  loading.value = true
  try {
    guide.value = await knowledgeApi.acceptanceGuide(keyword.value)
  } catch {
    ElMessage.error('查询失败')
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.snippet-item {
  margin-bottom: 12px;
  padding: 8px;
  background: #f5f7fa;
  border-radius: 4px;
}
.snippet-text {
  color: #606266;
  font-size: 13px;
  margin: 4px 0 0 0;
}
</style>
