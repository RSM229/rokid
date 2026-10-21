<template>
  <div class="invite-codes-page">
    <el-card shadow="never">
      <template #header>
        <div class="card-header">
          <div>
          <span class="title">项目邀请码</span>
          <span class="subtitle">
            邀请码绑定「项目 + 岗位」，已登录成员填写后即加入该项目并获得该岗位（与注册解耦）
          </span>
          </div>
          <div class="header-actions">
            <el-select
              v-model="filterProjectId"
              clearable
              placeholder="全部项目"
              style="width: 180px"
              @change="load"
            >
              <el-option v-for="p in projects" :key="p.id" :label="p.name" :value="p.id" />
            </el-select>
            <el-checkbox v-model="includeInactive" @change="load">显示已停用</el-checkbox>
            <el-button type="primary" @click="openCreate">新建邀请码</el-button>
          </div>
        </div>
      </template>

      <el-table :data="items" v-loading="loading" stripe>
        <el-table-column label="邀请码" min-width="130">
          <template #default="{ row }">
            <span class="code-text">{{ row.code }}</span>
            <el-button link type="primary" size="small" @click="copy(row.code)">复制</el-button>
          </template>
        </el-table-column>
        <el-table-column label="所属项目" min-width="140">
          <template #default="{ row }">
            <span v-if="row.project_name">{{ row.project_name }}</span>
            <span v-else class="muted">—（历史公司码）</span>
          </template>
        </el-table-column>
        <el-table-column label="岗位" min-width="130">
          <template #default="{ row }">
            <el-tag size="small">{{ row.role_label || roleLabel(row.role) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="级别" width="100">
          <template #default="{ row }">
            {{ levelLabel(row.level) }}
          </template>
        </el-table-column>
        <el-table-column label="使用情况" width="110">
          <template #default="{ row }">
            {{ row.used_count }} / {{ row.max_uses }}
          </template>
        </el-table-column>
        <el-table-column label="剩余" width="80">
          <template #default="{ row }">{{ row.remaining }}</template>
        </el-table-column>
        <el-table-column label="有效期" min-width="170">
          <template #default="{ row }">
            <span v-if="!row.expires_at" class="muted">长期有效</span>
            <span v-else :class="{ expired: row.expired }">
              {{ formatTime(row.expires_at) }}{{ row.expired ? '（已过期）' : '' }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="90">
          <template #default="{ row }">
            <el-tag v-if="row.is_active" type="success" size="small">启用</el-tag>
            <el-tag v-else type="info" size="small">已停用</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="创建人" width="110">
          <template #default="{ row }">{{ row.created_by || '-' }}</template>
        </el-table-column>
        <el-table-column label="操作" width="90" fixed="right">
          <template #default="{ row }">
            <el-button
              v-if="row.is_active"
              link
              type="danger"
              @click="revoke(row)"
            >停用</el-button>
            <span v-else class="muted">—</span>
          </template>
        </el-table-column>
      </el-table>

      <el-empty v-if="!loading && !items.length" description="暂无邀请码" />
    </el-card>

    <el-dialog v-model="dialogVisible" title="新建项目邀请码" width="480px">
      <el-form :model="form" label-width="100px">
        <el-form-item label="所属项目" required>
          <el-select
            v-model="form.projectId"
            style="width: 100%"
            placeholder="选择要加人的项目"
            filterable
          >
            <el-option
              v-for="p in projects"
              :key="p.id"
              :label="p.name"
              :value="p.id"
            />
          </el-select>
          <span class="form-hint">邀请码绑定项目与岗位，成员填写后即加入该项目并获得该岗位</span>
        </el-form-item>
        <el-form-item label="岗位" required>
          <el-select v-model="form.role" style="width: 100%" placeholder="选择注册后的岗位">
            <el-option
              v-for="opt in roleOptions"
              :key="opt.value"
              :label="opt.label"
              :value="opt.value"
            />
          </el-select>
          <span class="form-hint">只能发放项目级岗位，公司级角色请在用户管理中授予</span>

        </el-form-item>
        <el-form-item label="级别">
          <el-select v-model="form.level" style="width: 100%">
            <el-option label="个人" value="personal" />
            <el-option label="团队" value="team" />
            <el-option label="企业" value="enterprise" />
          </el-select>
        </el-form-item>
        <el-form-item label="可用次数">
          <el-input-number v-model="form.max_uses" :min="1" :max="1000" />
        </el-form-item>
        <el-form-item label="有效期">
          <el-select v-model="form.expires_in_days" style="width: 100%">
            <el-option label="长期有效" :value="null" />
            <el-option label="7 天" :value="7" />
            <el-option label="30 天" :value="30" />
            <el-option label="90 天" :value="90" />
            <el-option label="365 天" :value="365" />
          </el-select>
        </el-form-item>
        <el-form-item label="自定义码">
          <el-input
            v-model="form.code"
            placeholder="留空自动生成 8 位易读码"
            maxlength="20"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="save">创建</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { authApi, projectApi } from '@/api'
import { PROJECT_ROLE_OPTIONS, DEFAULT_ROLE, roleLabel } from '@/constants/role'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const items = ref<any[]>([])
const loading = ref(false)
const includeInactive = ref(false)
const dialogVisible = ref(false)
const saving = ref(false)
// 可选项目：项目负责人发码时选定「加哪个项目」
const projects = ref<Array<{ id: number; name: string }>>([])
// 项目筛选（仅超管可全局查看；项目负责人只看自家项目）
const filterProjectId = ref<number | null>(null)

/**
 * 是否具备全局邀请码管理权（超管）。
 *
 * 超管走 `/auth/invite-codes`（全量）；其他人走 `/auth/projects/{id}/invite-codes`
 * —— 后者判据是「本项目负责人」，不依赖 `user_manage`，否则项目负责人看不到自家码。
 */
const canGlobal = computed(() => auth.hasPermission('user_manage'))

// 只允许项目级岗位（与后端 create_project_invite_code 校验一致）
const roleOptions = PROJECT_ROLE_OPTIONS

const form = reactive<{
  projectId: number | null
  role: string
  level: string
  max_uses: number
  expires_in_days: number | null
  code: string
}>({
  projectId: null,
  role: DEFAULT_ROLE,
  level: 'personal',
  max_uses: 1,
  expires_in_days: null,
  code: '',
})

async function loadProjects() {
  try {
    const res: any = await projectApi.list(false)
    projects.value = res?.items || res || []
  } catch {
    projects.value = []
  }
}

const LEVEL_LABELS: Record<string, string> = {
  personal: '个人',
  team: '团队',
  enterprise: '企业',
}
function levelLabel(level: string) {
  return LEVEL_LABELS[level] || level
}

function formatTime(value: string) {
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return value
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

async function load() {
  loading.value = true
  try {
    if (canGlobal.value) {
      // 超管：一次拿全量，按需在本地按项目筛（避免逐项目 N 次请求）
      const res: any = await authApi.inviteCodes(includeInactive.value)
      let list: any[] = res?.items || []
      if (filterProjectId.value) {
        list = list.filter((r) => r.project_id === filterProjectId.value)
      }
      items.value = list
    } else {
      // 项目负责人：逐项目拉自家码（无 user_manage 也能看/管）
      const targets = filterProjectId.value
        ? projects.value.filter((p) => p.id === filterProjectId.value)
        : projects.value
      const merged: any[] = []
      for (const p of targets) {
        try {
          const res: any = await authApi.listProjectInviteCodes(p.id, includeInactive.value)
          merged.push(...(res?.items || []))
        } catch {
          // 无该项目管理权 → 跳过，不影响其他项目
        }
      }
      items.value = merged
    }
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || '加载邀请码失败')
  } finally {
    loading.value = false
  }
}

function openCreate() {
  // 默认落在「当前所在项目」上（多项目负责人可在下拉里改）
  form.projectId = auth.currentProjectId ?? (projects.value[0]?.id ?? null)
  form.role = DEFAULT_ROLE
  form.level = 'personal'
  form.max_uses = 1
  form.expires_in_days = null
  form.code = ''
  dialogVisible.value = true
}

async function save() {
  if (!form.projectId) {
    ElMessage.warning('请选择要加人的项目')
    return
  }
  if (!form.role) {
    ElMessage.warning('请选择岗位')
    return
  }
  saving.value = true
  try {
    // 项目邀请码：码带 project_id + role，成员在「加入项目」填写后即获该岗位。
    // 与注册解耦 —— 注册只产出裸用户，权限全靠这条路径授予。
    const res: any = await authApi.createProjectInviteCode(form.projectId, {
      role: form.role,
      level: form.level,
      max_uses: form.max_uses,
      expires_in_days: form.expires_in_days,
      code: form.code?.trim() || null,
    })
    ElMessage.success(`项目邀请码已创建：${res?.code || ''}`)
    dialogVisible.value = false
    await load()
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || '创建失败')
  } finally {
    saving.value = false
  }
}

async function revoke(row: any) {
  try {
    await ElMessageBox.confirm(
      `确定停用邀请码「${row.code}」？停用后不可再用于加入项目，已加入的成员不受影响。`,
      '停用邀请码',
      { type: 'warning', confirmButtonText: '停用', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  try {
    await authApi.revokeInviteCode(row.code)
    ElMessage.success('已停用')
    await load()
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || '停用失败')
  }
}

async function copy(code: string) {
  try {
    await navigator.clipboard.writeText(code)
    ElMessage.success('已复制')
  } catch {
    ElMessage.warning(`复制失败，请手动记录：${code}`)
  }
}

onMounted(async () => {
  await loadProjects()
  await load()
})
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
.header-actions {
  display: flex;
  align-items: center;
  gap: 12px;
}
.code-text {
  font-family: 'SF Mono', Consolas, monospace;
  font-weight: 600;
  letter-spacing: 1px;
  margin-right: 8px;
}
.muted {
  color: var(--el-text-color-placeholder);
}
.expired {
  color: var(--el-color-danger);
}
.form-hint {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  line-height: 1.6;
  margin-top: 4px;
}
</style>
