<template>
  <!-- 项目成员管理（2026-10-23 从 UserManage.vue 抽出为共享组件）。
       项目级归属与岗位的真源在此（project_members），不在用户表的「所属项目」字段。
       双入口：「用户管理 → 项目成员管理」与「项目管理 → 分配人员」共用本弹窗；
       用户列表在弹窗内自行拉取（authApi.users，后端按调用者身份过滤），
       不依赖父页面的 users，免得两个入口都要各备一份账号数据。 -->
  <el-dialog
    :model-value="visible"
    title="项目成员管理"
    width="760px"
    @update:model-value="onVisibleChange"
  >
    <el-form inline>
      <el-form-item label="项目">
        <el-select v-model="innerProjectId" style="width:260px" @change="loadMembers">
          <el-option
            v-for="project in projects"
            :key="project.id"
            :label="project.name"
            :value="project.id"
          />
        </el-select>
      </el-form-item>
      <el-form-item>
        <el-checkbox v-model="includeLeft" @change="loadMembers">
          显示已离场（历史在场区间）
        </el-checkbox>
      </el-form-item>
      <el-form-item>
        <span class="member-hint">
          归属真源是成员表；「所属项目」只是当前会话上下文，两者相互独立。
        </span>
      </el-form-item>
    </el-form>

    <!-- max-height：成员多的项目（实测 9+ 人）不设限会把「加入用户」表单和页脚顶出弹窗；
         设限后表头固定、tbody 内部滚动，下方表单与按钮始终可见。 -->
    <el-table :data="members" v-loading="loading" stripe size="small" max-height="320">
      <el-table-column prop="username" label="用户名" min-width="110" />
      <el-table-column prop="real_name" label="姓名" min-width="90" />
      <el-table-column label="主岗" min-width="130">
        <template #default="{ row }">{{ roleLabel(row.role) }}</template>
      </el-table-column>
      <el-table-column label="附加岗" min-width="150">
        <template #default="{ row }">
          <el-tag
            v-for="extra in row.extra_roles || []"
            :key="extra"
            size="small"
            style="margin-right: 4px"
          >
            {{ roleLabel(extra) }}
          </el-tag>
          <span v-if="!(row.extra_roles || []).length" class="member-hint">—</span>
        </template>
      </el-table-column>
      <el-table-column label="在场区间" min-width="180">
        <template #default="{ row }">
          <el-tag v-if="row.is_present" size="small" type="success">在岗</el-tag>
          <el-tag v-else size="small" type="info">已离场</el-tag>
          <div class="member-hint">{{ presenceRange(row) }}</div>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="90" fixed="right">
        <template #default="{ row }">
          <el-button
            v-if="row.is_present"
            link
            type="danger"
            @click="removeMember(row)"
          >移出</el-button>
          <el-button
            v-else
            link
            type="primary"
            @click="restoreMember(row)"
          >重新加入</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-divider />

    <el-form :model="memberForm" label-width="90px">
      <el-form-item label="加入用户" required>
        <el-select v-model="memberForm.user_id" filterable style="width:260px" placeholder="选择用户">
          <el-option
            v-for="item in dialogUsers"
            :key="item.id"
            :label="`${item.real_name}（${item.username}）`"
            :value="item.id"
          />
        </el-select>
      </el-form-item>
      <el-form-item label="主岗" required>
        <el-select v-model="memberForm.role" style="width:260px">
          <el-option
            v-for="opt in assignableRoleOptions"
            :key="opt.value"
            :label="opt.label"
            :value="opt.value"
          />
        </el-select>
      </el-form-item>
      <el-form-item label="附加岗">
        <el-select
          v-model="memberForm.extra_roles"
          multiple
          style="width:380px"
          placeholder="可多选；总监/总监代表为排他岗位，不可作附加岗"
        >
          <el-option
            v-for="opt in stackableRoleOptions"
            :key="opt.value"
            :label="opt.label"
            :value="opt.value"
          />
        </el-select>
      </el-form-item>
    </el-form>

    <template #footer>
      <el-button @click="close">关闭</el-button>
      <el-button type="primary" :loading="saving" @click="addMember">加入项目</el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { authApi, projectApi } from '@/api'
import { PROJECT_ROLE_OPTIONS, CHIEF_ROLE_KEYS, DEFAULT_ROLE, roleLabel } from '@/constants/role'

const props = defineProps<{
  visible: boolean
  /** 项目下拉数据源（父页面已拉全量；ProjectManage 传 items，UserManage 传 projects） */
  projects: any[]
  /**
   * 打开时默认选中的项目。父页面按自己的口径算好后传入：
   * UserManage = 筛选器选中值 → 顶栏当前项目 → 回落（见其 defaultProjectId）；
   * ProjectManage = 行上的项目 id。每次打开都重置，不停在上次的项目上。
   */
  defaultProjectId?: number | null
}>()

const emit = defineEmits<{
  (e: 'update:visible', v: boolean): void
  (e: 'changed', projectId: number | null): void
}>()

// S2：后端接口的 role 为英文 key，提交时必须用 key（中文会 400）。
// 「主岗」只能是项目级岗位；排他岗位（总监/总监代表）只能作主岗（每项目 1 名）。
const assignableRoleOptions = PROJECT_ROLE_OPTIONS
const stackableRoleOptions = computed(() =>
  PROJECT_ROLE_OPTIONS.filter((item) => !CHIEF_ROLE_KEYS.includes(item.value)),
)

