<template>
  <div class="kg-page">
    <div class="page-header">
      <el-breadcrumb separator="/">
        <el-breadcrumb-item>系统设置</el-breadcrumb-item>
        <el-breadcrumb-item>知识图谱管理</el-breadcrumb-item>
      </el-breadcrumb>
    </div>

    <p class="page-desc">知识图谱搭建 — 实体管理、关系建立、AI 自动抽取、子图浏览。</p>

    <!-- 统计卡片 -->
    <el-row :gutter="16" style="margin-bottom: 16px;">
      <el-col :span="6" v-for="(s, i) in statsCards" :key="i">
        <el-card shadow="never" class="stat-mini">
          <div class="stat-mini-body">
            <el-icon :size="32" :color="s.color"><component :is="s.icon" /></el-icon>
            <div>
              <div class="stat-mini-num">{{ s.value }}</div>
              <div class="stat-mini-label">{{ s.label }}</div>
            </div>
          </div>
        </el-card>
      </el-col>
    </el-row>

    <!-- 批量导入 -->
    <el-card shadow="never" style="margin-bottom: 16px;">
      <div style="display:flex; justify-content:space-between; align-items:center;">
        <div>
          <span class="section-title">知识库批量导入</span>
          <p style="color:#999; font-size:13px; margin:4px 0 0 0;">扫描知识库目录中的所有 docx/xlsx/pdf 文件，用 AI 自动抽取实体和关系导入图谱</p>
        </div>
        <el-button type="success" :loading="importing" @click="handleImportFromKB">开始批量导入</el-button>
      </div>
      <el-alert v-if="importResult" style="margin-top:12px"
        :title="`导入完成 — 实体 ${importResult.extracted_entities} 个，关系 ${importResult.extracted_relations} 条，跳过 ${importResult.skipped} 个已导入文件`"
        :type="importResult.errors.length > 0 ? 'warning' : 'success'" :closable="false" />
      <div v-if="importResult?.errors?.length" style="margin-top:8px;">
        <p v-for="(err, i) in importResult.errors" :key="i" style="color:#E6A23C; font-size:12px;">{{ err }}</p>
      </div>
    </el-card>

    <!-- AI 抽取 -->
    <el-card shadow="never" style="margin-bottom: 16px;">
      <template #header>
        <div style="display:flex; justify-content:space-between; align-items:center;">
          <span class="section-title">AI 自动抽取</span>
          <el-button size="small" type="primary" :loading="extracting" @click="handleExtract">开始抽取</el-button>
        </div>
      </template>
      <el-form label-width="100px">
        <el-form-item label="源文档">
          <el-input v-model="extractForm.source_doc" placeholder="来源文档（可选）" />
        </el-form-item>
        <el-form-item label="文本内容">
          <el-input v-model="extractForm.text" type="textarea" :rows="6"
            placeholder="粘贴规范文本、施工方案、验收手册等，AI 将自动抽取实体和关系..." />
        </el-form-item>
        <el-form-item v-if="extractResult">
          <el-alert :title="`抽取完成 — 实体 ${extractResult.extracted_entities} 个，关系 ${extractResult.extracted_relations} 条`"
            type="success" :closable="false" />
        </el-form-item>
      </el-form>
    </el-card>

    <!-- 实体管理 -->
    <el-card shadow="never" style="margin-bottom: 16px;">
      <template #header>
        <div style="display:flex; justify-content:space-between; align-items:center;">
          <span class="section-title">实体管理</span>
          <div style="display:flex; gap:8px;">
            <el-input v-model="entityKeyword" placeholder="搜索实体..." size="small" clearable
              prefix-icon="Search" style="width:200px" @keyup.enter="loadEntities" />
            <el-select v-model="entityTypeFilter" clearable size="small" placeholder="类型" style="width:140px">
              <el-option label="规范条款" value="规范条款" />
              <el-option label="验收流程" value="验收流程" />
              <el-option label="施工工序" value="施工工序" />
              <el-option label="材料标准" value="材料标准" />
              <el-option label="隐患类型" value="隐患类型" />
              <el-option label="工程部位" value="工程部位" />
              <el-option label="监理要点" value="监理要点" />
            </el-select>
            <el-button size="small" type="primary" @click="showAddEntity = true">+ 新建实体</el-button>
          </div>
        </div>
      </template>

      <el-table :data="entities" stripe empty-text="暂无实体" size="small">
        <el-table-column prop="id" label="#" width="50" />
        <el-table-column prop="name" label="名称" min-width="160" show-overflow-tooltip />
        <el-table-column prop="entity_type" label="类型" width="120">
          <template #default="{ row }"><el-tag size="small">{{ row.entity_type }}</el-tag></template>
        </el-table-column>
        <el-table-column prop="description" label="描述" min-width="220" show-overflow-tooltip />
        <el-table-column prop="source_doc" label="来源" min-width="140" show-overflow-tooltip />
        <el-table-column label="操作" width="160">
          <template #default="{ row }">
            <el-button size="small" link type="primary" @click="viewSubgraph(row)">子图</el-button>
            <el-button size="small" link @click="showRelationDialog(row)">建关系</el-button>
            <el-button size="small" link type="danger" @click="deleteEntity(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- 新建实体 -->
    <el-dialog v-model="showAddEntity" title="新建实体" width="500px">
      <el-form :model="entityForm" label-width="100px">
        <el-form-item label="名称" required><el-input v-model="entityForm.name" /></el-form-item>
        <el-form-item label="类型" required>
          <el-select v-model="entityForm.entity_type" style="width:100%">
            <el-option label="规范条款" value="规范条款" />
            <el-option label="验收流程" value="验收流程" />
            <el-option label="施工工序" value="施工工序" />
            <el-option label="材料标准" value="材料标准" />
            <el-option label="隐患类型" value="隐患类型" />
            <el-option label="工程部位" value="工程部位" />
            <el-option label="监理要点" value="监理要点" />
          </el-select>
        </el-form-item>
        <el-form-item label="描述"><el-input v-model="entityForm.description" type="textarea" :rows="2" /></el-form-item>
        <el-form-item label="来源文档"><el-input v-model="entityForm.source_doc" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showAddEntity = false">取消</el-button>
        <el-button type="primary" @click="saveEntity">保存</el-button>
      </template>
    </el-dialog>

    <!-- 建立关系 -->
    <el-dialog v-model="showRelation" title="建立实体关系" width="500px">
      <el-form :model="relationForm" label-width="100px">
        <el-form-item label="源实体"><el-input v-model="relationForm.source_name" disabled /></el-form-item>
        <el-form-item label="关系类型">
          <el-select v-model="relationForm.relation_type" style="width:100%">
            <el-option label="属于" value="属于" />
            <el-option label="前置" value="前置" />
            <el-option label="后续" value="后续" />
            <el-option label="关联" value="关联" />
            <el-option label="应用于" value="应用于" />
            <el-option label="依据" value="依据" />
          </el-select>
        </el-form-item>
        <el-form-item label="目标实体" required>
          <el-input v-model="relationForm.target_name" placeholder="目标实体名称（不存在则自动创建）" />
        </el-form-item>
        <el-form-item label="说明"><el-input v-model="relationForm.description" type="textarea" :rows="2" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showRelation = false">取消</el-button>
        <el-button type="primary" @click="saveRelation">建立关系</el-button>
      </template>
    </el-dialog>

    <!-- 子图弹窗 -->
    <el-dialog v-model="showSubgraph" title="子图浏览" width="700px">
      <div v-if="subgraph.nodes.length === 0" class="empty-state">
        <el-icon :size="48" color="#ddd"><Share /></el-icon>
        <p>该实体无关联节点</p>
      </div>
      <div v-else>
        <p>共 {{ subgraph.nodes.length }} 个节点，{{ subgraph.edges.length }} 条关系</p>
        <el-table :data="subgraph.nodes" stripe size="small" max-height="300">
          <el-table-column prop="id" label="#" width="50" />
          <el-table-column prop="name" label="节点" min-width="160" />
          <el-table-column prop="type" label="类型" width="100">
            <template #default="{ row }"><el-tag size="small">{{ row.type }}</el-tag></template>
          </el-table-column>
          <el-table-column prop="description" label="描述" min-width="200" show-overflow-tooltip />
        </el-table>
      </div>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { kgApi } from '@/api'
