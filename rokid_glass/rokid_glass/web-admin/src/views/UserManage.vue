<template>
  <div>
    <div class="page-header">
      <div>
        <h2>用户管理</h2>
        <p>创建白泽账号、分配角色与项目，并查看微信绑定状态。</p>
      </div>
      <div class="header-actions">
        <el-button @click="openMembers">项目成员管理</el-button>
        <!-- Step 3：角色权限配置入口（role_grant，当前仅超管） -->
        <el-button v-if="canGrantRole" @click="goRolePermissions">角色权限</el-button>
        <!-- 新增/编辑用户需 user_manage，总监只有 settings → 隐藏，避免点击必然 403 -->
        <el-button v-if="canManageUsers" type="primary" @click="openCreate">新增用户</el-button>
      </div>
    </div>

    <el-tabs v-model="activeTab" class="user-tabs">
      <el-tab-pane name="list" label="账号列表">
        <el-card shadow="never">
          <div class="list-toolbar">
            <el-select v-model="auditFilter" size="small" style="width: 170px">
              <el-option label="全部审批状态" value="" />
              <el-option label="已通过（可登录）" value="approved" />
              <el-option label="待审批（不可登录）" value="pending" />
              <el-option label="已驳回（不可登录）" value="rejected" />
            </el-select>
            <span class="muted">共 {{ filteredUsers.length }} / {{ users.length }} 个账号</span>
          </div>
          <el-table :data="filteredUsers" v-loading="loading" stripe>
            <el-table-column prop="username" label="用户名" min-width="120" />
            <el-table-column prop="real_name" label="姓名" min-width="100" />
            <el-table-column label="主岗" min-width="140">
              <template #default="{ row }">{{ roleLabel(row.role) }}</template>
            </el-table-column>
            <el-table-column label="公司级角色" min-width="130">
              <template #default="{ row }">
                <el-tag v-if="row.org_role" type="warning" size="small">
                  {{ roleLabel(row.org_role) }}
                </el-tag>
                <span v-else class="muted">—</span>
              </template>
            </el-table-column>
            <el-table-column label="所属项目" min-width="150">
              <template #default="{ row }">{{ projectName(row.project_id) }}</template>
            </el-table-column>
            <el-table-column prop="phone" label="手机号" min-width="120" />
            <el-table-column label="微信" width="150">
              <template #default="{ row }">
                <el-tag :type="row.wechat_bound ? 'success' : 'info'" size="small">
                  {{ row.wechat_bound ? '已绑定' : '未绑定' }}
                </el-tag>
                <el-button
                  v-if="row.wechat_bound"
                  link
                  type="danger"
                  size="small"
                  style="margin-left: 6px"
                  @click="unbindWechat(row)"
                >
                  解绑
                </el-button>
              </template>
            </el-table-column>
            <el-table-column label="审批状态" width="110">
              <template #default="{ row }">
                <el-tooltip
                  :disabled="!row.reject_reason"
                  :content="`驳回理由：${row.reject_reason || ''}`"
                  placement="top"
                >
                  <el-tag :type="auditTagType(row.audit_status)" size="small">
                    {{ auditLabel(row.audit_status) }}
                  </el-tag>
                </el-tooltip>
              </template>
            </el-table-column>
            <el-table-column label="账号状态" width="95">
              <template #default="{ row }">
                <el-tag :type="row.is_active ? 'success' : 'danger'" size="small">
                  {{ row.is_active ? '启用' : '停用' }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="220" fixed="right">
              <template #default="{ row }">
                <el-button v-if="canManageUsers" link type="primary" @click="openEdit(row)">编辑</el-button>
                <el-button
                  v-if="canManageUsers"
                  link
                  type="warning"
                  @click="openResetPassword(row)"
                >
                  重置密码
                </el-button>
                <el-button
                  v-if="canApprove && !isApproved(row)"
                  link
                  type="success"
                  @click="openApprove(row)"
                >
                  审批
                </el-button>
                <span v-if="!canManageUsers && !(canApprove && !isApproved(row))" class="muted">—</span>
              </template>
            </el-table-column>
          </el-table>
        </el-card>
      </el-tab-pane>

      <el-tab-pane v-if="canApprove" name="pending">
        <template #label>
          <el-badge :value="pendingList.length" :hidden="!pendingList.length" type="danger">
            <span>待审批</span>
          </el-badge>
        </template>
        <el-card shadow="never">
          <div class="pending-hint">
            新注册人员在此审核。批准时须<b>选择项目并指定岗位</b> —— 项目归属由审批决定
            （邀请码只决定申请意向，不代表最终岗位）。
          </div>
          <el-table :data="pendingList" v-loading="pendingLoading" stripe>
            <el-table-column prop="username" label="用户名" min-width="110" />
            <el-table-column prop="real_name" label="姓名" min-width="90" />
            <el-table-column prop="phone" label="手机号" min-width="120" />
            <el-table-column label="申请岗位" min-width="130">
              <template #default="{ row }">{{ row.apply_role_label || '—' }}</template>
            </el-table-column>
            <el-table-column prop="invite_code" label="邀请码" min-width="110" />
            <el-table-column label="提交时间" min-width="160">
              <template #default="{ row }">{{ (row.created_at || '').replace('T', ' ').slice(0, 16) }}</template>
            </el-table-column>
            <el-table-column label="操作" width="150" fixed="right">
              <template #default="{ row }">
                <el-button link type="primary" @click="openApprove(row)">批准</el-button>
                <el-button link type="danger" @click="openReject(row)">拒绝</el-button>
              </template>
            </el-table-column>
          </el-table>
          <el-empty v-if="!pendingLoading && !pendingList.length" description="暂无待审批申请" />
        </el-card>
      </el-tab-pane>
    </el-tabs>

    <el-dialog v-model="dialogVisible" :title="editingId ? '编辑用户' : '新增用户'" width="520px">
      <el-form :model="form" label-width="90px">
        <el-form-item label="用户名" required>
          <el-input
            v-model="form.username"
            :disabled="Boolean(editingId)"
            placeholder="用于登录，至少 3 个字符（建议字母开头）"
            maxlength="64"
          />
        </el-form-item>
        <el-form-item label="真实姓名" required>
          <el-input v-model="form.real_name" />
        </el-form-item>
        <el-form-item label="主岗" required>
          <!-- S2：提交英文 key，展示中文 label（仅项目级岗位） -->
          <el-select v-model="form.role" style="width:100%">
            <el-option v-for="opt in roleOptions" :key="opt.value" :label="opt.label" :value="opt.value" />
          </el-select>
        </el-form-item>
        <el-form-item label="公司级角色">
          <!-- 跨项目权限（org_role）。空 = 纯项目级成员。
               仅超管可授予；后端会拦截非超管提交。 -->
          <el-select
            v-model="form.org_role"
            clearable
            :disabled="!canGrantOrgRole"
            placeholder="无（仅项目级）"
            style="width:100%"
          >
            <el-option
              v-for="opt in orgRoleOptions"
              :key="opt.value"
              :label="opt.label"
              :value="opt.value"
            />
          </el-select>
          <span v-if="!canGrantOrgRole" class="form-hint">仅超级管理员可授予公司级角色</span>
        </el-form-item>
        <el-form-item label="所属项目" required>
          <el-select v-model="form.project_id" style="width:100%">
            <el-option v-for="project in projects" :key="project.id" :label="project.name" :value="project.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="手机号">
          <el-input v-model="form.phone" />
        </el-form-item>
        <el-form-item :label="editingId ? '重置密码' : '初始密码'" :required="!editingId">
          <el-input v-model="form.password" type="password" show-password placeholder="至少 8 位；编辑时留空则不修改" />
        </el-form-item>
        <el-form-item v-if="editingId" label="账号状态">
          <el-switch v-model="form.is_active" active-text="启用" inactive-text="停用" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="save">保存</el-button>
      </template>
    </el-dialog>

    <!-- Step 2：项目成员管理（项目级归属与岗位的真源在此，不在「所属项目」字段） -->
    <el-dialog v-model="memberDialogVisible" title="项目成员管理" width="760px">
      <el-form inline>
        <el-form-item label="项目">
          <el-select v-model="memberProjectId" style="width:260px" @change="loadMembers">
            <el-option
              v-for="project in projects"
              :key="project.id"
              :label="project.name"
              :value="project.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item>
          <el-checkbox v-model="memberIncludeLeft" @change="loadMembers">
            显示已离场（历史在场区间）
          </el-checkbox>
        </el-form-item>
        <el-form-item>
          <span class="member-hint">
            归属真源是成员表；「所属项目」只是当前会话上下文，两者相互独立。
          </span>
        </el-form-item>
      </el-form>

      <el-table :data="members" v-loading="memberLoading" stripe size="small">
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
              v-for="item in users"
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
        <el-button @click="memberDialogVisible = false">关闭</el-button>
        <el-button type="primary" :loading="memberSaving" @click="addMember">加入项目</el-button>
      </template>
    </el-dialog>

    <!-- Step 4：批准注册（选项目 + 定岗，一次落定开户与归属） -->
    <el-dialog v-model="approveVisible" title="批准入册" width="560px">
      <div class="approve-target">
        申请账号：<b>{{ approveTarget?.real_name }}</b>（{{ approveTarget?.username }}）
        <span v-if="approveTarget?.invite_code" class="muted"> · 邀请码 {{ approveTarget.invite_code }}</span>
        <span v-if="approveTarget?.apply_role_label" class="muted"> · 申请岗位 {{ approveTarget.apply_role_label }}</span>
      </div>
      <el-form :model="approveForm" label-width="100px" style="margin-top: 12px">
        <el-form-item label="加入项目">
          <el-select v-model="approveForm.project_id" clearable placeholder="可不选（仅激活账号）" style="width:100%">
            <el-option v-for="p in projects" :key="p.id" :label="p.name" :value="p.id" />
          </el-select>
          <span class="form-hint">归属由审批决定：选定项目后该账号即成为该项目成员</span>
        </el-form-item>
        <el-form-item label="主岗" required>
          <el-select v-model="approveForm.role" style="width:100%">
            <el-option v-for="opt in assignableRoleOptions" :key="opt.value" :label="opt.label" :value="opt.value" />
          </el-select>
        </el-form-item>
        <el-form-item label="附加岗">
          <el-select v-model="approveForm.extra_roles" multiple style="width:100%"
                     placeholder="可多选；总监/总监代表为排他岗位，不可作附加岗">
            <el-option v-for="opt in stackableRoleOptions" :key="opt.value" :label="opt.label" :value="opt.value" />
          </el-select>
        </el-form-item>
        <el-form-item label="公司级角色">
          <el-select v-model="approveForm.org_role" clearable :disabled="!canGrantOrgRole"
                     placeholder="无（仅项目级）" style="width:100%">
            <el-option v-for="opt in orgRoleOptions" :key="opt.value" :label="opt.label" :value="opt.value" />
          </el-select>
          <span v-if="!canGrantOrgRole" class="form-hint">仅超级管理员可授予公司级角色</span>
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="approveForm.remark" placeholder="可留空" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="approveVisible = false">取消</el-button>
        <el-button type="primary" :loading="approving" @click="submitApprove">确认批准</el-button>
      </template>
    </el-dialog>

    <!-- Step 4：拒绝注册 -->
    <el-dialog v-model="rejectVisible" title="拒绝注册申请" width="480px">
      <div class="approve-target">
        拒绝账号：<b>{{ rejectTarget?.real_name }}</b>（{{ rejectTarget?.username }}）
      </div>
      <el-input v-model="rejectReason" type="textarea" :rows="3" style="margin-top: 12px"
                placeholder="请填写拒绝理由（会展示给申请人）" />
      <template #footer>
        <el-button @click="rejectVisible = false">取消</el-button>
        <el-button type="danger" :loading="rejecting" @click="submitReject">确认拒绝</el-button>
      </template>
    </el-dialog>

    <!-- 忘记密码兜底：管理员重置为一次性临时口令 -->
    <el-dialog v-model="resetVisible" title="重置密码（一次性临时口令）" width="540px">
      <el-alert
        type="warning"
        :closable="false"
        show-icon
        class="reset-tip"
        title="重置后该账号原有密码立即失效"
        description="临时口令只需转交本人；其首次登录会被强制修改密码，改完口令即作废。"
      />
      <div v-if="resetResult">
        <el-descriptions :column="1" border>
          <el-descriptions-item label="账号">{{ resetResult.username }}</el-descriptions-item>
          <el-descriptions-item label="一次性临时口令">
            <span class="temp-pwd">{{ resetResult.temp_password }}</span>
            <el-button link type="primary" @click="copyTemp">复制</el-button>
          </el-descriptions-item>
        </el-descriptions>
        <div class="reset-hint">
          请通过可靠渠道（电话/当面）把临时口令转交用户 —— 不要在公开群聊里发送。
        </div>
      </div>
      <div v-else class="reset-hint">
        将为账号 <b>{{ resetTarget?.real_name }}</b>（{{ resetTarget?.username }}）生成一个一次性临时口令。
      </div>
      <template #footer>
        <el-button @click="resetVisible = false">关闭</el-button>
        <el-button v-if="!resetResult" type="primary" :loading="resetLoading" @click="confirmReset">
          确认重置
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { authApi, projectApi } from '@/api'
import { PROJECT_ROLE_OPTIONS, ORG_ROLE_OPTIONS, CHIEF_ROLE_KEYS, DEFAULT_ROLE, roleLabel } from '@/constants/role'
import { useAuthStore } from '@/stores/auth'

// S2：后端接口的 role 为英文 key，提交时必须用 key（中文会 400）。
// 「主岗」只能是项目级岗位 —— 公司级角色由 org_role 单独承载，
// 若混进主岗下拉会把公司级 key 写进 users.role，权限矩阵将错位。
const roleOptions = PROJECT_ROLE_OPTIONS
const orgRoleOptions = ORG_ROLE_OPTIONS
// 排他岗位（总监/总监代表）只能作主岗（每项目 1 名）
const assignableRoleOptions = PROJECT_ROLE_OPTIONS
const stackableRoleOptions = computed(() =>
  PROJECT_ROLE_OPTIONS.filter((item) => !CHIEF_ROLE_KEYS.includes(item.value)),
)
// 仅超管可授予公司级角色（与后端 _validate_org_role 同口径）
const auth = useAuthStore()
const router = useRouter()
// Step 3：角色权限配置入口（role_grant；后端同样以此 key 把关）
const canGrantRole = computed(() => auth.hasPermission('role_grant'))
function goRolePermissions() {
  router.push('/role-permissions')
}
const canGrantOrgRole = computed(() => auth.userInfo?.org_role === 'super_admin')
// 新增/编辑/改密 均需 user_manage（目前仅超管持有）；总监只有 settings 与
// member_manage，进本页只能看列表 + 管项目成员，故隐藏会必然 403 的按钮。
const canManageUsers = computed(() => auth.hasPermission('user_manage'))
// Step 4：审批需 user_approve（公司级：超管/管理员）；总监等持 member_manage
// 也不能审批 —— 与后端 require_permission("user_approve") 同口径。
const canApprove = computed(() => auth.hasPermission('user_approve'))
const users = ref<any[]>([])
const projects = ref<any[]>([])
const loading = ref(false)
const saving = ref(false)
const dialogVisible = ref(false)
const editingId = ref<number | null>(null)

// ---- Step 4：待审批 ----
const activeTab = ref('list')
const pendingList = ref<any[]>([])
const pendingLoading = ref(false)
const approveVisible = ref(false)
const approving = ref(false)
const approveTarget = ref<any>(null)
const approveForm = reactive({
  project_id: undefined as number | undefined,
  role: DEFAULT_ROLE,
  extra_roles: [] as string[],
  org_role: '' as string,
  remark: '',
})
const rejectVisible = ref(false)
const rejecting = ref(false)
const rejectTarget = ref<any>(null)
const rejectReason = ref('')

// ---- Step 4：账号列表的审批状态展示 ----
// 与后端同口径：audit_status 管「能否登录」，is_active 管「是否停用」。
// 缺省（存量/未迁移数据）按 approved 处理，避免把老账号误标为待审批。
const auditFilter = ref('')
const AUDIT_LABELS: Record<string, string> = {
  approved: '已通过',
  pending: '待审批',
  rejected: '已驳回',
}
function auditLabel(status?: string) {
  return AUDIT_LABELS[status || 'approved'] || status || '—'
}
function auditTagType(status?: string) {
  if (status === 'pending') return 'warning'
  if (status === 'rejected') return 'danger'
  return 'success'
}
function isApproved(row: any) {
  return (row.audit_status || 'approved') === 'approved'
}
const filteredUsers = computed(() =>
  auditFilter.value
    ? users.value.filter((item) => (item.audit_status || 'approved') === auditFilter.value)
    : users.value,
)

// ---- 项目成员管理（Step 2）----
const memberDialogVisible = ref(false)
const memberLoading = ref(false)
const memberSaving = ref(false)
const memberProjectId = ref<number | undefined>(undefined)
// Step 5：是否显示已离场成员（历史在场区间可追溯）
const memberIncludeLeft = ref(false)
const members = ref<any[]>([])
const memberForm = reactive({
  user_id: undefined as number | undefined,
  role: DEFAULT_ROLE,
  extra_roles: [] as string[],
})
const form = reactive({
  username: '',
  real_name: '',
  role: DEFAULT_ROLE,
  org_role: '' as string,
  project_id: undefined as number | undefined,
  phone: '',
  password: '',
  is_active: true,
})

function projectName(id?: number) {
  return projects.value.find((item) => item.id === id)?.name || `项目 #${id || '-'}`
}

async function loadData() {
  loading.value = true
  try {
    const [userResult, projectResult] = await Promise.all([
      authApi.users() as Promise<any>,
      projectApi.list() as Promise<any>,
    ])
    users.value = userResult.items || []
    projects.value = projectResult || []
  } catch (error: any) {
    ElMessage.error(error.response?.data?.detail || '用户数据加载失败')
  } finally {
    loading.value = false
  }
  if (canApprove.value) await loadPending()
}

// ---- Step 4：待审批 ----
async function loadPending() {
  if (!canApprove.value) return
  pendingLoading.value = true
  try {
    const result: any = await authApi.registrations('pending')
    pendingList.value = result.items || []
  } catch (error: any) {
    ElMessage.error(error.response?.data?.detail || '待审批列表加载失败')
  } finally {
    pendingLoading.value = false
  }
}

function openApprove(row: any) {
  approveTarget.value = row
  approveForm.project_id = projects.value[0]?.id
  // 码上的岗位 = 申请意向 → 预填（审批人可改，改后以审批为准）
  approveForm.role = row.apply_role || DEFAULT_ROLE
  approveForm.extra_roles = []
  approveForm.org_role = ''
  approveForm.remark = ''
  approveVisible.value = true
}

async function submitApprove() {
  if (!approveTarget.value) return
  if (!approveForm.role) return ElMessage.warning('请选择主岗')
  approving.value = true
  try {
    const roles = [approveForm.role, ...approveForm.extra_roles.filter((r) => r !== approveForm.role)]
    const result: any = await authApi.approveRegistration(approveTarget.value.user_id, {
      project_id: approveForm.project_id || null,
      roles,
      org_role: approveForm.org_role || null,
      remark: approveForm.remark || undefined,
    })
    ElMessage.success(result?.detail || '已批准')
    approveVisible.value = false
    await loadData()
  } catch (error: any) {
    // 排他岗位冲突（409）、无权限（403）等原样透出
    ElMessage.error(error.response?.data?.detail || '批准失败')
  } finally {
    approving.value = false
  }
}

function openReject(row: any) {
  rejectTarget.value = row
  rejectReason.value = ''
  rejectVisible.value = true
}

async function submitReject() {
  if (!rejectTarget.value) return
  if (!rejectReason.value.trim()) return ElMessage.warning('请填写拒绝理由')
  rejecting.value = true
  try {
    const result: any = await authApi.rejectRegistration(rejectTarget.value.user_id, rejectReason.value.trim())
    ElMessage.success(result?.detail || '已拒绝')
    rejectVisible.value = false
    await loadData()
  } catch (error: any) {
    ElMessage.error(error.response?.data?.detail || '拒绝失败')
  } finally {
    rejecting.value = false
  }
}

// ---- 忘记密码兜底：重置为一次性临时口令（权限 user_manage）----
const resetVisible = ref(false)
const resetLoading = ref(false)
const resetTarget = ref<any>(null)
const resetResult = ref<any>(null)

function openResetPassword(row: any) {
  resetTarget.value = row
  resetResult.value = null
  resetVisible.value = true
}

async function confirmReset() {
  if (!resetTarget.value) return
  resetLoading.value = true
  try {
    const res: any = await authApi.adminResetPassword(resetTarget.value.id)
    resetResult.value = res
    ElMessage.success('已生成一次性临时口令')
  } catch (error: any) {
    ElMessage.error(error.response?.data?.detail || '重置失败')
  } finally {
    resetLoading.value = false
  }
}

async function copyTemp() {
  const pwd = resetResult.value?.temp_password
  if (!pwd) return
  try {
    await navigator.clipboard.writeText(pwd)
    ElMessage.success('临时口令已复制')
  } catch {
    ElMessage.warning('复制失败，请手动选择复制')
  }
}

function resetForm() {
  form.username = ''
  form.real_name = ''
  form.role = DEFAULT_ROLE
  form.org_role = ''
  form.project_id = projects.value[0]?.id
  form.phone = ''
  form.password = ''
  form.is_active = true
}

function openCreate() {
  if (!canManageUsers.value) {
    ElMessage.warning('需要「账号管理」权限，请联系超级管理员')
    return
  }
  editingId.value = null
  resetForm()
  dialogVisible.value = true
}

function openEdit(row: any) {
  if (!canManageUsers.value) {
    ElMessage.warning('需要「账号管理」权限，请联系超级管理员')
    return
  }
  editingId.value = row.id
  form.username = row.username
  form.real_name = row.real_name
  form.role = row.role
  form.org_role = row.org_role || ''
  form.project_id = row.project_id
  form.phone = row.phone || ''
  form.password = ''
  form.is_active = row.is_active
  dialogVisible.value = true
}

async function save() {
  if (!form.username.trim() || !form.real_name.trim() || !form.role || !form.project_id) {
    return ElMessage.warning('请填写用户名、姓名、角色和项目')
  }
  // 后端 CreateUserRequest 要求 username ≥ 3 字符；前端先拦，报错才可读
  if (!editingId.value && form.username.trim().length < 3) {
    return ElMessage.warning('用户名至少 3 个字符')
  }
  if (!editingId.value && form.password.length < 8) {
    return ElMessage.warning('初始密码至少 8 位')
  }
  if (editingId.value && form.password && form.password.length < 8) {
    return ElMessage.warning('重置密码至少 8 位')
  }
  saving.value = true
  try {
    if (editingId.value) {
      await authApi.updateUser(editingId.value, {
        real_name: form.real_name.trim(),
        role: form.role,
        // 传空串 = 清除公司级角色；前端 el-select clearable 清空后为 ''
        org_role: form.org_role ?? '',
        project_id: form.project_id,
        phone: form.phone || null,
        is_active: form.is_active,
        new_password: form.password || null,
      })
    } else {
      await authApi.createUser({
        username: form.username.trim(),
        password: form.password,
        real_name: form.real_name.trim(),
        role: form.role,
        // 建号时可一并授予公司级；空串表示不授予
        org_role: form.org_role || null,
        project_id: form.project_id,
        phone: form.phone || null,
      })
    }
    ElMessage.success('用户已保存')
    dialogVisible.value = false
    await loadData()
  } catch (error: any) {
    ElMessage.error(error.response?.data?.detail || '用户保存失败')
  } finally {
    saving.value = false
  }
}

async function unbindWechat(row: any) {
  try {
    await ElMessageBox.confirm(
      `确认解除「${row.username}（${row.real_name || '-'}）」的微信绑定？解绑后该账号需在小程序重新绑定微信才能登录。`,
      '解绑微信',
      { type: 'warning', confirmButtonText: '确认解绑', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  try {
    const result: any = await authApi.unbindWechat(row.id)
    const hint = result?.openid_hint ? `（原绑定：${result.openid_hint}）` : ''
    ElMessage.success(`${result?.detail || '已解除微信绑定'}${hint}`)
    await loadData()
  } catch (error: any) {
    ElMessage.error(error.response?.data?.detail || '解绑失败')
  }
}

// ---- 项目成员管理（Step 2）----
function openMembers() {
  memberProjectId.value = memberProjectId.value || projects.value[0]?.id
  memberDialogVisible.value = true
  if (memberProjectId.value) loadMembers()
}

async function loadMembers() {
  if (!memberProjectId.value) return
  memberLoading.value = true
  try {
    const result: any = await projectApi.members(
      memberProjectId.value,
      memberIncludeLeft.value,
    )
    members.value = result.items || []
  } catch (error: any) {
    ElMessage.error(error.response?.data?.detail || '成员列表加载失败')
  } finally {
    memberLoading.value = false
  }
}

// Step 5：在场区间展示（joined_at ~ left_at）。在岗显示"自 x 起"。
function fmtMoment(value?: string | null) {
  if (!value) return ''
  return value.replace('T', ' ').slice(0, 16)
}

function presenceRange(row: any) {
  const from = fmtMoment(row.joined_at) || '—'
  if (row.is_present) return `自 ${from} 起在岗`
  return `${from} ~ ${fmtMoment(row.left_at)}`
}

// Step 5：把已离场成员重新加入（复用 addMember 的复职语义，清 left_at）
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
    await projectApi.addMember(memberProjectId.value!, {
      user_id: row.user_id,
      role: row.role,
      extra_roles: row.extra_roles || [],
    })
    ElMessage.success('已重新加入项目')
    await loadMembers()
  } catch (error: any) {
    ElMessage.error(error.response?.data?.detail || '重新加入失败')
  }
}

async function addMember() {
  if (!memberProjectId.value) return ElMessage.warning('请先选择项目')
  if (!memberForm.user_id) return ElMessage.warning('请选择要加入的用户')
  if (!memberForm.role) return ElMessage.warning('请选择主岗')
  memberSaving.value = true
  try {
    const result: any = await projectApi.addMember(memberProjectId.value, {
      user_id: memberForm.user_id,
      role: memberForm.role,
      extra_roles: memberForm.extra_roles,
    })
    ElMessage.success(result?.detail || '已加入项目')
    memberForm.user_id = undefined
    memberForm.extra_roles = []
    await loadMembers()
  } catch (error: any) {
    // 后端会因「排他岗位已存在」「不能把自己加入项目」等返回 4xx，原文透出
    ElMessage.error(error.response?.data?.detail || '加入项目失败')
  } finally {
    memberSaving.value = false
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
    await projectApi.removeMember(memberProjectId.value!, row.user_id)
    ElMessage.success('已移出项目（历史记录保留）')
    await loadMembers()
  } catch (error: any) {
    ElMessage.error(error.response?.data?.detail || '移出失败')
  }
}

onMounted(loadData)
</script>

<style scoped>
.page-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 16px;
}
.page-header h2 { margin: 0 0 6px; }
.page-header p { margin: 0; color: #7a8580; }
.header-actions { display: flex; gap: 8px; }
.member-hint { color: #7a8580; font-size: 12px; }
.muted { color: #9aa4a0; }
.list-toolbar {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 12px;
}
.form-hint { margin-left: 8px; color: #9aa4a0; font-size: 12px; }
.pending-hint {
  margin-bottom: 12px;
  padding: 8px 12px;
  border-radius: 6px;
  background: #f5f7fa;
  color: #5c6b66;
  font-size: 13px;
  line-height: 1.6;
}
.approve-target { color: #384140; font-size: 14px; }
.user-tabs :deep(.el-tabs__header) { margin-bottom: 12px; }
.reset-tip { margin-bottom: 14px; }
.temp-pwd {
  font-family: Consolas, Menlo, monospace;
  font-size: 16px;
  letter-spacing: 1px;
  color: #b88230;
  font-weight: 600;
  margin-right: 8px;
}
.reset-hint { margin-top: 12px; color: #7a8580; font-size: 12px; line-height: 1.6; }
</style>
