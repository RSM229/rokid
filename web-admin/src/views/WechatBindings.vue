<template>
  <div class="wechat-binding-page">
    <el-card shadow="never">
      <template #header>
        <div class="card-header">
          <div>
            <span class="title">微信绑定管理</span>
            <span class="subtitle">
              一个微信只能绑定一个账号；解绑后该微信可重新注册或绑定其他账号
            </span>
          </div>
          <div class="header-actions">
            <el-input
              v-model="keyword"
              placeholder="用户名 / 姓名 / 手机号"
              clearable
              style="width: 220px"
              @keyup.enter="load"
              @clear="load"
            />
            <el-button @click="load">查询</el-button>
            <el-button @click="load">
              <el-icon><Refresh /></el-icon>
              <span style="margin-left: 4px">刷新</span>
            </el-button>
          </div>
        </div>
      </template>

      <el-table :data="items" v-loading="loading" stripe>
        <el-table-column label="绑定账号" min-width="140">
          <template #default="{ row }">
            <div class="user-cell">
              <span class="uname">{{ row.username || '（账号已删除）' }}</span>
              <span class="realname">{{ row.real_name || '-' }}</span>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="角色" min-width="150">
          <template #default="{ row }">
            <el-tag v-if="row.org_role" type="warning" size="small">
              公司级 · {{ ORG_ROLE_LABELS[row.org_role] || row.org_role }}
            </el-tag>
            <el-tag v-else size="small">{{ row.role_label || row.role || '-' }}</el-tag>
            <!--
              🔴 2026-10-20：公司级「管理员」＝项目负责人，现场要用小程序，已放行。
              此前把「公司级」一律标成不可用，会误导现场负责人来解绑自己。
              现在只对后端判定的真正不可用（超管/禁用/待审/被拒）打标签，
              并用 tooltip 展示后端给的 mini_unusable_reason，前端不再自行推断口径
              （口径的唯一真源 = 后端 _mini_unusable_reason，与小程序发 token 闸门一致）。
            -->
            <el-tooltip
              v-if="row.mini_unusable"
              :content="row.mini_unusable_reason || '该账号无法在小程序登录'"
              placement="top"
            >
              <el-tag type="danger" size="small" effect="plain" class="ml4">
                小程序不可用
              </el-tag>
            </el-tooltip>
          </template>
        </el-table-column>
        <el-table-column label="手机号" width="130">
          <template #default="{ row }">{{ row.phone || '-' }}</template>
        </el-table-column>
        <el-table-column label="微信昵称" min-width="170">
          <template #default="{ row }">
            <div v-if="row.wx_nickname || row.wx_avatar_url" class="wx-cell">
              <el-avatar :size="26" :src="row.wx_avatar_url || ''">
                {{ (row.wx_nickname || '微').charAt(0) }}
              </el-avatar>
              <span class="wx-nick">{{ row.wx_nickname || '-' }}</span>
            </div>
            <span v-else class="muted-cell">未完善</span>
          </template>
        </el-table-column>
        <el-table-column label="微信标识（脱敏）" min-width="140">
          <template #default="{ row }">
            <span class="mono">{{ row.openid_masked || '-' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="绑定时间" min-width="160">
          <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="最近登录" min-width="160">
          <template #default="{ row }">
            <span v-if="!row.last_login_at" class="muted">从未</span>
            <span v-else>{{ formatTime(row.last_login_at) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="账号状态" width="90">
          <template #default="{ row }">
            <el-tag v-if="row.is_active" type="success" size="small">启用</el-tag>
            <el-tag v-else type="info" size="small">已禁用</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="100" fixed="right">
          <template #default="{ row }">
            <el-button link type="danger" @click="unbind(row)">解绑</el-button>
          </template>
        </el-table-column>
      </el-table>

      <el-empty v-if="!loading && !items.length" description="暂无微信绑定记录" />
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Refresh } from '@element-plus/icons-vue'
import { authApi } from '@/api'
import { ORG_ROLE_LABELS } from '@/constants/role'

const items = ref<any[]>([])
const loading = ref(false)
const keyword = ref('')

function formatTime(value: string | null) {
  if (!value) return '-'
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return value
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

async function load() {
  loading.value = true
  try {
    const res: any = await authApi.wechatBindings(keyword.value.trim() || undefined)
    items.value = res?.items || []
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || '加载微信绑定失败')
  } finally {
    loading.value = false
  }
}

async function unbind(row: any) {
  const who = row.username || `用户#${row.user_id}`
  // 🔴 2026-10-20：原因改用后端给的 mini_unusable_reason。
  // 旧文案按 `row.org_role ? '公司级' : '不可用'` 自行推断，会把已放行的
  // 公司级管理员说成「无法在小程序登录」，误导管理员来解绑自己能用的账号。
  const reason = row.mini_unusable
    ? `${row.mini_unusable_reason || '该账号无法在小程序登录'}，解绑可释放此微信。`
    : '解绑后，该微信将无法再登录该账号，需重新绑定。'
  try {
    await ElMessageBox.confirm(
      `确定解绑「${who}」绑定的微信吗？\n${reason}`,
      '解绑微信',
      { type: 'warning', confirmButtonText: '解绑', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  try {
    await authApi.unbindWechatBinding(row.user_id)
    ElMessage.success('已解绑，该微信可重新注册或绑定')
    await load()
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || '解绑失败')
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
.header-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}
.user-cell {
  display: flex;
  flex-direction: column;
  line-height: 1.5;
}
.user-cell .uname {
  font-weight: 600;
}
.user-cell .realname {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
.mono {
  font-family: 'SF Mono', Consolas, monospace;
  font-size: 12px;
}
.muted {
  color: var(--el-text-color-placeholder);
}
.muted-cell {
  color: var(--el-text-color-placeholder);
  font-size: 12px;
}
.wx-cell {
  display: flex;
  align-items: center;
  gap: 8px;
}
.wx-cell .wx-nick {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.ml4 {
  margin-left: 4px;
}
</style>
