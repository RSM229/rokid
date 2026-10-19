<template>
  <div class="knowledge-page">
    <!-- 顶部导航 -->
    <el-tabs v-model="activeTab" @tab-change="onTabChange">
      <!-- Tab 1: 知识检索 -->
      <el-tab-pane label="知识检索" name="search">
        <div class="page-header">
          <el-breadcrumb separator="/">
            <el-breadcrumb-item>监理工作台</el-breadcrumb-item>
            <el-breadcrumb-item>信息库（知识检索）</el-breadcrumb-item>
          </el-breadcrumb>
        </div>

        <p class="page-desc">搜索工程规范、验收流程、隐患标准等知识。</p>

        <!-- 搜索 -->
        <el-card shadow="never" style="margin-bottom: 16px;">
          <el-row :gutter="12" align="middle">
            <el-col :span="8">
              <el-input v-model="searchQuery" placeholder="搜索规范/知识..." clearable @keyup.enter="handleSearch">
                <template #prefix><el-icon><Search /></el-icon></template>
              </el-input>
            </el-col>
            <el-col :span="4">
              <el-select v-model="category" placeholder="分类" clearable style="width:100%">
                <el-option label="全部" value="" />
                <el-option label="验收流程" value="验收流程" />
                <el-option label="施工规范" value="施工规范" />
                <el-option label="安全标准" value="安全标准" />
                <el-option label="材料标准" value="材料标准" />
              </el-select>
            </el-col>
            <el-col :span="4"><el-button type="primary" @click="handleSearch">搜索</el-button></el-col>
            <el-col :span="8" style="text-align:right">
              <el-button type="success" @click="importDialogVisible = true">导入文档</el-button>
              <el-button type="warning" @click="batchImportDialogVisible = true">批量导入</el-button>
            </el-col>
          </el-row>
        </el-card>

        <!-- 搜索结果 -->
        <el-card shadow="never" v-if="results.length > 0 || searching">
          <template #header><span class="section-title">搜索结果 ({{ results.length }})</span></template>
          <el-table :data="results" stripe v-loading="searching" empty-text="无匹配结果" size="small">
            <el-table-column prop="title" label="标题" min-width="200" show-overflow-tooltip />
            <el-table-column prop="category" label="分类" width="120" />
            <el-table-column prop="source_file" label="来源文件" min-width="180" show-overflow-tooltip />
            <el-table-column label="操作" width="140">
              <template #default="{ row }">
                <el-button size="small" link type="primary" @click="viewDetail(row)">查看详情</el-button>
                <el-button size="small" link @click="downloadFile(row)">下载</el-button>
              </template>
            </el-table-column>
          </el-table>
        </el-card>

        <!-- 空状态 -->
        <el-card shadow="never" v-if="!searching && results.length === 0 && !hasSearched">
          <div class="empty-state">
            <el-icon :size="64" color="#ddd"><Reading /></el-icon>
            <p>输入关键词搜索工程规范与知识</p>
          </div>
        </el-card>

        <!-- 详情弹窗 -->
        <el-dialog v-model="detailVisible" title="知识详情" width="700px">
          <div v-if="currentItem">
            <h3>{{ currentItem.title }}</h3>
            <el-descriptions :column="2" border size="small" style="margin-top: 12px;">
              <el-descriptions-item label="分类">{{ currentItem.category }}</el-descriptions-item>
              <el-descriptions-item label="来源">{{ currentItem.source_file }}</el-descriptions-item>
            </el-descriptions>
            <div class="detail-content" v-html="currentItem.content"></div>
          </div>
        </el-dialog>

        <!-- 导入文档对话框 -->
        <el-dialog v-model="importDialogVisible" title="导入规范文档到知识库" width="520px">
          <el-form label-width="80px">
            <el-form-item label="文档名称">
              <el-input v-model="importName" placeholder="留空则使用文件名" />
            </el-form-item>
            <el-form-item label="目标分类">
              <el-select v-model="importCategory" placeholder="选择分类" style="width: 100%" filterable allow-create>
                <el-option
                  v-for="c in categoryList"
                  :key="c.name"
                  :label="c.name"
                  :value="c.name"
                />
                <!-- 兜底静态选项 -->
                <el-option label="省统表" value="省统表" />
                <el-option label="安全监理用表" value="安全监理用表" />
                <el-option label="验收流程" value="验收流程" />
                <el-option label="施工规范" value="施工规范" />
                <el-option label="安全标准" value="安全标准" />
                <el-option label="材料标准" value="材料标准" />
              </el-select>
            </el-form-item>
            <el-form-item label="文件">
              <el-upload action="#" :auto-upload="false" :on-change="handleImportFileChange" :limit="1" accept=".docx,.xlsx,.pdf,.doc,.xls">
                <el-button type="primary">选择文件</el-button>
                <template #tip><div class="el-upload__tip">支持 docx / xlsx / pdf / doc / xls 格式</div></template>
              </el-upload>
            </el-form-item>
          </el-form>
          <template #footer>
            <el-button @click="importDialogVisible = false">取消</el-button>
            <el-button type="primary" :loading="importLoading" @click="confirmImport">导入</el-button>
          </template>
        </el-dialog>

        <!-- 批量导入对话框 -->
        <el-dialog v-model="batchImportDialogVisible" title="批量导入规范文档" width="640px">
          <el-form label-width="80px">
            <el-form-item label="目标分类">
              <el-select v-model="batchImportCategory" placeholder="选择分类" style="width: 100%" filterable allow-create>
                <el-option
                  v-for="c in categoryList"
                  :key="c.name"
                  :label="c.name"
                  :value="c.name"
                />
                <!-- 兜底静态选项 -->
                <el-option label="省统表" value="省统表" />
                <el-option label="安全监理用表" value="安全监理用表" />
                <el-option label="验收流程" value="验收流程" />
                <el-option label="施工规范" value="施工规范" />
                <el-option label="安全标准" value="安全标准" />
                <el-option label="材料标准" value="材料标准" />
              </el-select>
            </el-form-item>
            <el-form-item label="选择文件">
              <el-upload ref="batchUploadRef" action="#" :auto-upload="false" :on-change="handleBatchFileChange" :on-remove="handleBatchFileRemove" multiple accept=".docx,.xlsx,.pdf,.doc,.xls">
                <el-button type="primary">选择多个文件</el-button>
                <template #tip><div class="el-upload__tip">支持多选 docx / xlsx / pdf / doc / xls 文件</div></template>
              </el-upload>
            </el-form-item>
            <el-form-item label="选择文件夹">
              <input type="file" webkitdirectory directory multiple ref="folderInputRef" style="display: none" @change="handleFolderChange" accept=".docx,.xlsx,.pdf,.doc,.xls" />
              <el-button @click="folderInputRef?.click()">选择整个文件夹</el-button>
              <div v-if="folderFileCount > 0" class="el-upload__tip">已从文件夹选择 {{ folderFileCount }} 个文档</div>
            </el-form-item>
            <el-form-item v-if="batchFileList.length > 0" label="已选文件">
              <div style="max-height: 200px; overflow-y: auto; width: 100%;">
                <div v-for="(f, i) in batchFileList" :key="i" class="batch-file-item">
                  <span>{{ f.name }}</span>
                  <span class="file-size">{{ (f.size / 1024).toFixed(1) }}KB</span>
                </div>
              </div>
            </el-form-item>
          </el-form>
          <template #footer>
            <el-button @click="batchImportDialogVisible = false">取消</el-button>
            <el-button type="primary" :loading="batchImportLoading" @click="confirmBatchImport">批量导入 ({{ batchFileList.length }} 个文件)</el-button>
          </template>
        </el-dialog>
      </el-tab-pane>

      <!-- Tab 2: 知识图谱 -->
      <el-tab-pane label="知识图谱" name="graph">
        <div class="page-header">
          <el-breadcrumb separator="/">
            <el-breadcrumb-item>监理工作台</el-breadcrumb-item>
            <el-breadcrumb-item>信息库（知识图谱）</el-breadcrumb-item>
          </el-breadcrumb>
        </div>

        <p class="page-desc">知识图谱搭建 - 实体管理、关系建立、AI 自动抽取、子图浏览。</p>

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
          <el-alert v-if="importResult" style="margin-top:12px" :title="`导入完成 — 实体 ${importResult.extracted_entities} 个，关系 ${importResult.extracted_relations} 条，跳过 ${importResult.skipped} 个已导入文件`" :type="importResult.errors.length > 0 ? 'warning' : 'success'" :closable="false" />
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
            <el-form-item label="源文档"><el-input v-model="extractForm.source_doc" placeholder="来源文档（可选）" /></el-form-item>
            <el-form-item label="文本内容"><el-input v-model="extractForm.text" type="textarea" :rows="6" placeholder="粘贴规范文本、施工方案、验收手册等，AI 将自动抽取实体和关系..." /></el-form-item>
            <el-form-item v-if="extractResult"><el-alert :title="`抽取完成 — 实体 ${extractResult.extracted_entities} 个，关系 ${extractResult.extracted_relations} 条`" type="success" :closable="false" /></el-form-item>
          </el-form>
        </el-card>

        <!-- 实体管理 -->
        <el-card shadow="never" style="margin-bottom: 16px;">
          <template #header>
            <div style="display:flex; justify-content:space-between; align-items:center;">
              <span class="section-title">实体管理</span>
              <div style="display:flex; gap:8px;">
                <el-input v-model="entityKeyword" placeholder="搜索实体..." size="small" clearable prefix-icon="Search" style="width:200px" @keyup.enter="loadEntities" />
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
            <el-form-item label="目标实体" required><el-input v-model="relationForm.target_name" placeholder="目标实体名称（不存在则自动创建）" /></el-form-item>
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
      </el-tab-pane>

      <!-- Tab 3: 规范库维护 -->
      <el-tab-pane label="规范库维护" name="maintain">
        <div class="page-header">
          <el-breadcrumb separator="/">
            <el-breadcrumb-item>监理工作台</el-breadcrumb-item>
            <el-breadcrumb-item>信息库（规范库维护）</el-breadcrumb-item>
          </el-breadcrumb>
        </div>

        <p class="page-desc">维护工程规范库：编辑规范名称、删除规范、移动分类，支持分类筛选。</p>

        <!-- 顶部操作栏 -->
        <el-card shadow="never" style="margin-bottom: 16px;">
          <el-row :gutter="12" align="middle">
            <el-col :span="6">
              <el-select v-model="maintainCategory" placeholder="分类筛选" clearable style="width:100%" @change="loadRegulations">
                <el-option label="全部" value="" />
                <el-option
                  v-for="c in categoryList"
                  :key="c.name"
                  :label="`${c.name}（${c.count}）`"
                  :value="c.name"
                />
              </el-select>
            </el-col>
            <el-col :span="6">
              <el-input v-model="maintainKeyword" placeholder="按名称筛选..." clearable />
            </el-col>
            <el-col :span="4">
              <el-button type="primary" @click="loadRegulations">查询</el-button>
              <el-button @click="loadCategories">刷新分类</el-button>
            </el-col>
            <el-col :span="8" style="text-align:right">
              <el-button type="success" @click="importDialogVisible = true">导入文档</el-button>
              <el-button type="warning" @click="batchImportDialogVisible = true">批量导入</el-button>
            </el-col>
          </el-row>
        </el-card>

        <!-- 规范列表 -->
        <el-card shadow="never">
          <template #header>
            <span class="section-title">规范列表 ({{ filteredRegulations.length }})</span>
          </template>
          <el-table :data="filteredRegulations" stripe v-loading="regulationsLoading" empty-text="暂无规范" size="small">
            <el-table-column prop="name" label="规范名称" min-width="200" show-overflow-tooltip />
            <el-table-column prop="category" label="分类" width="140" />
            <el-table-column prop="filename" label="文件名" min-width="180" show-overflow-tooltip />
            <el-table-column label="状态" width="80">
              <template #default="{ row }">
                <el-tag :type="row.exists ? 'success' : 'info'" size="small">{{ row.exists ? '可用' : '缺失' }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="300" fixed="right">
              <template #default="{ row }">
                <el-button size="small" link type="primary" @click="openEditRegulation(row)">编辑</el-button>
                <el-button size="small" link type="warning" @click="openMoveCategory(row)">移动分类</el-button>
                <el-button size="small" link type="danger" @click="confirmDeleteRegulation(row)">删除</el-button>
                <el-button size="small" link @click="downloadRegulationFile(row)">下载</el-button>
              </template>
            </el-table-column>
          </el-table>
        </el-card>

        <!-- 编辑规范名称弹窗 -->
        <el-dialog v-model="editRegulationVisible" title="编辑规范名称" width="480px">
          <el-form label-width="100px">
            <el-form-item label="原文件名">
              <el-input :model-value="editRegulationForm.filename" disabled />
            </el-form-item>
            <el-form-item label="当前分类">
              <el-input :model-value="editRegulationForm.category" disabled />
            </el-form-item>
            <el-form-item label="新规范名称" required>
              <el-input v-model="editRegulationForm.new_filename" placeholder="请输入新的规范名称" />
            </el-form-item>
          </el-form>
          <template #footer>
            <el-button @click="editRegulationVisible = false">取消</el-button>
            <el-button type="primary" :loading="editRegulationLoading" @click="submitEditRegulation">保存</el-button>
          </template>
        </el-dialog>

        <!-- 移动分类弹窗 -->
        <el-dialog v-model="moveCategoryVisible" title="移动到目标分类" width="480px">
          <el-form label-width="100px">
            <el-form-item label="规范名称">
              <el-input :model-value="moveCategoryForm.filename" disabled />
            </el-form-item>
            <el-form-item label="当前分类">
              <el-input :model-value="moveCategoryForm.currentCategory" disabled />
            </el-form-item>
            <el-form-item label="目标分类" required>
              <el-select v-model="moveCategoryForm.targetCategory" placeholder="选择目标分类" style="width:100%">
                <el-option
                  v-for="c in categoryList"
                  :key="c.name"
                  :label="c.name"
                  :value="c.name"
                />
              </el-select>
            </el-form-item>
          </el-form>
          <template #footer>
            <el-button @click="moveCategoryVisible = false">取消</el-button>
            <el-button type="primary" :loading="moveCategoryLoading" @click="submitMoveCategory">确认移动</el-button>
          </template>
        </el-dialog>
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { knowledgeApi, kgApi } from '@/api'
import { Search, Reading, Document, Connection, Share, MagicStick } from '@element-plus/icons-vue'

const activeTab = ref('search')

function onTabChange(tab: string) {
  if (tab === 'graph') { loadStats(); loadEntities() }
  if (tab === 'maintain') { loadCategories(); loadRegulations() }
}

// ===== 知识检索 Tab =====
const searchQuery = ref('')
const category = ref('')
const results = ref<any[]>([])
const searching = ref(false)
const hasSearched = ref(false)
const detailVisible = ref(false)
const currentItem = ref<any>(null)

const importDialogVisible = ref(false)
const importName = ref('')
const importCategory = ref('省统表')
const importFile = ref<File | null>(null)
const importLoading = ref(false)

// 批量导入
const batchImportDialogVisible = ref(false)
const batchImportCategory = ref('省统表')
const batchImportLoading = ref(false)
const batchFileList = ref<File[]>([])
const folderInputRef = ref<HTMLInputElement | null>(null)
const folderFileCount = ref(0)

async function handleSearch() {
  if (!searchQuery.value) return ElMessage.warning('请输入搜索关键词')
  searching.value = true
  hasSearched.value = true
  try {
    const res: any = await knowledgeApi.searchRegulations(searchQuery.value, category.value || undefined)
    results.value = res.results || res.regulations || []
  } catch (e: any) { ElMessage.error(e.response?.data?.detail || '搜索失败'); results.value = [] }
  finally { searching.value = false }
}

function viewDetail(row: any) { currentItem.value = row; detailVisible.value = true }

async function downloadFile(row: any) {
  try {
    const blob: any = await knowledgeApi.downloadRegulation(row.source_file || row.filename, row.category)
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url; a.download = row.source_file || 'download.pdf'; a.click()
    URL.revokeObjectURL(url)
  } catch (e) { ElMessage.error('下载失败') }
}

function handleImportFileChange(file: any) { importFile.value = file.raw || null }

async function confirmImport() {
  if (!importFile.value) return ElMessage.warning('请选择要导入的文件')
  importLoading.value = true
  try {
    const res: any = await knowledgeApi.importRegulation(importFile.value, importCategory.value, importName.value || undefined)
    ElMessage.success(res.message || '导入成功')
    importDialogVisible.value = false; importName.value = ''; importFile.value = null
  } catch (e: any) { ElMessage.error(e.response?.data?.detail || '导入失败') }
  finally { importLoading.value = false }
}

const ALLOWED_EXTS = ['.docx', '.xlsx', '.pdf', '.doc', '.xls']

function handleBatchFileChange(file: any) {
  const raw: File = file.raw
  if (raw && ALLOWED_EXTS.some(ext => raw.name.toLowerCase().endsWith(ext))) batchFileList.value.push(raw)
  else ElMessage.warning(`不支持的文件类型: ${raw?.name || ''}`)
}

function handleBatchFileRemove(file: any) { const idx = batchFileList.value.findIndex(f => f.name === file.name); if (idx >= 0) batchFileList.value.splice(idx, 1) }

function handleFolderChange(e: Event) {
  const target = e.target as HTMLInputElement
  if (!target.files) return
  const files = Array.from(target.files)
  const valid = files.filter(f => ALLOWED_EXTS.some(ext => f.name.toLowerCase().endsWith(ext)))
  batchFileList.value.push(...valid)
  folderFileCount.value = valid.length
  if (valid.length < files.length) ElMessage.info(`文件夹共 ${files.length} 个文件，已筛选出 ${valid.length} 个文档文件`)
}

async function confirmBatchImport() {
  if (batchFileList.value.length === 0) return ElMessage.warning('请选择至少一个文件')
  batchImportLoading.value = true
  try {
    const res: any = await knowledgeApi.importRegulationBatch(batchFileList.value, batchImportCategory.value)
    ElMessage.success(res.message || '批量导入完成')
    batchImportDialogVisible.value = false; batchFileList.value = []; folderFileCount.value = 0
    if (folderInputRef.value) folderInputRef.value.value = ''
  } catch (e: any) { ElMessage.error(e.response?.data?.detail || '批量导入失败') }
  finally { batchImportLoading.value = false }
}

// ===== 知识图谱 Tab =====
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
  { label: '实体总数', value: 0, icon: 'Document', color: '#409EFF' },
  { label: '关系总数', value: 0, icon: 'Connection', color: '#67C23A' },
  { label: '实体类型', value: 0, icon: 'Share', color: '#E6A23C' },
  { label: 'AI 抽取', value: '智能', icon: 'MagicStick', color: '#9b59b6' },
])

