<template>
  <div class="admin-grants">
    <el-card class="block" shadow="never">
      <template #header>
        <span class="block-title">一、授权谁是管理员（项目负责人）</span>
      </template>
      <el-alert
        type="info"
        :closable="false"
        show-icon
        title="管理员 = 项目负责人，默认只能查看自己的项目；此处决定谁拥有管理员身份（公司级 org_role）。"
        style="margin-bottom: 12px"
      />
      <el-table :data="users" v-loading="loading" stripe size="small">
        <el-table-column prop="id" label="ID" width="70" />
        <el-table-column prop="real_name" label="姓名" min-width="100" />
        <el-table-column prop="username" label="用户名" min-width="110" />
        <el-table-column label="当前身份" min-width="130">
          <template #default="{ row }">
            <el-tag v-if="row.org_role === 'super_admin'" type="danger" size="small">超级管理员</el-tag>
            <el-tag v-else-if="row.org_role === 'admin'" type="warning" size="small">管理员</el-tag>
            <el-tag v-else size="small" type="info">普通成员</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="140" fixed="right">
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

    <el-card class="block" shadow="never">
      <template #header>
        <span class="block-title">二、授权管理员查看其他管理员的项目</span>
      </template>
      <el-alert
        type="info"
        :closable="false"
        show-icon
        title="被授权的管理员会出现在对方项目的切换列表中，可查看该项目数据。授权即录入一条跨项目查看记录，与项目成员关系相互独立。"
        style="margin-bottom: 12px"
      />
      <el-table
        :data="grantTableData"
        v-loading="loading"
        stripe
        size="small"
        :row-class-name="grantRowClass"
      >
        <el-table-column label="管理员" min-width="200">
          <template #default="{ row }">
            <el-select
              v-if="row.__new"
              v-model="grantForm.grantee"
              filterable
              size="small"
              placeholder="选择管理员"
              style="width: 100%"
            >
              <el-option
                v-for="a in adminUsers"
                :key="a.id"
                :label="`${a.real_name}（${a.username}）`"
                :value="a.id"
              />
            </el-select>
            <span v-else>{{ userName(row.grantee_user_id) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="可查看的项目" min-width="280">
          <template #default="{ row }">
            <el-select
              v-if="row.__new"
              v-model="grantForm.projects"
              multiple
              filterable
              collapse-tags
              collapse-tags-tooltip
              size="small"
              placeholder="可多选"
              style="width: 100%"
            >
              <el-option
                v-for="p in projects"
                :key="p.id"
                :label="`${p.name}（${p.status === 'active' ? '进行中' : p.status}）`"
                :value="p.id"
              />
            </el-select>
            <span v-else>{{ projectName(row.project_id) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="授权人" min-width="130">
          <template #default="{ row }">
            <span v-if="row.__new" class="cell-muted">提交后自动记录</span>
            <span v-else>{{ userName(row.grantor_user_id) || '—' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="授权时间" min-width="150">
          <template #default="{ row }">
            <span v-if="row.__new" class="cell-muted">—</span>
            <span v-else>{{ row.created_at ? row.created_at.replace('T', ' ').slice(0, 16) : '—' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="100" fixed="right">
          <template #default="{ row }">
            <el-button
              v-if="row.__new"
              link
              type="primary"
              :loading="grantSaving"
              @click="doGrant"
            >授权</el-button>
            <el-button v-else link type="danger" @click="revokeGrant(row)">撤销</el-button>
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
const grantSaving = ref(false)
const users = ref<any[]>([])
const projects = ref<any[]>([])
const grants = ref<any[]>([])
const grantForm = ref<{ grantee: number | null; projects: number[] }>({ grantee: null, projects: [] })

const adminUsers = computed(() => users.value.filter((u) => u.org_role === 'admin'))
// 表格首行为「新增授权」录入行，其余为已存在的授权记录
const grantTableData = computed<any[]>(() => [{ __new: true }, ...grants.value])
const grantRowClass = ({ row }: any) => (row.__new ? 'grant-new-row' : '')

const userName = (id?: number) => {
  const u = users.value.find((x) => x.id === id)
  return u ? `${u.real_name}（${u.username}）` : ''
}
const projectName = (id?: number) => {
  const p = projects.value.find((x) => x.id === id)
  return p ? p.name : ''
}

async function loadAll() {
  loading.value = true
  try {
    const data: any = await adminGrantsApi.list()
    users.value = data.admins || []
    projects.value = data.projects || []
    grants.value = data.grants || []
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || '加载失败')
  } finally {
    loading.value = false
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

async function doGrant() {
  if (!grantForm.value.grantee || !grantForm.value.projects.length) {
    ElMessage.warning('请选择管理员和至少一个项目')
    return
  }
  grantSaving.value = true
  try {
    for (const pid of grantForm.value.projects) {
      await adminGrantsApi.grant(grantForm.value.grantee!, pid)
    }
    ElMessage.success('授权成功')
    grantForm.value = { grantee: null, projects: [] }
    await loadAll()
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || '授权失败')
  } finally {
    grantSaving.value = false
  }
}

async function revokeGrant(row: any) {
  try {
    await ElMessageBox.confirm('确认撤销该跨项目查看授权？', '撤销授权', {
      type: 'warning',
      confirmButtonText: '确认撤销',
      cancelButtonText: '取消',
    })
  } catch {
    return
  }
  try {
    await adminGrantsApi.revoke(row.grantee_user_id, row.project_id)
    ElMessage.success('已撤销')
    await loadAll()
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || '撤销失败')
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
.block-title {
  font-weight: 600;
}
.cell-muted {
  color: var(--el-text-color-placeholder);
}
:deep(.el-table__row.grant-new-row > td.el-table__cell) {
  background: var(--el-fill-color-lighter);
}
</style>
