<template>
  <div class="project-manage-page">
    <el-card shadow="never">
      <template #header>
        <div class="card-header">
          <div>
            <span class="title">项目管理</span>
            <span class="subtitle">
              立项后需在「用户管理 → 项目成员管理」中分配人员
            </span>
          </div>
          <div>
            <!-- 项目范围筛选（2026-10-21）：后端 GET /project/list 对超管**无条件返回全平台项目**
                 （`_can_view_all_projects` = is_super_admin，不走成员归属过滤），所以切换项目后
                 本页看着没变化。筛选放前端做，默认跟随顶栏当前项目。 -->
            <el-select
              v-model="scopeFilter"
              size="small"
              style="width: 150px; margin-right: 12px"
            >
              <el-option label="仅当前项目" value="current" />
              <el-option label="全部项目" value="all" />
            </el-select>
            <el-checkbox
              v-model="includeClosed"
              style="margin-right: 12px"
              @change="load"
            >显示已结项项目</el-checkbox>
            <el-button @click="load">
              <el-icon><Refresh /></el-icon>
              <span style="margin-left: 4px">刷新</span>
            </el-button>
            <el-button v-if="canCreate" type="primary" @click="openCreate">新建项目</el-button>
          </div>
        </div>
      </template>

      <el-table :data="visibleItems" v-loading="loading" stripe>
        <el-table-column label="项目名称" min-width="180">
          <template #default="{ row }">
            <span class="pname">{{ row.name }}</span>
            <el-tag v-if="row.id === currentProjectId" size="small" type="success" style="margin-left: 8px">
              当前
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="code" label="项目编号" min-width="140">
          <template #default="{ row }">
            <span class="mono">{{ row.code }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="short_name" label="简称" min-width="100">
          <template #default="{ row }">{{ row.short_name || '-' }}</template>
        </el-table-column>
        <el-table-column prop="location" label="地点" min-width="150">
          <template #default="{ row }">{{ row.location || '-' }}</template>
        </el-table-column>
        <el-table-column label="状态" min-width="110">
          <template #default="{ row }">
            <el-tag :type="statusTagType(row.status)" size="small">
              {{ statusLabel(row.status) }}
            </el-tag>
            <div v-if="row.completed_at" class="form-hint">
              结项 {{ fmtDate(row.completed_at) }}
            </div>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="300" fixed="right">
          <template #default="{ row }">
            <el-button
              v-if="row.id !== currentProjectId && row.status === 'active'"
              link
              type="primary"
              @click="switchTo(row)"
            >切换到此项目</el-button>
            <span v-else-if="row.status === 'active'" class="muted">使用中</span>
            <el-button v-if="auth.hasPermission('project_edit')" link type="primary" @click="openEdit(row)">项目资料</el-button>
            <el-button link type="primary" @click="goMembers(row)">分配人员</el-button>
            <el-button
              v-if="canComplete && row.status === 'active'"
              link
              type="warning"
              @click="completeProject(row)"
            >结项</el-button>
            <el-button
              v-if="canComplete && row.status !== 'active'"
              link
              type="primary"
              @click="reopenProject(row)"
            >恢复</el-button>
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
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Refresh } from '@element-plus/icons-vue'
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
// Step 5：默认只显示进行中项目；勾选后连已结项（完工/归档）一并显示
const includeClosed = ref(false)

// 仅超管可立项（与后端 project_create 权限同口径）
const canCreate = computed(() => auth.hasPermission('project_create'))
// Step 5：结项/恢复（权限 project_complete，默认仅超管可配）
const canComplete = computed(() => auth.hasPermission('project_complete'))
const currentProjectId = computed(() => auth.userInfo?.project_id ?? null)

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
    const res: any = await projectApi.list(includeClosed.value)
    items.value = Array.isArray(res) ? res : res?.items || []
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || '加载项目失败')
  } finally {
    loading.value = false
  }
}

// ---- 项目范围筛选（2026-10-21，纯前端）----
// 超管在 `GET /project/list` 上走的是「不加 where」分支，返回全平台项目，
// 与顶栏选中的项目无关。这里默认只看当前项目，需要纵览时切「全部项目」。
const scopeFilter = ref<'current' | 'all'>('all')

const visibleItems = computed(() => {
  // 当前项目置顶，其余按原顺序（后端已按创建时间倒序）
  const sorted = [...items.value].sort((a, b) => {
    const ca = a.id === currentProjectId.value ? 0 : 1
    const cb = b.id === currentProjectId.value ? 0 : 1
    return ca - cb
  })
  if (scopeFilter.value === 'current') {
    if (!currentProjectId.value) return []
    return sorted.filter((row) => row.id === currentProjectId.value)
  }
  return sorted
})

const emptyText = computed(() =>
  scopeFilter.value === 'current' && !currentProjectId.value
    ? '当前账号未绑定项目，请切到「全部项目」查看'
    : '暂无项目',
)

// 顶栏切换项目 / 视角变化后，列表筛选跟随：
// 选中具体项目 → 只看该项目；「全部项目」视角或未绑定项目 → 看全部
watch(
  () => [auth.currentProjectId, auth.allProjects] as const,
  ([pid, all]) => { scopeFilter.value = all || !pid ? 'all' : 'current' },
  { immediate: true },
)

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

onMounted(load)
</script>

<style scoped>
.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.card-header .title {
  font-size: 16px;
  font-weight: 600;
  margin-right: 12px;
}
.card-header .subtitle {
  font-size: 13px;
  color: var(--el-text-color-secondary);
}
.pname {
  font-weight: 600;
}
.mono {
  font-family: 'SF Mono', Consolas, monospace;
  font-size: 13px;
}
.muted {
  color: var(--el-text-color-placeholder);
}
.form-hint {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  line-height: 1.6;
  margin-top: 4px;
}
</style>