async function loadStats() { try { const s: any = await kgApi.stats(); statsCards.value[0].value = s.total_entities; statsCards.value[1].value = s.total_relations; statsCards.value[2].value = Object.keys(s.entity_types || {}).length } catch {} }

async function loadEntities() { try { entities.value = (await kgApi.listEntities({ keyword: entityKeyword.value || undefined, entity_type: entityTypeFilter.value || undefined }) as any) || [] } catch {} }

async function handleExtract() {
  if (!extractForm.text) return ElMessage.warning('请输入文本内容')
  extracting.value = true
  try { const res: any = await kgApi.extract(extractForm.text, extractForm.source_doc || undefined); extractResult.value = res; ElMessage.success('抽取完成'); loadStats(); loadEntities() }
  catch (e: any) { ElMessage.error(e.response?.data?.detail || '抽取失败') }
  finally { extracting.value = false }
}

async function saveEntity() {
  if (!entityForm.name) return ElMessage.warning('请输入名称')
  try { await kgApi.createEntity(entityForm); ElMessage.success('实体已创建'); showAddEntity.value = false; Object.assign(entityForm, { name: '', entity_type: '监理要点', description: '', source_doc: '' }); loadEntities(); loadStats() }
  catch (e: any) { ElMessage.error('创建失败') }
}