const innerProjectId = ref<number | undefined>(undefined)
// 是否显示已离场成员（历史在场区间可追溯）
const includeLeft = ref(false)
const loading = ref(false)
const saving = ref(false)
const members = ref<any[]>([])
const dialogUsers = ref<any[]>([])
const memberForm = reactive({
  user_id: undefined as number | undefined,
  role: DEFAULT_ROLE,
  extra_roles: [] as string[],
})

/**
 * 打开即重置默认项目并加载；关闭不清理数据（下次打开会覆盖，避免闪空）。
 * 🔴 默认项目必须由父页面按视角传入，不能在这里取 projects[0]
 * （后端 list_projects 是 created_at DESC，[0] = 最新建的项目，
 *  与顶栏选中项目无关 —— 2026-10-21 线上实测复现过）。
 */
watch(
  () => props.visible,
  (open) => {
    if (!open) return
    innerProjectId.value = props.defaultProjectId ?? undefined
    includeLeft.value = false
    memberForm.user_id = undefined
    memberForm.extra_roles = []
    void loadUsers()
    if (innerProjectId.value) loadMembers()
  },
)

async function loadUsers() {
  try {
    const res: any = await authApi.users()
    dialogUsers.value = res?.items || []
  } catch {
    // 拉不到账号列表不阻塞成员管理（加入用户下拉为空而已）
    dialogUsers.value = []
  }
}

async function loadMembers() {
  if (!innerProjectId.value) return
  loading.value = true
  try {
    const result: any = await projectApi.members(
      innerProjectId.value,
      includeLeft.value,
    )
    members.value = result.items || []
  } catch (error: any) {
    ElMessage.error(error.response?.data?.detail || '成员列表加载失败')
  } finally {
    loading.value = false
  }
}

// 在场区间展示（joined_at ~ left_at）。在岗显示"自 x 起"。
function fmtMoment(value?: string | null) {
  if (!value) return ''
  return value.replace('T', ' ').slice(0, 16)
}

function presenceRange(row: any) {
  const from = fmtMoment(row.joined_at) || '—'
  if (row.is_present) return `自 ${from} 起在岗`
  return `${from} ~ ${fmtMoment(row.left_at)}`
}

// 把已离场成员重新加入（复用 addMember 的复职语义，清 left_at）
async function restoreMember(row: any) {
  const extraText = (row.extra_roles || []).length
    ? ` + ${(row.extra_roles || []).map(roleLabel).join(' / ')}`
    : ''
  try {
    await ElMessageBox.confirm(
      `确认将「${row.real_name || row.username}」重新加入本项目？将沿用其原岗位：${roleLabel(row.role)}${extraText}`,
      '重新加入项目',
      { type: 'info', confirmButtonText: '确认加入', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  try {
    await projectApi.addMember(innerProjectId.value!, {
      user_id: row.user_id,
      role: row.role,
      extra_roles: row.extra_roles || [],
    })
    ElMessage.success('已重新加入项目')
    await loadMembers()
    emit('changed', innerProjectId.value ?? null)
  } catch (error: any) {
    ElMessage.error(error.response?.data?.detail || '重新加入失败')
  }
}

async function addMember() {
  if (!innerProjectId.value) return ElMessage.warning('请先选择项目')
  if (!memberForm.user_id) return ElMessage.warning('请选择要加入的用户')
  if (!memberForm.role) return ElMessage.warning('请选择主岗')
  saving.value = true
  try {
    const result: any = await projectApi.addMember(innerProjectId.value, {
      user_id: memberForm.user_id,
      role: memberForm.role,
      extra_roles: memberForm.extra_roles,
    })
    ElMessage.success(result?.detail || '已加入项目')
    memberForm.user_id = undefined
    memberForm.extra_roles = []
    await loadMembers()
    emit('changed', innerProjectId.value ?? null)
  } catch (error: any) {
    // 后端会因「排他岗位已存在」「不能把自己加入项目」等返回 4xx，原文透出
    ElMessage.error(error.response?.data?.detail || '加入项目失败')
  } finally {
    saving.value = false
  }
}

async function removeMember(row: any) {
  try {
    await ElMessageBox.confirm(
      `确认将「${row.real_name || row.username}」移出本项目？\n`
        + '其在本项目的岗位一并失效；成员与在场记录会保留（软删除），之后可重新加入。',
      '移出项目',
      { type: 'warning', confirmButtonText: '确认移出', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  try {
    await projectApi.removeMember(innerProjectId.value!, row.user_id)
    ElMessage.success('已移出项目（历史记录保留）')
    await loadMembers()
    emit('changed', innerProjectId.value ?? null)
  } catch (error: any) {
    ElMessage.error(error.response?.data?.detail || '移出失败')
  }
}

function close() {
  emit('update:visible', false)
}

/** 点右上角关闭 / 点遮罩时 dialog 会发 false，等同于取消。 */
function onVisibleChange(v: boolean | string | number) {
  if (!v) close()
}
</script>

<style scoped>
.member-hint { color: #7a8580; font-size: 12px; }
</style>