import {
  Search, Document, Connection, Share, MagicStick,
} from '@element-plus/icons-vue'

const extracting = ref(false)
const importing = ref(false)
const importResult = ref<any>(null)
const entities = ref<any[]>([])
const entityKeyword = ref('')
const entityTypeFilter = ref('')
const showAddEntity = ref(false)
const showRelation = ref(false)
const showSubgraph = ref(false)
const subgraph = ref<any>({ nodes: [], edges: [] })
const extractResult = ref<any>(null)

const extractForm = reactive({ text: '', source_doc: '' })
const entityForm = reactive({ name: '', entity_type: '监理要点', description: '', source_doc: '' })
const relationForm = reactive({ source_name: '', target_name: '', relation_type: '关联', description: '' })

const statsCards = ref([
  { label: '实体总数', value: 0, icon: 'Document', color: '#2563EB' },
  { label: '关系总数', value: 0, icon: 'Connection', color: '#67C23A' },
  { label: '实体类型', value: 0, icon: 'Share', color: '#E6A23C' },
  { label: 'AI 抽取', value: '智能', icon: 'MagicStick', color: '#9b59b6' },
])

async function loadStats() {
  try {
    const s: any = await kgApi.stats()
    statsCards.value[0].value = s.total_entities
    statsCards.value[1].value = s.total_relations
    statsCards.value[2].value = Object.keys(s.entity_types || {}).length
  } catch {}
}