function showRelationDialog(row: any) { relationForm.source_name = row.name; relationForm.target_name = ''; relationForm.description = ''; showRelation.value = true }

async function saveRelation() {
  if (!relationForm.target_name) return ElMessage.warning('请输入目标实体')
  try { await kgApi.createRelation(relationForm); ElMessage.success('关系已建立'); showRelation.value = false; loadStats() }
  catch (e: any) { ElMessage.error('建立失败') }
}

async function deleteEntity(row: any) {
  try { await ElMessageBox.confirm(`确定删除实体 "${row.name}" 及其所有关系？`, '确认', { type: 'warning' }); await kgApi.deleteEntity(row.id); ElMessage.success('已删除'); loadEntities(); loadStats() }
  catch (e: any) { if (e !== 'cancel') ElMessage.error('删除失败') }
}

async function viewSubgraph(row: any) { try { const data: any = await kgApi.subgraph(row.id, 2); subgraph.value = data; showSubgraph.value = true } catch (e: any) { ElMessage.error('加载子图失败') } }

async function handleImportFromKB() {
  importing.value = true; importResult.value = null
  try { const res: any = await kgApi.importFromKB(); importResult.value = res; ElMessage.success(`导入完成：实体 ${res.extracted_entities} 个，关系 ${res.extracted_relations} 条`); loadStats(); loadEntities() }
  catch (e: any) { ElMessage.error(e.response?.data?.detail || '批量导入失败') }
  finally { importing.value = false }
}

