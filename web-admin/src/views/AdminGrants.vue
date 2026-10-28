<template>
  <div class="admin-grants">
    <el-card shadow="never">
      <template #header>
        <div class="card-head">
          <span class="block-title">管理员与项目授权</span>
          <el-switch v-model="onlyAdmin" size="small" active-text="只看管理员" />
        </div>
      </template>
      <el-alert
        type="info"
        :closable="false"
        show-icon
        title="管理员 = 项目负责人，默认只能查看自己的项目；「可授权查看的管理员」为该管理员额外开放的管理员——勾选某管理员即授权其查看该管理员名下的全部项目（含其所有成员项目），与项目成员关系相互独立。"
        style="margin-bottom: 12px"
      />
      <el-table :data="tableRows" v-loading="loading" stripe size="small" row-key="id">
        <el-table-column prop="real_name" label="姓名" min-width="100" />
        <el-table-column prop="username" label="用户名" min-width="120" />
        <el-table-column label="身份" width="110">
          <template #default="{ row }">
            <el-tag v-if="row.org_role === 'super_admin'" type="danger" size="small">超级管理员</el-tag>
            <el-tag v-else-if="row.org_role === 'admin'" type="warning" size="small">管理员</el-tag>
            <el-tag v-else size="small" type="info">普通成员</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="可授权查看的管理员" min-width="340">
          <template #default="{ row }">
            <span v-if="row.org_role === 'super_admin'" class="cell-muted">超级管理员可查看全部项目</span>
            <span v-else-if="row.org_role !== 'admin'" class="cell-muted">设为管理员后可授权</span>
            <el-select
              v-else
              v-model="adminSelection[row.id]"
              multiple
              filterable
              collapse-tags
              collapse-tags-tooltip
              size="small"
              placeholder="未授权其他管理员"
              style="width: 100%"
              :loading="savingRow === row.id"
              @change="(val: number[]) => onAdminsChange(row, val)"
            >
              <el-option
                v-for="a in otherAdmins(row)"
                :key="a.id"
                :label="`${a.real_name}（${a.username}）· ${a.project_count} 个项目`"
                :value="a.id"
              />
            </el-select>
          </template>
        </el-table-column>
        <el-table-column label="授权记录" min-width="220">
          <template #default="{ row }">
            <el-tooltip v-if="grantsOf(row.id).length" placement="top">
              <template #content>
                <div v-for="(g, i) in grantsOf(row.id)" :key="i" class="tip-line">
                  {{ userName(g.grantor_admin_id) }}（{{ adminProjectCount(g.grantor_admin_id) }} 个项目） · {{ userName(g.grantor_user_id) || '—' }} · {{ fmtTime(g.created_at) }}
                </div>
              </template>
              <span class="cell-link">{{ grantsOf(row.id).length }} 条授权</span>
            </el-tooltip>
            <span v-else class="cell-muted">—</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="130" fixed="right">
          <template #default="{ row }">
            <el-button
              v-if="row.org_role !== 'admin'"
              link
              type="warning"
              :disabled="row.org_role === 'super_admin'"
              @click="setAdmin(row)"
            >设为管理员</el-button>
            <el-button
              v-else
              link
              type="danger"
              @click="clearAdmin(row)"
            >取消管理员</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { adminGrantsApi } from '@/api'

const loading = ref(false)
const onlyAdmin = ref(false)
const savingRow = ref<number | null>(null)
const admins = ref<any[]>([])
const grants = ref<any[]>([])
// 每行管理员已勾选的「源管理员」；savedSelection 为服务端快照，用于算增量
const adminSelection = ref<Record<number, number[]>>({})
const savedSelection = ref<Record<number, number[]>>({})

const tableRows = computed(() => (onlyAdmin.value ? admins.value.filter((u) => u.org_role === 'admin') : admins.value))

const userName = (id?: number) => {
  const u = admins.value.find((x) => x.id === id)
  return u ? `${u.real_name}（${u.username}）` : ''
}
// 可作为授权来源的「其他管理员」（不含超管、不含自己）
const otherAdmins = (row: any) => admins.value.filter((u) => u.org_role === 'admin' && u.id !== row.id)
const adminProjectCount = (id?: number) => {
  const u = admins.value.find((x) => x.id === id)
  return u ? u.project_count || 0 : 0
}
const fmtTime = (t?: string) => (t ? t.replace('T', ' ').slice(0, 16) : '—')
const grantsOf = (granteeId: number) => grants.value.filter((g) => g.grantee_user_id === granteeId)

function buildSelection() {
  const map: Record<number, number[]> = {}
  for (const u of admins.value) map[u.id] = []
  for (const g of grants.value) {
    if (!map[g.grantee_user_id]) map[g.grantee_user_id] = []
    map[g.grantee_user_id].push(g.grantor_admin_id)
  }
  adminSelection.value = JSON.parse(JSON.stringify(map))
  savedSelection.value = JSON.parse(JSON.stringify(map))
}

async function loadAll() {
  loading.value = true
  try {
    const data: any = await adminGrantsApi.list()
    admins.value = data.admins || []
    grants.value = data.grants || []
    buildSelection()
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || '加载失败')
  } finally {
    loading.value = false
  }
}

async function onAdminsChange(row: any, val: number[]) {
  const before = savedSelection.value[row.id] || []
  const added = val.filter((id) => !before.includes(id))
  const removed = before.filter((id) => !val.includes(id))
  if (!added.length && !removed.length) return
  savingRow.value = row.id
  try {
    for (const aid of added) await adminGrantsApi.grant(row.id, aid)
    for (const aid of removed) await adminGrantsApi.revoke(row.id, aid)
    const parts: string[] = []
    if (added.length) parts.push(`新增 ${added.length} 个`)
    if (removed.length) parts.push(`撤销 ${removed.length} 个`)
    ElMessage.success(`「${row.real_name}」的管理员授权已更新（${parts.join('，')}）`)
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || '授权更新失败')
  } finally {
    savingRow.value = null
    await loadAll()
  }
}

async function setAdmin(row: any) {
  try {
    await ElMessageBox.confirm(`确认将「${row.real_name}（${row.username}）」设为管理员（项目负责人）？`, '设为管理员', {
      type: 'warning',
      confirmButtonText: '确认',
      cancelButtonText: '取消',
    })
  } catch {
    return
  }
  try {
    await adminGrantsApi.setAdmin(row.id)
    ElMessage.success('已设为管理员')
    await loadAll()
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || '操作失败')
  }
}

async function clearAdmin(row: any) {
  try {
    await ElMessageBox.confirm(`确认取消「${row.real_name}（${row.username}）」的管理员身份？`, '取消管理员', {
      type: 'warning',
      confirmButtonText: '确认',
      cancelButtonText: '取消',
    })
  } catch {
    return
  }
  try {
    await adminGrantsApi.clearAdmin(row.id)
    ElMessage.success('已取消管理员')
    await loadAll()
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || '操作失败')
  }
}

onMounted(loadAll)
</script>

<style scoped>
.admin-grants {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.block-title {
  font-weight: 600;
}
.cell-muted {
  color: var(--el-text-color-placeholder);
}
.cell-link {
  color: var(--el-color-primary);
  cursor: default;
}
.tip-line {
  line-height: 1.6;
}
</style>
