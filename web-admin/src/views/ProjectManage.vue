<template>
  <div class="project-manage-page">
    <!-- 页头：标题在左，唯一主操作在右（原先四样控件挤在一排，2026-10-22 重排） -->
    <div class="page-head">
      <div class="page-head-text">
        <h2 class="page-title">项目管理</h2>
        <p class="page-sub">立项后需在「用户管理 → 项目成员管理」中分配人员</p>
      </div>
      <el-button v-if="canCreate" type="primary" @click="openCreate">
        <el-icon class="btn-icon"><Plus /></el-icon>
        新建项目
      </el-button>
    </div>

    <!-- 概览统计：项目数与状态分布一眼可见，不用自己在表里数 -->
    <div class="stat-row">
      <div class="stat-card">
        <div class="stat-label">项目总数</div>
        <div class="stat-value">{{ stats.total }}</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">进行中</div>
        <div class="stat-value stat-active">{{ stats.active }}</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">已结项</div>
        <div class="stat-value stat-closed">{{ stats.closed }}</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">当前所在</div>
        <div class="stat-current" :title="stats.currentName">{{ stats.currentName || '未绑定项目' }}</div>
      </div>
    </div>

    <el-card shadow="never" class="table-card">
      <!-- 工具栏：筛选/搜索/开关同排，刷新靠右 -->
      <div class="toolbar">
        <!-- 项目范围筛选（2026-10-21）：后端 GET /project/list 对超管**无条件返回全平台项目**
             （`_can_view_all_projects` = is_super_admin，不走成员归属过滤），所以切换项目后
             本页看着没变化。筛选放前端做，默认跟随顶栏当前项目。 -->
        <el-select v-model="scopeFilter" class="tb-scope">
          <el-option label="全部项目" value="all" />
          <el-option label="仅当前项目" value="current" />
        </el-select>
        <el-input
          v-model="keyword"
          class="tb-search"
          placeholder="搜索名称 / 编号 / 地点"
          clearable
        >
          <template #prefix>
            <el-icon><Search /></el-icon>
          </template>
        </el-input>
        <div class="tb-switch">
          <el-switch v-model="includeClosed" size="small" />
          <span class="tb-switch-text">含已结项</span>
        </div>
        <div class="tb-right">
          <span class="tb-count">共 {{ visibleItems.length }} 条</span>
          <el-button :icon="Refresh" circle @click="load" title="刷新" />
        </div>
      </div>

      <el-table
        :data="visibleItems"
        v-loading="loading"
        stripe
        :header-cell-style="headerCellStyle"
        row-class-name="pm-row"
      >
        <el-table-column label="项目名称" min-width="200">
          <template #default="{ row }">
            <span class="pname">{{ row.name }}</span>
            <el-tag v-if="row.id === currentProjectId" size="small" type="success" effect="light" class="cur-tag">
              当前
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="code" label="项目编号" min-width="160">
          <template #default="{ row }">
            <span class="mono">{{ row.code }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="short_name" label="简称" min-width="100">
          <template #default="{ row }">
            <span :class="row.short_name ? '' : 'dim'">{{ row.short_name || '-' }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="location" label="地点" min-width="150">
          <template #default="{ row }">
            <span :class="row.location ? 'dim' : 'dim'">{{ row.location || '-' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="状态" min-width="110">
          <template #default="{ row }">
            <el-tag :type="statusTagType(row.status)" size="small" effect="light" disable-transitions>
              {{ statusLabel(row.status) }}
            </el-tag>
            <div v-if="row.completed_at" class="form-hint">
              结项 {{ fmtDate(row.completed_at) }}
            </div>
          </template>
        </el-table-column>
        <!-- 操作列收纳：最高频动作外露，其余进「更多」下拉，宽度从 360 降到 168 -->
        <el-table-column label="操作" width="168" fixed="right" align="right">
          <template #default="{ row }">
            <div class="act-cell">
              <template v-if="primaryAction(row)">
                <span v-if="primaryAction(row)!.link" class="act-link" @click="primaryAction(row)!.onClick">
                  {{ primaryAction(row)!.label }}
                </span>
                <span v-else class="muted">{{ primaryAction(row)!.label }}</span>
              </template>
              <el-dropdown
                v-if="moreActions(row).length"
                trigger="click"
                @command="(cmd: string) => handleCommand(cmd, row)"
              >
                <span class="act-more">
                  更多<el-icon class="more-icon"><ArrowDown /></el-icon>
                </span>
                <template #dropdown>
                  <el-dropdown-menu>
                    <el-dropdown-item
                      v-for="a in moreActions(row)"
                      :key="a.key"
                      :command="a.key"
                      :divided="a.divided"
                      :class="a.danger ? 'danger-item' : ''"
                    >
                      {{ a.label }}
                    </el-dropdown-item>
                  </el-dropdown-menu>
                </template>
              </el-dropdown>
            </div>
          </template>
        </el-table-column>
      </el-table>

      <el-empty v-if="!loading && !visibleItems.length" :description="emptyText" />
    </el-card>

    <el-dialog v-model="dialogVisible" :title="editingId ? '编辑项目资料' : '新建项目'" width="600px">
      <el-form :model="form" label-width="90px">
        <el-form-item label="项目名称" required>
          <el-input v-model="form.name" placeholder="如：广东监理示范工程" maxlength="128" />
        </el-form-item>
        <el-form-item label="项目编号" required>
          <el-input v-model="form.code" placeholder="如：GDJL-002（全局唯一）" maxlength="64" />
          <span class="form-hint">创建后不建议修改，用于对外标识</span>
        </el-form-item>
        <el-form-item label="项目简称">
          <el-input v-model="form.short_name" placeholder="选填" maxlength="64" />
        </el-form-item>
        <el-form-item label="项目地点">
          <el-input v-model="form.location" placeholder="选填" maxlength="128" />
        </el-form-item>
        <el-form-item label="施工单位"><el-input v-model="form.construction_unit" maxlength="256" /></el-form-item>
        <el-form-item label="监理单位"><el-input v-model="form.supervision_unit" maxlength="256" /></el-form-item>
        <el-form-item label="项目监理机构"><el-input v-model="form.supervision_org" maxlength="256" /></el-form-item>
        <el-form-item label="验收单位人员"><el-input v-model="form.acceptance_participants" type="textarea" placeholder="默认名单；实际验收时需确认" /></el-form-item>
        <el-form-item label="项目纬度"><el-input v-model="form.latitude" placeholder="-90 至 90，用于获取气象" /></el-form-item>
        <el-form-item label="项目经度"><el-input v-model="form.longitude" placeholder="-180 至 180，用于获取气象" /></el-form-item>
        <el-form-item label="项目章"><el-select v-model="form.seal_signature_id" clearable placeholder="选择本人管理的项目章"><el-option v-for="sig in projectSignatures" :key="sig.id" :label="sig.name" :value="sig.id" /></el-select><span class="form-hint">仅配置用章主体，实际用章仍须密码授权</span></el-form-item>
        <el-form-item label="项目描述">
          <el-input
            v-model="form.description"
            type="textarea"
            :rows="3"
            placeholder="选填"
            maxlength="500"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="save">{{ editingId ? '保存' : '创建' }}</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="deleteVisible" title="删除项目" width="520px">
      <div v-if="deleting" class="del-target">
        <div class="del-name">{{ deleting.name }}</div>
        <div class="del-code">项目编号：{{ deleting.code }}</div>
      </div>
      <el-alert
        type="warning"
        :closable="false"
        show-icon
        title="此操作不可逆，将从数据库彻底移除该项目"
        description="仅「从未产生业务数据」的空壳项目可删（无成员任命、无文书/巡视/验收/资料等任何记录、无图纸与档案文件）。若项目已有数据，请改用「结项」——结项可恢复、保留责任追溯链。请输入项目编号以确认。"
        style="margin-bottom: 12px"
      />
      <el-input
        v-model="deleteCode"
        :placeholder="`请输入项目编号 ${deleting?.code || ''}`"
        maxlength="64"
        clearable
      />
      <template #footer>
        <el-button @click="deleteVisible = false">取消</el-button>
        <el-button
          type="danger"
          :loading="deleteSaving"
          :disabled="!deleting || deleteCode.trim().toUpperCase() !== (deleting.code || '').toUpperCase()"
          @click="confirmDelete"
        >确认删除</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { ArrowDown, Plus, Refresh, Search } from '@element-plus/icons-vue'
import { projectApi, signatureApi } from '@/api'
import { useAuthStore } from '@/stores/auth'

const router = useRouter()
const auth = useAuthStore()

const items = ref<any[]>([])
const loading = ref(false)
const dialogVisible = ref(false)
const saving = ref(false)
const editingId = ref<number | null>(null)
const projectSignatures = ref<any[]>([])
// Step 5：默认只显示进行中项目；打开开关后连已结项（完工/归档）一并显示。
// 2026-10-22 起改为**始终拉全量**（include_closed=true）再前端过滤，
// 否则统计条拿不到已结项数量。
const includeClosed = ref(false)
// 名称/编号/简称/地点模糊搜索（纯前端，30 个项目里找比翻页快）
const keyword = ref('')

// 仅超管可立项（与后端 project_create 权限同口径）
const canCreate = computed(() => auth.hasPermission('project_create'))
// Step 5：结项/恢复（权限 project_complete，默认仅超管可配）
const canComplete = computed(() => auth.hasPermission('project_complete'))
// 物理删除只给超管（与后端 `_require_super` 同口径，不走权限键）
const isSuperAdmin = computed(() => String(auth.userInfo?.org_role || '') === 'super_admin')
const currentProjectId = computed(() => auth.userInfo?.project_id ?? null)
const canEdit = computed(() => auth.hasPermission('project_edit'))

const form = reactive({
  name: '',
  code: '',
  short_name: '',
  location: '',
  description: '',
  construction_unit: '', supervision_unit: '', supervision_org: '', acceptance_participants: '', latitude: '', longitude: '', seal_signature_id: null as number | null,
})

async function load() {
  loading.value = true
  try {
    // 恒定 include_closed=true：已结项项目也要进内存，供统计条计数；
    // 是否展示由下方 visibleItems 按 includeClosed 开关过滤。
    const res: any = await projectApi.list(true)
    items.value = Array.isArray(res) ? res : res?.items || []
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || '加载项目失败')
  } finally {
    loading.value = false
  }
}

// ---- 概览统计（2026-10-22）----
const stats = computed(() => {
  const all = items.value
  const active = all.filter((p) => (p.status || 'active') === 'active').length
  const cur = all.find((p) => p.id === currentProjectId.value)
  return {
    total: all.length,
    active,
    closed: all.length - active,
    currentName: cur?.name || '',
  }
})

// ---- 项目范围筛选（2026-10-21，纯前端）----
// 超管在 `GET /project/list` 上走的是「不加 where」分支，返回全平台项目，
// 与顶栏选中的项目无关。这里默认只看当前项目，需要纵览时切「全部项目」。
const scopeFilter = ref<'current' | 'all'>('all')

const visibleItems = computed(() => {
  // 当前项目置顶，其余按原顺序（后端已按创建时间倒序）
  let sorted = [...items.value].sort((a, b) => {
    const ca = a.id === currentProjectId.value ? 0 : 1
    const cb = b.id === currentProjectId.value ? 0 : 1
    return ca - cb
  })
  if (scopeFilter.value === 'current') {
    if (!currentProjectId.value) return []
    sorted = sorted.filter((row) => row.id === currentProjectId.value)
  }
  if (!includeClosed.value) {
    sorted = sorted.filter((row) => (row.status || 'active') === 'active')
  }
  const kw = keyword.value.trim().toLowerCase()
  if (kw) {
    sorted = sorted.filter((row) =>
      [row.name, row.code, row.short_name, row.location]
        .some((v) => String(v || '').toLowerCase().includes(kw)),
    )
  }
  return sorted
})

const emptyText = computed(() => {
  if (keyword.value.trim()) return `没有匹配「${keyword.value.trim()}」的项目`
  if (scopeFilter.value === 'current' && !currentProjectId.value) {
    return '当前账号未绑定项目，请切到「全部项目」查看'
  }
  if (!includeClosed.value && items.value.length) return '没有进行中的项目，可打开「含已结项」查看历史项目'
  return '暂无项目'
})

// 顶栏切换项目 / 视角变化后，列表筛选跟随：
// 选中具体项目 → 只看该项目；「全部项目」视角或未绑定项目 → 看全部
watch(
  () => [auth.currentProjectId, auth.allProjects] as const,
  ([pid, all]) => { scopeFilter.value = all || !pid ? 'all' : 'current' },
  { immediate: true },
)

// ---- 表格视觉（A 深蓝精修）----
const headerCellStyle = {
  background: '#FAFBFC',
  color: '#606266',
  fontWeight: 600,
  fontSize: '13px',
  borderBottom: '1px solid #EBEEF5',
}

// ---- 操作列：主操作外露 + 其余收纳进下拉 ----
interface RowAction {
  key: string
  label: string
  link?: boolean
  danger?: boolean
  divided?: boolean
  onClick?: () => void
}

/** 外露的那一个动作：进行中→切换（当前项目显示"使用中"），已结项→恢复。 */
function primaryAction(row: any): RowAction | null {
  const active = (row.status || 'active') === 'active'
  if (active) {
    if (row.id === currentProjectId.value) return { key: 'current', label: '使用中' }
    return { key: 'switch', label: '切换', link: true, onClick: () => switchTo(row) }
  }
  if (canComplete.value) return { key: 'reopen', label: '恢复', link: true, onClick: () => reopenProject(row) }
  return { key: 'closed', label: statusLabel(row.status) }
}

/** 下拉里的其余动作（按权限与状态裁剪）。 */
function moreActions(row: any): RowAction[] {
  const out: RowAction[] = []
  const active = (row.status || 'active') === 'active'
  if (canEdit.value) out.push({ key: 'edit', label: '项目资料' })
  out.push({ key: 'members', label: '分配人员' })
  if (canComplete.value && active) out.push({ key: 'complete', label: '结项' })
  if (isSuperAdmin.value && row.id !== currentProjectId.value) {
    out.push({ key: 'delete', label: '删除项目', danger: true, divided: true })
  }
  return out
}

function handleCommand(cmd: string, row: any) {
  if (cmd === 'edit') return openEdit(row)
  if (cmd === 'members') return goMembers(row)
  if (cmd === 'complete') return completeProject(row)
  if (cmd === 'delete') return openDelete(row)
}

async function openEdit(row: any) {
  editingId.value = row.id
  for (const key of Object.keys(form)) (form as any)[key] = row[key] ?? (key === 'seal_signature_id' ? null : '')
  projectSignatures.value = await signatureApi.list() as any
  dialogVisible.value = true
}

function openCreate() {
  editingId.value = null
  for (const key of Object.keys(form)) (form as any)[key] = key === 'seal_signature_id' ? null : ''
  signatureApi.list().then((res: any) => { projectSignatures.value = res }).catch(() => { projectSignatures.value = [] })
  if (!canCreate.value) {
    ElMessage.warning('需要「立项」权限，请联系超级管理员')
    return
  }
  form.name = ''
  form.code = ''
  form.short_name = ''
  form.location = ''
  form.description = ''
  dialogVisible.value = true
}

async function save() {
  if (!form.name.trim()) {
    ElMessage.warning('请输入项目名称')
    return
  }
  if (!form.code.trim()) {
    ElMessage.warning('请输入项目编号')
    return
  }
  saving.value = true
  try {
    const payload = {
      name: form.name.trim(),
      code: form.code.trim(),
      short_name: form.short_name.trim() || null,
      location: form.location.trim() || null,
      description: form.description.trim() || null,
      construction_unit: form.construction_unit.trim() || null,
      supervision_unit: form.supervision_unit.trim() || null,
      supervision_org: form.supervision_org.trim() || null,
      acceptance_participants: form.acceptance_participants.trim() || null,
      latitude: form.latitude.trim() || null, longitude: form.longitude.trim() || null,
      seal_signature_id: form.seal_signature_id,
    }
    const res: any = editingId.value ? await projectApi.update(editingId.value, payload) : await projectApi.create(payload)
    ElMessage.success(`项目资料已保存：${res?.name || ''}`)
    dialogVisible.value = false
    await load()
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || '创建失败')
  } finally {
    saving.value = false
  }
}

async function switchTo(row: any) {
  try {
    await projectApi.switch(row.id)
    ElMessage.success(`已切换到「${row.name}」，刷新后生效`)
    await auth.fetchUserInfo()
    await load()
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || '切换失败')
  }
}

function goMembers(row: any) {
  // 成员管理在「用户管理」页；直接带上项目 id 跳转，落地即锁定该项目的账号筛选，
  // 免得超管在全量账号里自己找（2026-10-21 改：不再弹提示框）。
  router.push({ path: '/user-manage', query: { project_id: String(row.id) } })
}

// ---- Step 5：项目生命周期（结项 / 恢复）----

const PROJECT_STATUS_LABELS: Record<string, string> = {
  active: '进行中',
  completed: '已完工',
  archived: '已归档',
}

function statusLabel(status?: string) {
  return PROJECT_STATUS_LABELS[status || 'active'] || status || '进行中'
}

function statusTagType(status?: string): 'success' | 'info' | 'warning' {
  if (status === 'completed') return 'info'
  if (status === 'archived') return 'warning'
  return 'success'
}

function fmtDate(value?: string | null) {
  if (!value) return ''
  return value.replace('T', ' ').slice(0, 16)
}

async function completeProject(row: any) {
  try {
    await ElMessageBox.confirm(
      `确认对「${row.name}」结项？\n`
        + '结项后：项目置为「已完工」；所有在岗成员将被置为离场（在场记录保留）；'
        + '项目从各方项目列表中收口。误操作可由超管在「显示已结项项目」中恢复。',
      '项目结项',
      { type: 'warning', confirmButtonText: '确认结项', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  try {
    const res: any = await projectApi.complete(row.id)
    ElMessage.success(res?.detail || '项目已结项')
    await load()
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || '结项失败')
  }
}

async function reopenProject(row: any) {
  try {
    await ElMessageBox.confirm(
      `确认将「${row.name}」恢复为进行中？\n`
        + '仅恢复项目状态；原成员结项时已被置离场，需在「分配人员」中重新任命。',
      '恢复项目',
      { type: 'info', confirmButtonText: '确认恢复', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  try {
    const res: any = await projectApi.reopen(row.id)
    ElMessage.success(res?.detail || '项目已恢复为进行中')
    await load()
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || '恢复失败')
  }
}

// ---- 空壳项目物理删除（2026-10-22，仅超管；门槛见后端 delete_project）----

const deleteVisible = ref(false)
const deleteSaving = ref(false)
const deleting = ref<any | null>(null)
const deleteCode = ref('')

function openDelete(row: any) {
  deleting.value = row
  deleteCode.value = ''
  deleteVisible.value = true
}

async function confirmDelete() {
  if (!deleting.value) return
  deleteSaving.value = true
  try {
    const res: any = await projectApi.remove(deleting.value.id, deleteCode.value.trim())
    ElMessage.success(res?.detail || '项目已删除')
    deleteVisible.value = false
    deleting.value = null
    await load()
  } catch (e: any) {
    // 后端对"有业务数据"返回 409，detail 已说明原因，直接透出
    ElMessage.error(e?.response?.data?.detail || '删除失败')
  } finally {
    deleteSaving.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.project-manage-page {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

/* ---- 页头 ---- */
.page-head {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 16px;
}
.page-title {
  margin: 0;
  font-size: 18px;
  font-weight: 600;
  color: #1f2329;
  line-height: 1.4;
}
.page-sub {
  margin: 4px 0 0;
  font-size: 13px;
  color: var(--el-text-color-secondary);
  line-height: 1.5;
}
.btn-icon {
  margin-right: 4px;
}

/* ---- 概览统计 ---- */
.stat-row {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
}
.stat-card {
  background: #fff;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
  padding: 14px 16px;
}
.stat-label {
  font-size: 13px;
  color: var(--el-text-color-secondary);
}
.stat-value {
  margin-top: 6px;
  font-size: 26px;
  font-weight: 600;
  line-height: 1.2;
  color: #1f2329;
}
.stat-active { color: #1d7a45; }
.stat-closed { color: var(--el-text-color-secondary); }
.stat-current {
  margin-top: 10px;
  font-size: 14px;
  font-weight: 600;
  color: #1f2329;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* ---- 表格卡片与工具栏 ---- */
.table-card {
  border-radius: 8px;
}
.toolbar {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 14px;
  flex-wrap: wrap;
}
.tb-scope { width: 140px; }
.tb-search { width: 240px; }
.tb-switch {
  display: flex;
  align-items: center;
  gap: 6px;
}
.tb-switch-text {
  font-size: 13px;
  color: var(--el-text-color-regular);
  white-space: nowrap;
}
.tb-right {
  margin-left: auto;
  display: flex;
  align-items: center;
  gap: 10px;
}
.tb-count {
  font-size: 13px;
  color: var(--el-text-color-secondary);
}

/* ---- 单元格 ---- */
.pname {
  font-weight: 600;
  color: #1f2329;
}
.cur-tag {
  margin-left: 8px;
}
.mono {
  font-family: 'SF Mono', Consolas, Menlo, monospace;
  font-size: 13px;
  color: var(--el-text-color-regular);
}
.dim {
  color: var(--el-text-color-secondary);
}
.muted {
  color: var(--el-text-color-placeholder);
  font-size: 13px;
}
.form-hint {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  line-height: 1.6;
  margin-top: 4px;
}

/* ---- 操作列 ---- */
.act-cell {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 4px;
}
.act-link {
  color: var(--el-color-primary);
  cursor: pointer;
  font-size: 13px;
}
.act-link:hover { text-decoration: underline; }
.act-more {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  color: var(--el-color-primary);
  cursor: pointer;
  font-size: 13px;
  outline: none;
}
.act-more:hover { text-decoration: underline; }
.more-icon { font-size: 12px; }

/* ---- 表格行悬停 ---- */
:deep(.pm-row:hover > td.el-table__cell) {
  background: #f5f8ff !important;
}

/* ---- 删除弹窗 ---- */
.del-target {
  padding: 10px 12px;
  margin-bottom: 12px;
  border-radius: 4px;
  background: var(--el-fill-color-lighter);
}
.del-name {
  font-weight: 600;
}
.del-code {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  font-family: 'SF Mono', Consolas, Menlo, monospace;
  margin-top: 4px;
}
</style>

<style>
/* 下拉里的危险项（删除项目）——scoped 选择器进不到 teleport 到 body 的下拉面板 */
.el-dropdown-menu__item.danger-item {
  color: var(--el-color-danger);
}
.el-dropdown-menu__item.danger-item:not(.is-disabled):hover {
  background: var(--el-color-danger-light-9);
  color: var(--el-color-danger);
}
</style>
