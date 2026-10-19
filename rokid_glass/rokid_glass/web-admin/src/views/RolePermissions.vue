<template>
  <div>
    <div class="page-header">
      <div>
        <h2>角色权限</h2>
        <p>
          配置每个角色能访问的功能。角色本身由系统定义（可增删角色会影响成员岗位归属），
          此处只调整权限；改动保存后对该角色的用户**即时生效**。
        </p>
      </div>
      <div class="header-actions">
        <span class="version">权限版本 v{{ permVersion }}</span>
        <el-button :loading="loading" @click="load">刷新</el-button>
        <el-button type="danger" plain :loading="resetting" @click="resetAll">恢复默认</el-button>
      </div>
    </div>

    <el-alert
      v-if="!isSuperAdmin"
      type="warning"
      :closable="false"
      show-icon
      title="您只能授予自己已拥有的权限；灰色的复选框表示超出您的授权范围或与角色层级冲突。"
      style="margin-bottom: 12px"
    />

    <el-card shadow="never" v-loading="loading">
      <el-table :data="groupedRows" :span-method="spanMethod" size="small" border>
        <el-table-column prop="groupName" label="分组" width="110" align="center">
          <template #default="{ row }">
            <span class="group-name">{{ row.groupName }}</span>
          </template>
        </el-table-column>

        <el-table-column label="权限" width="240">
          <template #default="{ row }">
            <div class="perm-cell">
              <span class="perm-label">{{ row.label }}</span>
              <el-tag v-if="row.org_level" size="small" type="warning" effect="plain">公司级</el-tag>
            </div>
            <div class="perm-key">{{ row.key }}</div>
            <div v-if="row.hint" class="perm-hint">{{ row.hint }}</div>
          </template>
        </el-table-column>

        <el-table-column
          v-for="role in roles"
          :key="role.key"
          :label="role.label"
          width="112"
          align="center"
        >
          <template #header>
            <div class="role-head">
              <span class="role-name" :class="{ 'org-role': role.is_org_level }">{{ role.label }}</span>
              <el-button
                v-if="role.editable"
                link
                type="primary"
                size="small"
                :loading="saving === role.key"
                :disabled="!isDirty(role.key)"
                @click="saveRole(role.key)"
              >
                {{ isDirty(role.key) ? '保存' : '已保存' }}
              </el-button>
              <span v-else class="muted tiny">全量</span>
            </div>
          </template>
          <template #default="{ row }">
            <el-checkbox
              :model-value="isChecked(role.key, row.key)"
              :disabled="isDisabled(role, row)"
              @change="(v: any) => toggle(role.key, row.key, Boolean(v))"
            />
          </template>
        </el-table-column>

        <template #empty>暂无可配置项</template>
      </el-table>

      <div class="legend">
        <span class="muted">
          勾选即授予该角色对应权限。项目级岗位不可持有「公司级」权限（复选框自动禁用）。
          超级管理员恒为全量权限，不可配置。
        </span>
      </div>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { permissionApi } from '@/api'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const isSuperAdmin = computed(() => auth.userInfo?.org_role === 'super_admin')

const loading = ref(false)
const saving = ref('')
const resetting = ref(false)
const permVersion = ref(0)

const roles = ref<any[]>([])
const permissions = ref<any[]>([])
const myPermissions = ref<string[]>([])
/** 编辑态 / 已保存态：按角色 key 存权限键数组，两者不同即为「有未保存改动」 */
const draft = ref<Record<string, string[]>>({})
const original = ref<Record<string, string[]>>({})

/** 按分组聚合行，并标记每组的首行/行数，供 span-method 合并「分组」列 */
const groupedRows = computed(() => {
  const buckets = new Map<string, any[]>()
  for (const item of permissions.value) {
    if (!buckets.has(item.group)) buckets.set(item.group, [])
    buckets.get(item.group)!.push(item)
  }
  const rows: any[] = []
  for (const [groupName, items] of buckets) {
    items.forEach((item, index) => {
      rows.push({ ...item, groupName, isGroupFirst: index === 0, groupSize: items.length })
    })
  }
  return rows
})

function spanMethod({ row, columnIndex }: any) {
  if (columnIndex !== 0) return { rowspan: 1, colspan: 1 }
  if (row.isGroupFirst) return { rowspan: row.groupSize, colspan: 1 }
  return { rowspan: 0, colspan: 0 }
}