// ===== 规范库维护 Tab =====
interface CategoryItem { name: string; count: number }
const categoryList = ref<CategoryItem[]>([])
const regulationsList = ref<any[]>([])
const regulationsLoading = ref(false)
const maintainCategory = ref('')
const maintainKeyword = ref('')

// 编辑规范
const editRegulationVisible = ref(false)
const editRegulationLoading = ref(false)
const editRegulationForm = reactive({
  filename: '',
  category: '',
  new_filename: '',
})

// 移动分类
const moveCategoryVisible = ref(false)
const moveCategoryLoading = ref(false)
const moveCategoryForm = reactive({
  filename: '',
  currentCategory: '',
  targetCategory: '',
})

// 按关键字过滤（本地筛选）
const filteredRegulations = computed(() => {
  if (!maintainKeyword.value) return regulationsList.value
  const kw = maintainKeyword.value.toLowerCase()
  return regulationsList.value.filter(
    (r: any) =>
      (r.name || '').toLowerCase().includes(kw) ||
      (r.filename || '').toLowerCase().includes(kw),
  )
})

async function loadCategories() {
  try {
    const res: any = await knowledgeApi.listCategories()
    // 后端可能返回 { categories: [...] } 或 [...] 等结构，统一兼容
    const list = res.categories || res.items || res || []
    categoryList.value = (list as any[]).map((c: any) => {
      if (typeof c === 'string') return { name: c, count: 0 }
      return { name: c.name || c.category || '', count: c.count ?? 0 }
    })
  } catch {
    categoryList.value = []
  }
}

