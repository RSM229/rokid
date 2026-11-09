<template>
  <div class="image-archive-page">
    <div class="page-header">
      <el-breadcrumb separator="/">
        <el-breadcrumb-item>监理工作台</el-breadcrumb-item>
        <el-breadcrumb-item>影像归档</el-breadcrumb-item>
      </el-breadcrumb>
      <div>
        <el-button @click="openStandard">分类标准</el-button>
        <el-button :loading="loadingMedia" @click="loadMedia">刷新照片</el-button>
      </div>
    </div>

    <p class="page-desc">
      按《工程影像资料分类标准》把水印相机照片归成 8 类，生成影像台账、资料缺口提醒，
      并同步「工程影像归档工作记录」与「项目分类标准口径」两份文书到项目资料库。
      归档采用复制方式，原图不会被移动或覆盖。
    </p>

    <el-steps :active="step" finish-status="success" align-center class="archive-steps">
      <el-step title="选择照片" description="勾选本批待归档影像" />
      <el-step title="预览与改口径" description="核对归类、台账与缺口" />
      <el-step title="落盘计划" description="确认目录与文件名" />
      <el-step title="归档完成" description="台账与两份文书" />
    </el-steps>

    <!-- ---------- 步骤 1：选择照片 ---------- -->
    <el-card v-if="step === 0" shadow="never">
      <template #header>
        <div class="card-header">
          <span class="section-title">本项目的现场影像（{{ mediaList.length }}）</span>
          <span class="hint">勾选顺序即归档序号；归类依据为上传时识别的水印字段</span>
        </div>
      </template>
      <el-table
        ref="mediaTableRef"
        :data="mediaList"
        v-loading="loadingMedia"
        size="small"
        stripe
        row-key="id"
        height="440"
        empty-text="暂无影像，请先在小程序或现场识别端上传水印照片"
        @selection-change="onSelectionChange"
      >
        <el-table-column type="selection" width="46" reserve-selection />
        <el-table-column label="预览" width="92">
          <template #default="{ row }">
            <img v-if="thumbUrls[row.id]" :src="thumbUrls[row.id]" class="thumb" alt="" />
            <div v-else class="thumb thumb-empty">加载中</div>
          </template>
        </el-table-column>
        <el-table-column prop="id" label="ID" width="70" />
        <el-table-column prop="created_at" label="上传时间" width="175">
          <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column prop="source" label="来源" width="110" show-overflow-tooltip />
        <el-table-column prop="url" label="文件" min-width="220" show-overflow-tooltip />
        <el-table-column label="大小" width="90">
          <template #default="{ row }">{{ formatSize(row.size_bytes) }}</template>
        </el-table-column>
      </el-table>
      <div class="footer-bar">
        <span class="hint">已选 {{ selectedIds.length }} 张</span>
        <el-button type="primary" :loading="previewing" :disabled="!selectedIds.length" @click="runPreview(false)">
          生成归档预览
        </el-button>
      </div>
    </el-card>

    <!-- ---------- 步骤 2：预览与改口径 ---------- -->
    <template v-if="step === 1 && batch">
      <el-row :gutter="12" class="stat-row">
        <el-col :span="5"><div class="stat-card"><div class="stat-num">{{ batch.summary?.total ?? 0 }}</div><div class="stat-label">本批影像</div></div></el-col>
        <el-col :span="5"><div class="stat-card"><div class="stat-num">{{ Object.keys(batch.summary?.by_category || {}).length }}</div><div class="stat-label">涉及类别</div></div></el-col>
        <el-col :span="5"><div class="stat-card warn"><div class="stat-num">{{ batch.summary?.needs_review ?? 0 }}</div><div class="stat-label">待确认 / 待补编号</div></div></el-col>
        <el-col :span="5"><div class="stat-card warn"><div class="stat-num">{{ (batch.gaps?.missing || []).length }}</div><div class="stat-label">资料缺口</div></div></el-col>
        <el-col :span="4"><div class="stat-card"><div class="stat-num">{{ (batch.documents || []).length }}</div><div class="stat-label">将更新文书</div></div></el-col>
      </el-row>

      <el-card shadow="never">
        <el-tabs v-model="activeTab">
          <el-tab-pane label="归档结果" name="records">
            <el-table :data="batch.records || []" size="small" stripe max-height="430">
              <el-table-column prop="序号" label="序号" width="62" />
              <el-table-column prop="时间" label="时间" width="72" />
              <el-table-column prop="单位工程" label="单位工程" width="120" show-overflow-tooltip />
              <el-table-column prop="部位" label="部位" width="120" show-overflow-tooltip />
              <el-table-column prop="施工内容" label="施工内容" min-width="170" show-overflow-tooltip />
              <el-table-column prop="归类" label="归类" width="150" show-overflow-tooltip />
              <el-table-column prop="依据" label="判定依据" min-width="210" show-overflow-tooltip />
              <el-table-column prop="文件名" label="归档文件名" min-width="200" show-overflow-tooltip />
              <el-table-column label="状态" width="110">
                <template #default="{ row }">
                  <el-tag :type="row.状态 === 'confirmed' ? 'success' : 'warning'" size="small">
                    {{ row.状态 === 'confirmed' ? '已确认' : '待复核' }}
                  </el-tag>
                </template>
              </el-table-column>
              <el-table-column label="操作" width="94" fixed="right">
                <template #default="{ row }">
                  <el-button size="small" link type="primary" @click="openOverride(row)">改口径</el-button>
                </template>
              </el-table-column>
            </el-table>
          </el-tab-pane>

          <el-tab-pane label="影像台账" name="ledger">
            <el-table :data="batch.ledger_rows || []" size="small" stripe max-height="430">
              <el-table-column
                v-for="field in ledgerFields"
                :key="field"
                :prop="field"
                :label="field"
                min-width="130"
                show-overflow-tooltip
              />
            </el-table>
          </el-tab-pane>

          <el-tab-pane :label="`资料缺口 (${(batch.gaps?.missing || []).length})`" name="gaps">
            <el-empty v-if="!(batch.gaps?.missing || []).length" description="本批影像覆盖全部关键类别，暂无缺口" />
            <el-table v-else :data="gapRows" size="small" stripe>
              <el-table-column prop="code" label="缺失类别" width="120" />
              <el-table-column prop="name" label="名称" width="160" />
              <el-table-column prop="suggestion" label="补拍 / 补资料建议" min-width="380" />
            </el-table>
            <p class="hint" style="margin-top: 12px">已归类别统计：{{ categorySummary }}</p>
          </el-tab-pane>

          <el-tab-pane :label="`待确认 (${(batch.confirmations || []).length})`" name="confirmations">
            <el-table :data="batch.confirmations || []" size="small" stripe max-height="430" empty-text="无需人工确认">
              <el-table-column prop="序号" label="序号" width="70" />
              <el-table-column prop="时间" label="时间" width="80" />
              <el-table-column prop="部位" label="部位" width="150" show-overflow-tooltip />
              <el-table-column prop="工序/对象" label="工序 / 对象" min-width="180" show-overflow-tooltip />
              <el-table-column prop="待办" label="待办事项" min-width="320" show-overflow-tooltip />
            </el-table>
          </el-tab-pane>

          <el-tab-pane label="文书预览" name="documents">
            <el-row :gutter="12">
              <el-col v-for="doc in batch.documents || []" :key="doc.doc_type" :span="12">
                <el-card shadow="never" class="doc-card">
                  <div class="doc-title">{{ doc.title }}</div>
                  <div class="hint">编号 {{ doc.doc_number }} · 将生成第 {{ doc.next_revision }} 版</div>
                  <el-button size="small" type="primary" plain style="margin-top: 12px" @click="openDocPreview(doc)">
                    查看正文（Markdown）
                  </el-button>
                </el-card>
              </el-col>
              <el-col v-if="!(batch.documents || []).length" :span="24">
                <el-empty description="本次未开启文档生成" />
              </el-col>
            </el-row>
          </el-tab-pane>
        </el-tabs>
      </el-card>

      <div class="footer-bar">
        <el-button @click="step = 0">返回重选</el-button>
        <el-button type="primary" :loading="planning" @click="runPlan">下一步：生成落盘计划</el-button>
      </div>
    </template>

    <!-- ---------- 步骤 3：落盘计划 ---------- -->
    <template v-if="step === 2">
      <el-card shadow="never" style="margin-bottom: 12px">
        <template #header><span class="section-title">落盘计划（{{ plan.length }} 张，仅复制不移动）</span></template>
        <el-table :data="plan" size="small" stripe max-height="330">
          <el-table-column prop="序号" label="序号" width="62" />
          <el-table-column prop="media_id" label="媒体 ID" width="90" />
          <el-table-column prop="目标目录" label="目标目录" min-width="220" show-overflow-tooltip />
          <el-table-column prop="文件名" label="归档文件名" min-width="230" show-overflow-tooltip />
          <el-table-column prop="源路径" label="源路径" min-width="220" show-overflow-tooltip />
        </el-table>
      </el-card>

      <el-card shadow="never">
        <template #header><span class="section-title">归档口径确认</span></template>
        <el-form label-width="150px" size="small">
          <el-form-item label="生成两份文书">
            <el-switch v-model="generateDocs" />
            <span class="hint" style="margin-left: 12px">工作记录 + 项目分类标准口径，同步进项目资料库 KB</span>
          </el-form-item>
          <el-form-item label="本次口径修正说明">
            <el-input v-model="standardNote" type="textarea" :rows="2" maxlength="300" show-word-limit
                      placeholder="例如：经监理部确认，沟槽开挖不实施旁站，按 X2 危大工程巡视归类" />
          </el-form-item>
          <el-form-item label="当前生效排除项">
            <span class="hint">{{ (standard?.project_rules?.effective_excluded || []).join('、') || '无' }}</span>
          </el-form-item>
          <el-form-item label="调整项目口径">
            <div style="width: 100%">
              <el-checkbox v-model="adjustRules">本次调整并持久生效（对后续批次继续有效）</el-checkbox>
              <el-input v-if="adjustRules" v-model="exclusionsText" style="margin-top: 8px"
                        placeholder="多个关键词用逗号分隔；留空表示清空排除项" />
            </div>
          </el-form-item>
        </el-form>
      </el-card>

      <div class="footer-bar">
        <el-button @click="step = 1">返回修改</el-button>
        <el-button type="success" :loading="committing" @click="runCommit">确认执行归档</el-button>
      </div>
    </template>

    <!-- ---------- 步骤 4：归档结果 ---------- -->
    <template v-if="step === 3 && result">
      <el-alert type="success" :closable="false" show-icon title="归档完成"
                :description="`已复制 ${result.copy_results?.length || 0} 张影像，台账 ${result.ledger?.rows ?? 0} 行；原图保持不变。`" />
      <el-alert v-if="kbFailures.length" type="warning" :closable="false" show-icon
                style="margin-top: 8px" title="部分文书未同步进项目资料库"
                :description="`${kbFailures.map(d => d.title || '文书').join('、')}：${kbFailures.map(d => d.kb_sync_error || d.error || '未知原因').join('；')}。项目资料库要求项目至少有一名在岗成员，请到「项目管理 → 分配人员」补齐后重新归档一次（照片与台账不受影响）。`" />

      <el-card shadow="never" style="margin-top: 12px">
        <template #header><span class="section-title">影像台账与四类联动结果</span></template>
        <el-descriptions :column="2" border size="small">
          <el-descriptions-item label="台账文件">{{ result.ledger?.public_path || '-' }}</el-descriptions-item>
          <el-descriptions-item label="归档根目录">{{ result.archive_root || '-' }}</el-descriptions-item>
          <el-descriptions-item label="台账行数">{{ result.ledger?.rows ?? '-' }}</el-descriptions-item>
          <el-descriptions-item label="口径文件版本">
            {{ result.standard_rules?.revision ?? standard?.project_rules?.revision ?? 0 }}
          </el-descriptions-item>
        </el-descriptions>
        <el-button v-if="result.ledger?.public_path" size="small" type="primary" plain
                   style="margin-top: 12px" @click="downloadLedger">
          下载影像台账 CSV
        </el-button>
      </el-card>

      <el-card shadow="never" style="margin-top: 12px">
        <template #header><span class="section-title">生成的文书（项目资料库 KB）</span></template>
        <el-table :data="result.documents || []" size="small" stripe empty-text="本次未生成文书">
          <el-table-column prop="title" label="文书" min-width="220" show-overflow-tooltip />
          <el-table-column prop="revision" label="版本" width="90">
            <template #default="{ row }">R{{ row.revision || 1 }}</template>
          </el-table-column>
          <el-table-column prop="status" label="生成状态" width="110">
            <template #default="{ row }">
              <el-tag :type="row.status === 'generated' ? 'success' : 'danger'" size="small">
                {{ row.status === 'generated' ? '已生成' : '失败' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="kb_sync_status" label="资料库同步" width="130">
            <template #default="{ row }">
              <el-tag v-if="row.kb_sync_status"
                      :type="row.kb_sync_status === 'synced' ? 'success' : (row.kb_sync_status === 'failed' ? 'danger' : 'warning')"
                      size="small">
                {{ kbLabel(row.kb_sync_status) }}
              </el-tag>
              <span v-else class="hint">-</span>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="130" fixed="right">
            <template #default="{ row }">
              <el-button v-if="row.pdf_url" size="small" link type="primary" @click="openPdf(row)">打开 PDF</el-button>
              <span v-else class="hint">{{ row.error || row.kb_sync_error || '-' }}</span>
            </template>
          </el-table-column>
        </el-table>
      </el-card>

      <div class="footer-bar">
        <el-button type="primary" @click="resetAll">再归档一批</el-button>
      </div>
    </template>

    <!-- ---------- 改口径弹窗 ---------- -->
    <el-dialog v-model="overrideVisible" :title="`调整第 ${overrideForm['序号'] || ''} 张照片的归类口径`" width="560px">
      <el-form label-width="120px" size="small">
        <el-form-item label="类别码">
          <el-select v-model="overrideForm.category_code" placeholder="不改则留空" clearable style="width: 100%">
            <el-option v-for="c in standard?.categories || []" :key="c.code"
                       :label="`${c.code} ${c.name}`" :value="c.code" />
          </el-select>
        </el-form-item>
        <el-form-item label="二级类（巡视）">
          <el-select v-model="overrideForm.subcategory" placeholder="仅对 X 巡视有效" clearable
                     :disabled="overrideForm.category_code && overrideForm.category_code !== 'X'"
                     style="width: 100%">
            <el-option v-for="s in standard?.x_subcategories || []" :key="s.code"
                       :label="`${s.code} ${s.name}`" :value="s.code" />
          </el-select>
        </el-form-item>
        <el-form-item label="关联资料编号">
          <el-input v-model="overrideForm.related_doc_no" placeholder="补齐书面资料编号后可转为正式归档" />
        </el-form-item>
        <el-form-item label="补充标签">
          <el-select v-model="overrideForm.labels" multiple filterable allow-create default-first-option
                     placeholder="可自定义，回车确认" style="width: 100%">
            <el-option v-for="t in standard?.labels || []" :key="t" :label="t" :value="t" />
          </el-select>
        </el-form-item>
        <el-form-item label="口径说明">
          <el-input v-model="overrideForm.note" type="textarea" :rows="3"
                    placeholder="写入工作记录与分类标准文档，便于追溯" />
        </el-form-item>
      </el-form>
      <div class="hint" style="margin-bottom: 8px">
        保存后会重新计算整批：归档结果、文件名、台账、统计与资料缺口一起联动。
      </div>
      <template #footer>
        <el-button v-if="overrides[overrideForm['序号']]" type="danger" plain @click="clearOverride">撤销人工口径</el-button>
        <el-button @click="overrideVisible = false">取消</el-button>
        <el-button type="primary" :loading="previewing" @click="saveOverride">保存并重算</el-button>
      </template>
    </el-dialog>

    <!-- ---------- 文书正文预览 ---------- -->
    <el-dialog v-model="docPreviewVisible" :title="docPreviewTitle" width="820px" top="6vh">
      <pre class="md-preview">{{ docPreviewMarkdown }}</pre>
    </el-dialog>

    <!-- ---------- 分类标准 ---------- -->
    <el-dialog v-model="standardVisible" title="工程影像资料分类标准" width="820px" top="6vh">
      <el-tabs v-model="standardTab">
        <el-tab-pane label="8 类归档目录" name="cats">
          <el-table :data="standard?.categories || []" size="small" stripe>
            <el-table-column prop="code" label="类别码" width="90" />
            <el-table-column prop="name" label="名称" width="150" />
            <el-table-column prop="archive_dir" label="归档目录" width="190" />
            <el-table-column prop="definition" label="定义" min-width="260" show-overflow-tooltip />
          </el-table>
        </el-tab-pane>
        <el-tab-pane label="巡视二级类" name="subs">
          <el-table :data="standard?.x_subcategories || []" size="small" stripe>
            <el-table-column prop="code" label="代码" width="90" />
            <el-table-column prop="name" label="名称" width="180" />
            <el-table-column prop="typical" label="典型场景" min-width="320" show-overflow-tooltip />
          </el-table>
        </el-tab-pane>
        <el-tab-pane label="旁站清单 (13)" name="pangzhan">
          <el-table :data="standard?.pangzhan_list || []" size="small" stripe max-height="420">
            <el-table-column prop="序号" label="序号" width="70" />
            <el-table-column prop="工序" label="工序" width="220" />
            <el-table-column prop="主要控制点" label="主要控制点" min-width="320" show-overflow-tooltip />
          </el-table>
        </el-tab-pane>
        <el-tab-pane label="本项目口径" name="rules">
          <p><strong>生效排除项：</strong>{{ (standard?.project_rules?.effective_excluded || []).join('、') || '无' }}</p>
          <p><strong>口径版本：</strong>R{{ standard?.project_rules?.revision ?? 0 }}</p>
          <p class="hint">{{ standard?.project_rules?.note || '' }}</p>
          <p class="hint">命中排除项的施工内容不按 P 旁站归类，改按 X2 危大工程巡视处理；标准原条目仍保留在清单中。</p>
        </el-tab-pane>
      </el-tabs>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed, onMounted, onUnmounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { ensureWritableProject } from '@/composables/useAllProjectsGuard'
import { imageArchiveApi, downloadFile, viewFile } from '@/api'

const step = ref(0)
const activeTab = ref('records')
const standardTab = ref('cats')

const mediaList = ref<any[]>([])
const thumbUrls = ref<Record<number, string>>({})
const selectedIds = ref<number[]>([])
const mediaTableRef = ref<any>(null)
const loadingMedia = ref(false)

const standard = ref<any>(null)
const standardVisible = ref(false)
const batch = ref<any>(null)
const previewing = ref(false)
const planning = ref(false)
const committing = ref(false)
const plan = ref<any[]>([])
const result = ref<any>(null)

const generateDocs = ref(true)
const standardNote = ref('')
const adjustRules = ref(false)
const exclusionsText = ref('')

const overrides = ref<Record<string, any>>({})
const overrideVisible = ref(false)
const overrideForm = reactive<any>({ '序号': 0, category_code: '', subcategory: '', related_doc_no: '', labels: [], note: '' })

const docPreviewVisible = ref(false)
const docPreviewTitle = ref('')
const docPreviewMarkdown = ref('')

const ledgerFields = computed<string[]>(() => batch.value?.ledger_fields || [])
const categoryNames: Record<string, string> = {
  P: '旁站', W: '见证取样', Y: '隐蔽验收', M: '材料/设备进场',
  X: '巡视', H: '隐患整改', C: '检查/会议', O: '其他',
}
const gapRows = computed(() =>
  (batch.value?.gaps?.missing || []).map((code: string) => ({
    code,
    name: categoryNames[code] || code,
    suggestion: batch.value?.gaps?.suggestions?.[code] || '按标准补拍或补齐对应书面资料',
  })),
)
const categorySummary = computed(() => {
  const by = batch.value?.summary?.by_category || {}
  return Object.entries(by).map(([k, v]) => `${k}(${categoryNames[k] || ''}) ${v} 张`).join('，') || '无'
})
const kbFailures = computed<any[]>(() =>
  (result.value?.documents || []).filter((d: any) => d.kb_sync_status !== 'synced'),
)

function kbLabel(status?: string) {
  return ({ synced: '已同步', failed: '失败', pending: '待同步' } as Record<string, string>)[status || ''] || status || '-'
}

function formatSize(bytes?: number) {
  if (!bytes) return '-'
  if (bytes < 1024) return bytes + 'B'
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + 'KB'
  return (bytes / 1024 / 1024).toFixed(1) + 'MB'
}

function formatTime(value?: string) {
  return value ? String(value).replace('T', ' ').slice(0, 19) : '-'
}

function errText(e: any, fallback: string) {
  const d = e?.response?.data?.detail
  return (Array.isArray(d) ? d.map((x: any) => x.msg || x).join('；') : d) || e?.message || fallback
}

async function loadStandard() {
  try {
    standard.value = await imageArchiveApi.standard()
    exclusionsText.value = (standard.value?.project_rules?.effective_excluded || []).join('，')
  } catch {
    standard.value = null
  }
}

async function hydrateThumbs(rows: any[]) {
  const token = localStorage.getItem('token')
  for (let i = 0; i < rows.length; i += 6) {
    await Promise.all(rows.slice(i, i + 6).map(async (row) => {
      if (!row.url || thumbUrls.value[row.id]) return
      try {
        const res = await fetch(row.url, {
          headers: token ? { Authorization: `Bearer ${token}` } : {},
        })
        if (!res.ok) return
        thumbUrls.value[row.id] = URL.createObjectURL(await res.blob())
      } catch { /* 单张缩略图失败不影响选择 */ }
    }))
  }
}

async function loadMedia() {
  loadingMedia.value = true
  try {
    const res: any = await imageArchiveApi.listImages(100)
    mediaList.value = res?.items || []
    hydrateThumbs(mediaList.value.slice(0, 60))
  } catch (e: any) {
    ElMessage.error(errText(e, '加载影像列表失败'))
    mediaList.value = []
  } finally {
    loadingMedia.value = false
  }
}

function onSelectionChange(rows: any[]) {
  selectedIds.value = rows.map((r) => r.id)
}

function buildPayload(dryRun: boolean) {
  const ov = Object.entries(overrides.value).map(([k, v]) => ({ '序号': Number(k), ...v }))
  const payload: any = {
    items: selectedIds.value.map((id) => ({ media_id: id })),
    overrides: ov,
    dry_run: dryRun,
    generate_documents: generateDocs.value,
  }
  if (standardNote.value.trim()) payload.standard_note = standardNote.value.trim()
  if (adjustRules.value) {
    payload.pangzhan_exclusions = exclusionsText.value
      .split(/[,，、\s]+/).map((s) => s.trim()).filter(Boolean)
  }
  return payload
}

async function runPreview(silent: boolean) {
  if (!selectedIds.value.length) return ElMessage.warning('请先勾选待归档照片')
  if (!(await ensureWritableProject('预览影像归档'))) return
  previewing.value = true
  try {
    const res: any = await imageArchiveApi.preview(buildPayload(true))
    batch.value = res
    if (!silent) {
      step.value = 1
      ElMessage.success(`归档预览完成：${res.summary?.total ?? 0} 张，${res.summary?.needs_review ?? 0} 项待确认`)
    }
  } catch (e: any) {
    ElMessage.error(errText(e, '归档预览失败'))
  } finally {
    previewing.value = false
  }
}

async function runPlan() {
  if (!(await ensureWritableProject('生成影像归档计划'))) return
  planning.value = true
  try {
    const res: any = await imageArchiveApi.commit(buildPayload(true))
    plan.value = res?.plan || []
    step.value = 2
  } catch (e: any) {
    ElMessage.error(errText(e, '生成落盘计划失败'))
  } finally {
    planning.value = false
  }
}

async function runCommit() {
  try {
    await ElMessageBox.confirm(
      '将复制照片到标准目录、写入影像台账，并更新两份文书（重复归档会出 R2 新版）。原图不会被移动或覆盖，确认执行？',
      '确认执行归档', { type: 'warning', confirmButtonText: '确认归档', cancelButtonText: '再看看' },
    )
  } catch { return }
  if (!(await ensureWritableProject('确认归档影像'))) return
  committing.value = true
  try {
    const res: any = await imageArchiveApi.commit(buildPayload(false))
    result.value = res
    step.value = 3
    ElMessage.success('归档完成，台账与两份文书已联动更新')
    loadStandard()
  } catch (e: any) {
    ElMessage.error(errText(e, '归档执行失败'))
  } finally {
    committing.value = false
  }
}

function openOverride(row: any) {
  const no = row['序号']
  const saved = overrides.value[no] || {}
  Object.assign(overrideForm, {
    '序号': no,
    category_code: saved.category_code || '',
    subcategory: saved.subcategory || '',
    related_doc_no: saved.related_doc_no || '',
    labels: saved.labels ? [...saved.labels] : [],
    note: saved.note || '',
  })
  overrideVisible.value = true
}

async function saveOverride() {
  const no = Number(overrideForm['序号'])
  const payload: any = {}
  if (overrideForm.category_code) payload.category_code = overrideForm.category_code
  if (overrideForm.subcategory) payload.subcategory = overrideForm.subcategory
  if (overrideForm.related_doc_no) payload.related_doc_no = overrideForm.related_doc_no
  if (overrideForm.labels?.length) payload.labels = [...overrideForm.labels]
  if (overrideForm.note) payload.note = overrideForm.note
  if (!Object.keys(payload).length) return ElMessage.warning('请至少填写一项调整内容')
  overrides.value[no] = payload
  overrideVisible.value = false
  await runPreview(true)
}

async function clearOverride() {
  delete overrides.value[Number(overrideForm['序号'])]
  overrideVisible.value = false
  await runPreview(true)
}

function openDocPreview(doc: any) {
  docPreviewTitle.value = `${doc.title}（第 ${doc.next_revision} 版预览）`
  docPreviewMarkdown.value = doc.markdown || ''
  docPreviewVisible.value = true
}

function openStandard() {
  standardVisible.value = true
  if (!standard.value) loadStandard()
}

function downloadLedger() {
  const url = result.value?.ledger?.public_path
  if (!url) return
  downloadFile(url).catch((e: any) => ElMessage.error(errText(e, '台账下载失败')))
}

function openPdf(row: any) {
  viewFile(row.pdf_url).catch((e: any) => ElMessage.error(errText(e, 'PDF 打开失败')))
}

function resetAll() {
  step.value = 0
  batch.value = null
  plan.value = []
  result.value = null
  overrides.value = {}
  selectedIds.value = []
  standardNote.value = ''
  adjustRules.value = false
  mediaTableRef.value?.clearSelection?.()
  loadMedia()
}

onMounted(() => {
  loadStandard()
  loadMedia()
})

onUnmounted(() => {
  Object.values(thumbUrls.value).forEach((url) => URL.revokeObjectURL(url))
})
</script>

<style scoped>
.page-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 12px; }
.page-desc { color: #909399; font-size: 13px; line-height: 1.7; margin: 0 0 16px; }
.section-title { font-weight: 600; font-size: 15px; }
.card-header { display: flex; align-items: baseline; gap: 16px; }
.hint { color: #909399; font-size: 12px; }
.archive-steps { margin: 4px 0 20px; }
.thumb { width: 62px; height: 44px; object-fit: cover; border-radius: 4px; background: #f5f7fa; }
.thumb-empty { display: flex; align-items: center; justify-content: center; color: #c0c4cc; font-size: 11px; }
.footer-bar { display: flex; align-items: center; justify-content: flex-end; gap: 12px; margin-top: 16px; }
.stat-row { margin-bottom: 12px; }
.stat-card { background: #fff; border: 1px solid #ebeef5; border-radius: 6px; padding: 12px 16px; text-align: center; }
.stat-card.warn { border-color: #f3d19e; }
.stat-num { font-size: 24px; font-weight: 600; color: #303133; line-height: 1.3; }
.stat-label { color: #909399; font-size: 12px; margin-top: 2px; }
.doc-card { margin-bottom: 12px; }
.doc-title { font-weight: 600; font-size: 14px; margin-bottom: 6px; }
.md-preview { max-height: 62vh; overflow: auto; background: #f7f8fa; border: 1px solid #ebeef5;
  border-radius: 4px; padding: 16px; font-size: 12px; line-height: 1.7; white-space: pre-wrap; word-break: break-word; }
</style>