async function loadEntities() {
  try {
    entities.value = (await kgApi.listEntities({
      keyword: entityKeyword.value || undefined,
      entity_type: entityTypeFilter.value || undefined,
    }) as any) || []
  } catch {}
}

async function handleExtract() {
  if (!extractForm.text) return ElMessage.warning('请输入文本内容')
  extracting.value = true
  try {
    const res: any = await kgApi.extract(extractForm.text, extractForm.source_doc || undefined)
    extractResult.value = res
    ElMessage.success('抽取完成')
    loadStats(); loadEntities()
  } catch (e: any) { ElMessage.error(e.response?.data?.detail || '抽取失败') }
  finally { extracting.value = false }
}

async function saveEntity() {
  if (!entityForm.name) return ElMessage.warning('请输入名称')
  try {
    await kgApi.createEntity(entityForm)
    ElMessage.success('实体已创建')
    showAddEntity.value = false
    Object.assign(entityForm, { name: '', entity_type: '监理要点', description: '', source_doc: '' })
    loadEntities(); loadStats()
  } catch (e: any) { ElMessage.error('创建失败') }
}

function showRelationDialog(row: any) {
  relationForm.source_name = row.name
  relationForm.target_name = ''
  relationForm.description = ''
  showRelation.value = true
}

async function saveRelation() {
  if (!relationForm.target_name) return ElMessage.warning('请输入目标实体')
  try {
    await kgApi.createRelation(relationForm)
    ElMessage.success('关系已建立')
    showRelation.value = false
    loadStats()
  } catch (e: any) { ElMessage.error('建立失败') }
}

async function deleteEntity(row: any) {
  try {
    await ElMessageBox.confirm(`确定删除实体 "${row.name}" 及其所有关系？`, '确认', { type: 'warning' })
    await kgApi.deleteEntity(row.id)
    ElMessage.success('已删除')
    loadEntities(); loadStats()
  } catch (e: any) { if (e !== 'cancel') ElMessage.error('删除失败') }
}

async function viewSubgraph(row: any) {
  try {
    const data: any = await kgApi.subgraph(row.id, 2)
    subgraph.value = data
    showSubgraph.value = true
  } catch (e: any) { ElMessage.error('加载子图失败') }
}

async function handleImportFromKB() {
  importing.value = true
  importResult.value = null
  try {
    const res: any = await kgApi.importFromKB()
    importResult.value = res
    ElMessage.success(`导入完成：实体 ${res.extracted_entities} 个，关系 ${res.extracted_relations} 条`)
    loadStats(); loadEntities()
  } catch (e: any) {
    ElMessage.error(e.response?.data?.detail || '批量导入失败')
  } finally { importing.value = false }
}

onMounted(() => { loadStats(); loadEntities() })
</script>

<style scoped>
.page-header { margin-bottom: 8px; }
.page-desc { color: #999; font-size: 13px; margin-bottom: 16px; }
.section-title { font-weight: 600; font-size: 15px; }
.stat-mini { height: 88px; }
.stat-mini-body { display: flex; align-items: center; gap: 16px; height: 56px; }
.stat-mini-num { font-size: 24px; font-weight: 700; color: #333; line-height: 1.2; }
.stat-mini-label { font-size: 12px; color: #999; margin-top: 4px; }
.empty-state { display: flex; flex-direction: column; align-items: center; padding: 40px 0; color: #999; }
.empty-state p { margin-top: 8px; font-size: 13px; }
</style>