async function loadRegulations() {
  regulationsLoading.value = true
  try {
    const res: any = await knowledgeApi.listRegulations(maintainCategory.value || undefined)
    regulationsList.value = res.results || res.regulations || res.items || []
  } catch (e: any) {
    ElMessage.error(e.response?.data?.detail || '加载规范列表失败')
    regulationsList.value = []
  } finally {
    regulationsLoading.value = false
  }
}

function openEditRegulation(row: any) {
  editRegulationForm.filename = row.filename || row.source_file || ''
  editRegulationForm.category = row.category || ''
  editRegulationForm.new_filename = row.name || row.filename || ''
  editRegulationVisible.value = true
}

async function submitEditRegulation() {
  if (!editRegulationForm.new_filename.trim()) {
    ElMessage.warning('请输入新的规范名称')
    return
  }
  editRegulationLoading.value = true
  try {
    await knowledgeApi.maintainRegulation({
      action: 'update',
      filename: editRegulationForm.filename,
      new_filename: editRegulationForm.new_filename.trim(),
    })
    ElMessage.success('规范名称已更新')
    editRegulationVisible.value = false
    loadRegulations()
    loadCategories()
  } catch (e: any) {
    ElMessage.error(e.response?.data?.detail || '更新失败')
  } finally {
    editRegulationLoading.value = false
  }
}

