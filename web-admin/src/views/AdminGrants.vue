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
        title="管理员 = 项目负责人，默认只能查看自己的项目；「可查看的其他管理员项目」为该管理员额外开放的项目，选择即保存，与项目成员关系相互独立。"
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
        <el-table-column label="可查看的其他管理员项目" min-width="320">
          <template #default="{ row }">
            <span v-if="row.org_role === 'super_admin'" class="cell-muted">超级管理员可查看全部项目</span>
            <span v-else-if="row.org_role !== 'admin'" class="cell-muted">设为管理员后可授权</span>
            <el-select
              v-else
              v-model="projectSelection[row.id]"
              multiple
              filterable
              collapse-tags
              collapse-tags-tooltip
              size="small"
              placeholder="未授权其他项目"
              style="width: 100%"
              :loading="savingRow === row.id"
              @change="(val: number[]) => onProjectsChange(row, val)"
            >
              <el-option
                v-for="p in projects"
                :key="p.id"
                :label="`${p.name}（${p.status === 'active' ? '进行中' : p.status}）`"
                :value="p.id"
              />
            </el-select>
          </template>
        </el-table-column>
        <el-table-column label="授权记录" min-width="170">
          <template #default="{ row }">
            <el-tooltip v-if="grantsOf(row.id).length" placement="top">
              <template #content>
                <div v-for="(g, i) in grantsOf(row.id)" :key="i" class="tip-line">
                  {{ projectName(g.project_id) }} · {{ userName(g.grantor_user_id) || '—' }} · {{ fmtTime(g.created_at) }}
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
const users = ref<any[]>([])
const projects = ref<any[]>([])
const grants = ref<any[]>([])
// 每行管理员已勾选的项目；savedSelection 为服务端快照，用于算增量
const projectSelection = ref<Record<number, number[]>>({})
const savedSelection = ref<Record<number, number[]>>({})

const tableRows = computed(() => (onlyAdmin.value ? users.value.filter((u) => u.org_role === 'admin') : users.value))

const userName = (id?: number) => {
  const u = users.value.find((x) => x.id === id)
  return u ? `${u.real_name}（${u.username}）` : ''
}
const projectName = (id?: number) => {
  const p = projects.value.find((x) => x.id === id)
  return p ? p.name : ''
}
const fmtTime = (t?: string) => (t ? t.replace('T', ' ').slice(0, 16) : '—')
const grantsOf = (granteeId: number) => grants.value.filter((g) => g.grantee_user_id === granteeId)

function buildSelection() {
  const map: Record<number, number[]> = {}
  for (const u of users.value) map[u.id] = []
  for (const g of grants.value) {
    if (!map[g.grantee_user_id]) map[g.grantee_user_id] = []
    map[g.grantee_user_id].push(g.project_id)
  }
  projectSelection.value = JSON.parse(JSON.stringify(map))
  savedSelection.value = JSON.parse(JSON.stringify(map))
}

async function loadAll() {
  loading.value = true
  try {
    const data: any = await adminGrantsApi.list()
    users.value = data.admins || []
    projects.value = data.projects || []
    grants.value = data.grants || []
    buildSelection()
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || '加载失败')
  } finally {
    loading.value = false
  }
}

async function onProjectsChange(row: any, val: number[]) {
  const before = savedSelection.value[row.id] || []
  const added = val.filter((id) => !before.includes(id))
  const removed = before.filter((id) => !val.includes(id))
  if (!added.length && !removed.length) return
  savingRow.value = row.id
  try {
    for (const pid of added) await adminGrantsApi.grant(row.id, pid)
    for (const pid of removed) await adminGrantsApi.revoke(row.id, pid)
    const parts: string[] = []
    if (added.length) parts.push(`新增 ${added.length} 个`)
    if (removed.length) parts.push(`撤销 ${removed.length} 个`)
    ElMessage.success(`「${row.real_name}」的项目授权已更新（${parts.join('，')}）`)
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
