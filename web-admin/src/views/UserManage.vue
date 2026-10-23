<template>
  <div>
    <div class="page-header">
      <div>
        <h2>用户管理</h2>
        <p>创建白泽账号、分配角色与项目，并查看微信绑定状态。</p>
      </div>
      <div class="header-actions">
        <!-- 次要操作收进下拉，避免一排六个按钮抢视线；「新增用户」是主操作，单独留在外面。
             总监等无 user_manage 的账号只剩「项目成员管理」一项，就不套下拉了。 -->
        <el-dropdown v-if="canManageUsers" trigger="click" @command="handleHeaderCommand">
          <el-button>
            更多操作<span class="caret">▾</span>
          </el-button>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item command="members">项目成员管理</el-dropdown-item>
              <el-dropdown-item v-if="canManageUsers" command="import" divided>
                批量导入
              </el-dropdown-item>
              <el-dropdown-item v-if="canManageUsers" command="export" :disabled="exporting">
                导出 Excel
                <!-- 下拉菜单 teleport 到 body，scoped 样式命不中，故用内联 -->
                <span style="color: var(--el-text-color-secondary); font-size: 12px">{{ exportScopeLabel }}</span>
              </el-dropdown-item>
              <el-dropdown-item
                v-if="canManageUsers"
                command="delete"
                divided
                style="color: var(--el-color-danger)"
              >
                删除账号
              </el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
        <el-button v-else @click="openMembers">项目成员管理</el-button>
        <!-- 新增/编辑用户需 user_manage，总监只有 settings → 隐藏，避免点击必然 403 -->
        <el-button v-if="canManageUsers" type="primary" @click="openCreate">新增用户</el-button>
      </div>
    </div>

    <el-tabs v-model="activeTab" class="user-tabs">
      <el-tab-pane name="list" label="账号列表">
        <el-card shadow="never">
          <div class="list-toolbar">
            <!-- 项目筛选（2026-10-21）：后端 GET /auth/users 对公司级账号（超管/管理员）
                 **不按项目过滤**（`_can_manage_all_projects` 直接短路），所以切换项目后
                 这里一直是全量账号。筛选放在前端做，口径与后端「非公司级」分支对齐：
                 该项目**成员**（project_members，归属真源）∪ 所属项目 = 该项目的账号。 -->
            <el-select
              v-model="projectFilter"
              size="small"
              style="width: 220px"
              :loading="memberScopeLoading"
            >
              <el-option label="全部项目" :value="0" />
              <el-option label="未分配项目" :value="NO_PROJECT" />
              <el-option
                v-for="p in projects"
                :key="p.id"
                :label="p.name"
                :value="p.id"
              />
            </el-select>
            <el-select v-model="auditFilter" size="small" style="width: 170px">
              <el-option label="全部审批状态" value="" />
              <el-option label="已通过（可登录）" value="approved" />
              <el-option label="待审批（不可登录）" value="pending" />
              <el-option label="已驳回（不可登录）" value="rejected" />
            </el-select>
            <span class="muted">共 {{ filteredUsers.length }} / {{ users.length }} 个账号</span>
            <span v-if="scopeHint" class="muted scope-hint">{{ scopeHint }}</span>
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
            <el-table-column label="密码" min-width="180">
              <template #default="{ row }">
                <template v-if="row.temp_password">
                  <div class="pwd-cell">
                    <el-input
                      :model-value="row.temp_password"
                      readonly
                      :type="pwdVisible(row.id) ? 'text' : 'password'"
                      size="small"
                      class="pwd-input"
                    />
                    <el-button
                      link
                      type="primary"
                      size="small"
                      @click="togglePwd(row.id)"
                    >
                      {{ pwdVisible(row.id) ? '隐藏' : '显示' }}
                    </el-button>
                  </div>
                  <div v-if="row.must_change_password" class="pwd-hint">强制改密</div>
                </template>
                <el-tooltip
                  v-else
                  content="真实密码不可见；仅显示管理员重置后的默认密码 admin123"
                  placement="top"
                >
                  <span class="muted">—</span>
                </el-tooltip>
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
            <el-table-column label="操作" width="260" fixed="right">
              <template #default="{ row }">
                <el-button v-if="canManageUsers" link type="primary" @click="openChangePassword(row)">修改密码</el-button>
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

    <!-- Step 2：项目成员管理（2026-10-23 抽为共享组件 MemberManageDialog，
         项目管理页「分配人员」同一弹窗双入口；默认项目口径见 defaultProjectId） -->
    <MemberManageDialog
      :visible="memberDialogVisible"
      :projects="projects"
      :default-project-id="defaultProjectId()"
      @update:visible="memberDialogVisible = $event"
      @changed="onMembersChanged"
    />

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

    <!-- 忘记密码兜底：管理员重置为统一默认口令 admin123 -->
    <el-dialog v-model="resetVisible" title="重置密码（恢复为默认密码）" width="540px">
      <el-alert
        type="warning"
        :closable="false"
        show-icon
        class="reset-tip"
        title="重置后该账号密码将恢复为统一默认口令"
        description="密码统一重置为 admin123，原有密码立即失效。请通过可靠渠道（电话/当面）转交本人；用户之后可随时自行修改密码。"
      />
      <div v-if="resetResult">
        <el-descriptions :column="1" border>
          <el-descriptions-item label="账号">{{ resetResult.username }}</el-descriptions-item>
          <el-descriptions-item label="默认密码">
            <span class="temp-pwd">{{ resetResult.temp_password }}</span>
            <el-button link type="primary" @click="copyTemp">复制</el-button>
          </el-descriptions-item>
        </el-descriptions>
        <div class="reset-hint">
          默认口令为 <b>admin123</b>；用户登录后可在「个人中心」自行修改，不改则一直沿用。
        </div>
      </div>
      <div v-else class="reset-hint">
        将把账号 <b>{{ resetTarget?.real_name }}</b>（{{ resetTarget?.username }}）的密码重置为统一默认口令 admin123。
      </div>
      <template #footer>
        <el-button @click="resetVisible = false">关闭</el-button>
        <el-button v-if="!resetResult" type="primary" :loading="resetLoading" @click="confirmReset">
          确认重置
        </el-button>
      </template>
    </el-dialog>

    <!-- 修改密码：管理员直接为账号设置一个已知新密码（权限 user_manage） -->
    <el-dialog v-model="changePwdVisible" title="修改密码" width="480px">
      <div class="approve-target">
        目标账号：<b>{{ changePwdTarget?.real_name }}</b>（{{ changePwdTarget?.username }}）
      </div>
      <el-form :model="changePwdForm" label-width="90px" style="margin-top: 12px">
        <el-form-item label="新密码" required>
          <el-input
            v-model="changePwdForm.password"
            type="password"
            show-password
            placeholder="至少 8 位；设置后立即生效"
          />
        </el-form-item>
        <el-form-item label="确认密码" required>
          <el-input
            v-model="changePwdForm.confirm"
            type="password"
            show-password
            placeholder="再次输入新密码"
          />
        </el-form-item>
      </el-form>
      <el-alert
        type="info"
        :closable="false"
        show-icon
        class="reset-tip"
        title="该操作直接覆盖账号密码"
        description="设置后账号原有密码立即失效，用户下次用新密码登录即可（不会强制改密）。"
      />
      <template #footer>
        <el-button @click="changePwdVisible = false">取消</el-button>
        <el-button type="primary" :loading="changePwdLoading" @click="submitChangePassword">
          确认修改
        </el-button>
      </template>
    </el-dialog>

    <!-- 删除账号：管理员在右上角选择并删除指定账号（不可删自己，带二次确认） -->
    <el-dialog v-model="deleteVisible" title="删除账号" width="480px">
      <el-alert
        type="warning"
        :closable="false"
        show-icon
        class="reset-tip"
        title="此操作不可恢复"
        description="删除后该账号及其微信绑定、项目成员等关联数据将一并清除；业务记录仅解除其归属、予以保留。"
      />
      <el-form label-width="90px" style="margin-top: 14px">
        <el-form-item label="选择账号" required>
          <el-select
            v-model="deleteTargetId"
            filterable
            placeholder="选择要删除的账号"
            style="width: 100%"
          >
            <el-option
              v-for="u in deletableUsers"
              :key="u.id"
              :label="`${u.real_name}（${u.username}）`"
              :value="u.id"
            />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="deleteVisible = false">取消</el-button>
        <el-button type="danger" :loading="deleteLoading" @click="confirmDelete">
          确认删除
        </el-button>
      </template>
    </el-dialog>

    <!-- 批量导入账号：先下载模板 → 选表 → 校验预览 → 确认写入 -->
    <el-dialog v-model="importVisible" title="批量导入账号" width="780px">
      <div class="import-step">
        <span class="step-no">1</span>
        <div class="step-body">
          <div class="step-title">下载模板，按列填写</div>
          <div class="step-desc">
            列序：用户名、姓名、主岗、公司级角色、所属项目、手机号、密码。
            必填「姓名」「主岗」；用户名留空会优先用手机号、否则自动生成；密码留空统一用下方默认值。
          </div>
          <el-button link type="primary" @click="downloadTemplate">下载导入模板（xlsx）</el-button>
        </div>
      </div>

      <div class="import-step">
        <span class="step-no">2</span>
        <div class="step-body">
          <div class="step-title">选择填好的表格</div>
          <el-upload
            :auto-upload="false"
            :limit="1"
            accept=".xlsx,.csv"
            :on-change="onImportFileChange"
            :on-remove="onImportFileRemove"
          >
            <el-button>选择文件</el-button>
            <template #tip>
              <div class="muted">支持 .xlsx / .csv，单次最多 500 行；同一文件重复导入会按用户名/手机号跳过已有账号。</div>
            </template>
          </el-upload>
          <div class="import-pwd">
            <span class="muted">密码（留空的行统一使用）</span>
            <el-input
              v-model="importPassword"
              size="small"
              class="pwd-input"
              placeholder="至少 8 位"
            />
          </div>
        </div>
      </div>

      <div v-if="importResult" class="import-result">
        <div class="import-summary">
          共 {{ importResult.total }} 行：校验通过 {{ importOkCount }} 行，失败
          {{ importResult.failed }} 行
          <span v-if="importResult.dry_run" class="muted">（本次只校验，未写入）</span>
          <span v-else class="muted">（已创建 {{ importResult.created }} 个账号）</span>
        </div>
        <el-table :data="importResult.rows" size="small" max-height="300" stripe>
          <el-table-column prop="line" label="行" width="60" />
          <el-table-column prop="real_name" label="姓名" width="100" />
          <el-table-column prop="username" label="用户名" width="150" />
          <el-table-column label="状态" width="90">
            <template #default="{ row }">
              <el-tag :type="row.status === 'ok' ? 'success' : 'danger'" size="small">
                {{ row.status === 'ok' ? (importResult.dry_run ? '可创建' : '已创建') : '失败' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="detail" label="说明" min-width="250" show-overflow-tooltip />
        </el-table>
      </div>

      <template #footer>
        <el-button @click="importVisible = false">关闭</el-button>
        <el-button :loading="importChecking" :disabled="!importFile" @click="runImport(true)">
          校验（不写入）
        </el-button>
        <el-button
          type="primary"
          :loading="importing"
          :disabled="!importFile"
          @click="runImport(false)"
        >
          确认导入
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { authApi, projectApi } from '@/api'
import { PROJECT_ROLE_OPTIONS, ORG_ROLE_OPTIONS, CHIEF_ROLE_KEYS, DEFAULT_ROLE, roleLabel } from '@/constants/role'
import { useAuthStore } from '@/stores/auth'
import MemberManageDialog from '@/components/MemberManageDialog.vue'

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
const route = useRoute()
// 「角色权限」配置页已下线（2026-10-20），入口与跳转一并移除。
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

/**
 * 「默认操作哪个项目」的唯一取值口径（2026-10-21 修）。
 *
 * 🔴 原先三处（成员弹窗 openMembers / 审批 openApprove / 建号 resetForm）都写
 * `projects.value[0]?.id`，而后端 `list_projects` 是 `ORDER BY created_at DESC`，
 * 于是 **projects[0] = 最新建的项目**，跟用户顶栏选中的项目毫无关系。线上实测：
 * 超管切到项目 1，点「项目成员管理」弹窗却默认选中最新建的「111」，显示的是
 * 111 的成员；审批入册 / 新增用户同样会**静默把数据写进最新建的项目**（写入型更危险）。
 *
 * 优先级：筛选器选中值 → 顶栏当前项目 → 才回落 projects[0]。
 * 「全部项目」视角下 auth.currentProjectId 仍有值（视角只改读取范围，不动
 * users.project_id），所以照用；真取不到时回落，避免下拉空白无法提交。
 *
 * 现调用方：审批 openApprove / 建号 resetForm / 成员弹窗（经 prop 传入，2026-10-23）。
 */
function defaultProjectId(): number | undefined {
  if (projectFilter.value > 0) return projectFilter.value
  if (auth.currentProjectId) return auth.currentProjectId
  return projects.value[0]?.id
}

// ---- 项目筛选（2026-10-21，纯前端）----
// 后端 `GET /auth/users` 对公司级账号短路了项目过滤（`_can_manage_all_projects`），
// 所以超管切项目后账号列表仍是全量。这里在前端补一层筛选，且**默认跟随顶栏当前项目**，
// 让「切到哪个项目就只看哪个项目的人」符合直觉。口径对齐后端非公司级分支：
// 命中 = 该项目**成员**（project_members，归属真源）∪ 所属项目 = 该项目的账号。
const NO_PROJECT = -1 // 「未分配项目」哨兵值（project_id 为空的账号）
const projectFilter = ref<number>(0) // 0 = 全部项目
const memberScopeLoading = ref(false)
// 选中项目的成员 user_id 集合（懒加载 + 缓存，避免每次筛选都打接口）
const memberScope = ref<Record<number, Set<number>>>({})

const scopeHint = computed(() => {
  if (projectFilter.value === 0) return ''
  if (projectFilter.value === NO_PROJECT) return '（未分配项目的账号）'
  const name = projects.value.find((p) => p.id === projectFilter.value)?.name
  return name ? `（${name}：成员 ∪ 所属项目）` : ''
})

async function ensureMemberScope(projectId: number) {
  if (projectId <= 0 || memberScope.value[projectId]) return
  memberScopeLoading.value = true
  try {
    // include_left=true：连已离场成员一并纳入，和后端「同项目成员」子查询语义一致
    const res: any = await projectApi.members(projectId, true)
    const ids = new Set<number>((res?.items || []).map((m: any) => m.user_id))
    memberScope.value = { ...memberScope.value, [projectId]: ids }
  } catch {
    // 拉不到成员（如无 member_manage、接口异常）就退化成「仅按所属项目过滤」，
    // 但**不写缓存** —— 否则一次失败会永久锁死，切回来也不再重试。
    // filteredUsers 在 members 为 undefined 时只按 project_id 匹配，不阻塞列表。
  } finally {
    memberScopeLoading.value = false
  }
}

const filteredUsers = computed(() => {
  let list = users.value
  if (auditFilter.value) {
    list = list.filter((item) => (item.audit_status || 'approved') === auditFilter.value)
  }
  const pf = projectFilter.value
  if (pf === NO_PROJECT) {
    list = list.filter((item) => !item.project_id)
  } else if (pf > 0) {
    const members = memberScope.value[pf]
    list = list.filter((item) => item.project_id === pf || (members ? members.has(item.id) : false))
  }
  return list
})

// ---- 「密码」列：逐行显隐临时口令（仅临时口令可见，真实密码永不可见）----
const showPwdMap = reactive<Record<number, boolean>>({})
function pwdVisible(id?: number) {
  return id ? !!showPwdMap[id] : false
}
function togglePwd(id?: number) {
  if (!id) return
  showPwdMap[id] = !showPwdMap[id]
}

// ---- 项目成员管理（Step 2；2026-10-23 起弹窗本体在 components/MemberManageDialog.vue）----
// 本页只保留入口（打开）与联动（成员变动后刷新筛选缓存）。弹窗打开时自行
// 拉取账号列表（authApi.users，后端按身份过滤），不消费本页的 users。
const memberDialogVisible = ref(false)
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

// 选中具体项目 → 预取该项目成员集合（供 filteredUsers 同步过滤）
watch(projectFilter, (pid) => { if (pid > 0) ensureMemberScope(pid) }, { immediate: true })
// 顶栏切换项目后，本页的筛选默认跟随当前项目（切回「全部项目」视角则不锁）
watch(
  () => auth.currentProjectId,
  (pid) => { if (!auth.allProjects && pid) projectFilter.value = pid },
)

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
  // 🔴 写入型默认值：原先 projects[0] = 最新建的项目，会把审批通过的人
  // 静默落进不相干的项目（2026-10-21 修，与 openMembers 同一口径）。
  approveForm.project_id = defaultProjectId()
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
    ElMessage.success('已重置为默认密码 admin123')
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

// ---- 修改密码：管理员直接为账号设一个已知新密码（权限 user_manage）----
const changePwdVisible = ref(false)
const changePwdLoading = ref(false)
const changePwdTarget = ref<any>(null)
const changePwdForm = reactive({
  password: '',
  confirm: '',
})

function openChangePassword(row: any) {
  if (!canManageUsers.value) {
    ElMessage.warning('需要「账号管理」权限，请联系超级管理员')
    return
  }
  changePwdTarget.value = row
  changePwdForm.password = ''
  changePwdForm.confirm = ''
  changePwdVisible.value = true
}

async function submitChangePassword() {
  if (!changePwdTarget.value) return
  if (changePwdForm.password.length < 8) return ElMessage.warning('新密码至少 8 位')
  if (changePwdForm.password !== changePwdForm.confirm) {
    return ElMessage.warning('两次输入的密码不一致')
  }
  changePwdLoading.value = true
  try {
    // 复用 updateUser 的 new_password：设置后立即生效，并清空临时口令与强制改密标记
    await authApi.updateUser(changePwdTarget.value.id, {
      new_password: changePwdForm.password,
    })
    ElMessage.success('密码已修改')
    changePwdVisible.value = false
    await loadData()
  } catch (error: any) {
    ElMessage.error(error.response?.data?.detail || '修改失败')
  } finally {
    changePwdLoading.value = false
  }
}

// ---- 删除账号：管理员在右上角选择并删除指定账号（不可删自己，带二次确认）----
const deleteVisible = ref(false)
const deleteLoading = ref(false)
const deleteTargetId = ref<number | null>(null)

// 可删除列表：排除当前登录账号（后端也会拦截自删，避免把自己锁死）
const deletableUsers = computed(() =>
  users.value.filter((item) => item.id !== auth.userInfo?.user_id),
)

function openDelete() {
  if (!canManageUsers.value) {
    ElMessage.warning('需要「账号管理」权限，请联系超级管理员')
    return
  }
  deleteTargetId.value = null
  deleteVisible.value = true
}

async function confirmDelete() {
  if (!deleteTargetId.value) return ElMessage.warning('请选择要删除的账号')
  const target = users.value.find((item) => item.id === deleteTargetId.value)
  if (!target) return
  try {
    await ElMessageBox.confirm(
      `确认删除账号「${target.real_name}（${target.username}）」？\n`
        + '此操作不可恢复，其微信绑定、项目成员等关联数据将一并清除。',
      '删除账号',
      { type: 'warning', confirmButtonText: '确认删除', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  deleteLoading.value = true
  try {
    await authApi.deleteUser(deleteTargetId.value)
    ElMessage.success('账号已删除')
    deleteVisible.value = false
    await loadData()
  } catch (error: any) {
    ElMessage.error(error.response?.data?.detail || '删除失败')
  } finally {
    deleteLoading.value = false
  }
}

function resetForm() {
  form.username = ''
  form.real_name = ''
  form.role = DEFAULT_ROLE
  form.org_role = ''
  // 🔴 写入型默认值：原先 projects[0] = 最新建的项目（2026-10-21 修）
  form.project_id = defaultProjectId()
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

// ---- 项目成员管理（Step 2；弹窗本体见 components/MemberManageDialog.vue）----
function openMembers() {
  // 默认项目由弹窗经 defaultProjectId prop 取值（筛选器选中值 → 顶栏当前项目 →
  // 回落 projects[0]），每次打开都重置，不停在上次的项目上。
  // 🔴 不能取 projects[0] 了事：后端 list_projects 是 created_at DESC，
  // [0] = 最新建的项目，与顶栏选中项目无关（2026-10-21 线上实测复现）。
  memberDialogVisible.value = true
}

/**
 * 成员变动（加入/移出/复职）后的页面联动。
 *
 * 账号列表的项目筛选用的是 memberScope 缓存（该项目成员 user_id 集合），
 * 成员一变缓存即过期；清掉后按当前筛选重取，再顺带刷新账号列表本身。
 */
async function onMembersChanged() {
  memberScope.value = {}
  if (projectFilter.value > 0) await ensureMemberScope(projectFilter.value)
  await loadData()
}

// ---- 批量导入 / 导出（2026-10-20）----
/** 顶部「更多操作」下拉的分发：原来是一排按钮，收进菜单后统一在这里路由 */
function handleHeaderCommand(command: string) {
  switch (command) {
    case 'members':
      openMembers()
      break
    case 'import':
      openImport()
      break
    case 'export':
      void exportAccounts()
      break
    case 'delete':
      openDelete()
      break
  }
}

// 现场一次进几十号人时，逐个点「新增用户」不现实：先下载模板 → 填表 → 校验预览 → 确认导入，
// 导完直接导出一张含初始口令的 Excel 发给各人。
const importVisible = ref(false)
const importFile = ref<File | null>(null)
const importPassword = ref('admin123')
const importChecking = ref(false)
const importing = ref(false)
const importResult = ref<any>(null)
const exporting = ref(false)
const importOkCount = computed(
  () => ((importResult.value?.rows || []) as any[]).filter((row) => row.status === 'ok').length,
)

function saveBlob(blob: Blob, filename: string) {
  const url = window.URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  anchor.click()
  window.URL.revokeObjectURL(url)
}

/** 下载类接口用了 responseType=blob，出错时 detail 藏在 Blob 里，需要解出来 */
async function blobErrorText(error: any, fallback: string): Promise<string> {
  const data = error?.response?.data
  if (data instanceof Blob) {
    try {
      const parsed = JSON.parse(await data.text())
      return parsed?.detail || fallback
    } catch {
      return fallback
    }
  }
  return data?.detail || fallback
}

function openImport() {
  importVisible.value = true
  importFile.value = null
  importResult.value = null
  importPassword.value = 'admin123'
}

async function downloadTemplate() {
  try {
    saveBlob((await authApi.userImportTemplate()) as any, '白泽_账号批量导入模板.xlsx')
  } catch (error: any) {
    ElMessage.error(await blobErrorText(error, '模板下载失败'))
  }
}

function onImportFileChange(file: any) {
  importFile.value = file?.raw || null
  importResult.value = null
}

function onImportFileRemove() {
  importFile.value = null
  importResult.value = null
}

async function runImport(dryRun: boolean) {
  if (!importFile.value) return ElMessage.warning('请先选择要导入的表格')
  const password = importPassword.value.trim()
  if (password.length < 8) return ElMessage.warning('初始密码至少 8 位')

  const flag = dryRun ? importChecking : importing
  flag.value = true
  try {
    const result: any = await authApi.batchImportUsers(importFile.value, {
      defaultPassword: password,
      dryRun,
    })
    importResult.value = result
    if (dryRun) {
      const okRows = ((result.rows || []) as any[]).filter((row) => row.status === 'ok').length
      ElMessage.success(`校验完成：${okRows} 行可导入`)
    } else {
      ElMessage.success(
        `已创建 ${result.created} 个账号${result.failed ? `，${result.failed} 行失败` : ''}`,
      )
      await loadData()
    }
  } catch (error: any) {
    ElMessage.error(error.response?.data?.detail || (dryRun ? '校验失败' : '导入失败'))
  } finally {
    flag.value = false
  }
}

/**
 * 「导出 Excel」菜单项上的范围提示。
 *
 * 账号表带**初始口令明文**，导出范围错了等于把别的项目的口令发出去，
 * 所以点之前就要让人看清这次导的是谁 —— 不能等文件下载完才发现不对。
 */
const exportScopeLabel = computed(() => {
  if (projectFilter.value === NO_PROJECT) return '（当前不可导出）'
  if (projectFilter.value > 0) return `（${projectName(projectFilter.value)}）`
  return '（全部项目）'
})

async function exportAccounts() {
  // 🔴 导出跟随页面筛选（2026-10-21）：屏幕上是哪个项目的人，导出的就是那批人。
  // 「未分配项目」后端参数表达不了（只接受具体项目 id），**不能静默导全量** ——
  // 账号表里带初始口令明文，导错范围等于把别的项目的口令发出去，故明确拒绝并给替代做法。
  if (projectFilter.value === NO_PROJECT) {
    ElMessage.warning('「未分配项目」不支持导出，请切到具体项目或「全部项目」后再导出')
    return
  }
  const pid = projectFilter.value > 0 ? projectFilter.value : null
  exporting.value = true
  try {
    saveBlob((await authApi.exportUsers(pid)) as any, exportFileName(pid))
    ElMessage.success(pid ? `已导出「${projectName(pid)}」的账号表` : '账号表已导出（全部项目）')
  } catch (error: any) {
    ElMessage.error(await blobErrorText(error, '导出失败'))
  } finally {
    exporting.value = false
  }
}

function exportFileName(projectId?: number | null) {
  const now = new Date()
  const pad = (value: number) => String(value).padStart(2, '0')
  // 带项目名，避免同时导多个项目时分不清谁是谁（文件名非法字符替换掉）
  const name = projectId ? projectName(projectId) : ''
  const safe = name.replace(/[\\/:*?"<>|#]/g, '_').slice(0, 40)
  const part = safe ? `_${safe}` : ''
  return `白泽_账号表${part}_${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}_${pad(now.getHours())}${pad(now.getMinutes())}.xlsx`
}

onMounted(async () => {
  // 支持项目管理页「分配人员」带 project_id 跳转过来：直接锁定该项目筛选
  const q = Number(route.query.project_id || 0)
  if (q > 0) projectFilter.value = q
  else if (!auth.allProjects && auth.currentProjectId) projectFilter.value = auth.currentProjectId
  await loadData()
})
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
.header-actions { display: flex; gap: 8px; align-items: center; }
.header-actions .caret { margin-left: 4px; font-size: 12px; opacity: 0.65; }
.muted { color: #9aa4a0; }
.list-toolbar {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 12px;
}
.scope-hint { font-size: 12px; }
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
.pwd-cell { display: flex; align-items: center; gap: 6px; }
.pwd-input { width: 130px; }
.pwd-input :deep(.el-input__inner) {
  font-family: Consolas, Menlo, monospace;
  letter-spacing: 1px;
}
.pwd-hint { color: #b88230; font-size: 12px; margin-top: 2px; }
/* ---- 批量导入 ---- */
.import-step {
  display: flex;
  gap: 12px;
  padding: 12px 0;
  border-bottom: 1px dashed #e4e9e6;
}
.import-step:last-of-type { border-bottom: none; }
.step-no {
  flex: 0 0 22px;
  width: 22px;
  height: 22px;
  margin-top: 2px;
  border-radius: 50%;
  background: #13795b;
  color: #fff;
  font-size: 12px;
  display: flex;
  align-items: center;
  justify-content: center;
}
.step-body { flex: 1; min-width: 0; }
.step-title { font-weight: 600; color: #2f3a37; margin-bottom: 4px; }
.step-desc { color: #7a8580; font-size: 12px; line-height: 1.7; margin-bottom: 6px; }
.import-pwd { display: flex; align-items: center; gap: 8px; margin-top: 12px; }
.import-result { margin-top: 14px; padding-top: 12px; border-top: 1px solid #eef2f0; }
.import-summary { font-size: 13px; color: #384140; margin-bottom: 8px; }
</style>