async function confirmDeleteRegulation(row: any) {
  try {
    await ElMessageBox.confirm(
      `确定删除规范 "${row.name || row.filename}"？该操作不可恢复。`,
      '删除确认',
      { type: 'warning' },
    )
    await knowledgeApi.maintainRegulation({
      action: 'delete',
      filename: row.filename || row.source_file || '',
    })
    ElMessage.success('已删除')
    loadRegulations()
    loadCategories()
  } catch (e: any) {
    if (e !== 'cancel') ElMessage.error(e.response?.data?.detail || '删除失败')
  }
}

function openMoveCategory(row: any) {
  moveCategoryForm.filename = row.filename || row.source_file || ''
  moveCategoryForm.currentCategory = row.category || ''
  moveCategoryForm.targetCategory = ''
  moveCategoryVisible.value = true
}

async function submitMoveCategory() {
  if (!moveCategoryForm.targetCategory) {
    ElMessage.warning('请选择目标分类')
    return
  }
  if (moveCategoryForm.targetCategory === moveCategoryForm.currentCategory) {
    ElMessage.warning('目标分类与当前分类相同')
    return
  }
  moveCategoryLoading.value = true
  try {
    await knowledgeApi.maintainRegulation({
      action: 'move_category',
      filename: moveCategoryForm.filename,
      target_category: moveCategoryForm.targetCategory,
    })
    ElMessage.success('已移动到目标分类')
    moveCategoryVisible.value = false
    loadRegulations()
    loadCategories()
  } catch (e: any) {
    ElMessage.error(e.response?.data?.detail || '移动分类失败')
  } finally {
    moveCategoryLoading.value = false
  }
}