function isChecked(roleKey: string, permKey: string) {
  return (draft.value[roleKey] || []).includes(permKey)
}

function toggle(roleKey: string, permKey: string, checked: boolean) {
  const next = new Set(draft.value[roleKey] || [])
  if (checked) next.add(permKey)
  else next.delete(permKey)
  draft.value[roleKey] = [...next].sort()
}

function isDirty(roleKey: string) {
  const a = [...(draft.value[roleKey] || [])].sort().join(',')
  const b = [...(original.value[roleKey] || [])].sort().join(',')
  return a !== b
}

/**
 * 复选框是否禁用。三条规则与后端校验同口径：
 * 1. 超管列不可编辑（恒全量，改了不生效）；
 * 2. 项目级岗位不能持有「公司级」权限；
 * 3. 不能授予自己没有的权限 —— 但**已勾上的允许取消**（收回权限不需要先拥有它）。
 */
function isDisabled(role: any, perm: any) {
  if (!role.editable) return true
  if (isChecked(role.key, perm.key)) return false
  if (perm.org_level && !role.is_org_level) return true
  if (!myPermissions.value.includes(perm.key)) return true
  return false
}

async function load() {
  loading.value = true
  try {
    const res: any = await permissionApi.matrix()
    roles.value = res.roles || []
    permissions.value = res.permissions || []
    myPermissions.value = res.my_permissions || []
    permVersion.value = res.perm_version ?? 0
    const d: Record<string, string[]> = {}
    const o: Record<string, string[]> = {}
    for (const role of roles.value) {
      d[role.key] = [...(role.permissions || [])]
      o[role.key] = [...(role.permissions || [])]
    }
    draft.value = d
    original.value = o
  } catch (error: any) {
    ElMessage.error(error.response?.data?.detail || '权限矩阵加载失败')
  } finally {
    loading.value = false
  }
}

async function saveRole(roleKey: string) {
  saving.value = roleKey
  try {
    const res: any = await permissionApi.updateRole(roleKey, draft.value[roleKey] || [])
    original.value[roleKey] = [...(draft.value[roleKey] || [])]
    if (res.perm_version) permVersion.value = res.perm_version
    const label = roles.value.find((r) => r.key === roleKey)?.label || roleKey
    if (!res.updated) ElMessage.info(`${label} 无变化`)
    else {
      const added = (res.added || []).length
      const removed = (res.removed || []).length
      ElMessage.success(`${label} 已保存（+${added} / -${removed}），即时生效`)
    }
  } catch (error: any) {
    ElMessage.error(error.response?.data?.detail || '保存失败')
  } finally {
    saving.value = ''
  }
}

async function resetAll() {
  await ElMessageBox.confirm(
    '将把所有角色的权限恢复为系统默认值，您在界面上做过的调整会全部丢失。确认继续？',
    '恢复默认权限',
    { type: 'warning', confirmButtonText: '恢复默认', cancelButtonText: '取消' },
  )
  resetting.value = true
  try {
    await permissionApi.reset()
    ElMessage.success('已恢复默认权限')
    await load()
  } catch (error: any) {
    ElMessage.error(error.response?.data?.detail || '恢复失败')
  } finally {
    resetting.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.page-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: 16px;
  gap: 16px;
}
.page-header h2 { margin: 0 0 6px; }
.page-header p { margin: 0; color: #7a8580; max-width: 720px; font-size: 13px; line-height: 1.6; }
.header-actions { display: flex; align-items: center; gap: 8px; flex-shrink: 0; }
.version { color: #9aa4a0; font-size: 12px; }
.muted { color: #9aa4a0; }
.tiny { font-size: 12px; }
.group-name { font-weight: 600; color: #384140; }
.perm-cell { display: flex; align-items: center; gap: 6px; }
.perm-label { font-weight: 500; }
.perm-key { color: #b0b8b4; font-size: 11px; margin-top: 2px; }
.perm-hint { color: #9aa4a0; font-size: 11px; margin-top: 2px; line-height: 1.4; }
.role-head { display: flex; flex-direction: column; align-items: center; gap: 2px; }
.role-name { font-weight: 600; font-size: 12px; }
.role-name.org-role { color: #d97706; }
.legend { margin-top: 10px; font-size: 12px; }
</style>