async function downloadRegulationFile(row: any) {
  try {
    const blob: any = await knowledgeApi.downloadRegulation(
      row.filename || row.source_file,
      row.category,
    )
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = row.filename || row.source_file || 'download'
    a.click()
    URL.revokeObjectURL(url)
  } catch {
    ElMessage.error('下载失败')
  }
}

onMounted(() => {
  // 预加载分类列表，供导入弹窗和规范库维护使用
  loadCategories()
  if (activeTab.value === 'graph') { loadStats(); loadEntities() }
  if (activeTab.value === 'maintain') { loadRegulations() }
})
</script>

<style scoped>
.page-header { margin-bottom: 8px; }
.page-desc { color: #999; font-size: 13px; margin-bottom: 16px; }
.section-title { font-weight: 600; font-size: 15px; }
.empty-state { display: flex; flex-direction: column; align-items: center; justify-content: center; height: 200px; color: #999; }
.empty-state p { margin-top: 12px; font-size: 14px; }
.detail-content { padding: 12px 0; line-height: 1.8; white-space: pre-wrap; }
.batch-file-item { display: flex; justify-content: space-between; padding: 6px 8px; border-bottom: 1px solid #f0f0f0; font-size: 13px; }
.file-size { color: #909399; font-size: 12px; }
.stat-mini { height: 88px; }
.stat-mini-body { display: flex; align-items: center; gap: 16px; height: 56px; }
.stat-mini-num { font-size: 24px; font-weight: 700; color: #333; line-height: 1.2; }
.stat-mini-label { font-size: 12px; color: #999; margin-top: 4px; }
</style>
