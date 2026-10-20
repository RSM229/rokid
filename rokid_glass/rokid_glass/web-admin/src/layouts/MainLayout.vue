<template>
  <el-container class="app-layout">
    <!-- 左侧导航 -->
    <el-aside :width="isCollapse ? '64px' : '200px'" class="app-sider">
      <div class="sider-header">
        <span class="sider-logo-window">
          <img src="/baize-logo.png" alt="白泽 Logo" class="sider-logo" />
        </span>
        <span v-if="!isCollapse" class="brand-name">白泽</span>
      </div>
      <el-menu
        :default-active="$route.path"
        router
        :collapse="isCollapse"
        background-color="#001529"
        text-color="rgba(255,255,255,0.65)"
        active-text-color="#409EFF"
      >
        <!-- 一级菜单：按截图结构 -->
        <template v-for="item in menuItems" :key="item.path">
          <!-- 有子菜单的分组 -->
          <el-sub-menu v-if="item.children?.length" :index="item.path">
            <template #title>
              <el-icon><component :is="item.icon" /></el-icon>
              <span>{{ item.title }}</span>
            </template>
            <el-menu-item
              v-for="child in item.children"
              :key="child.path"
              :index="child.path"
            >
              {{ child.title }}
            </el-menu-item>
          </el-sub-menu>

          <!-- 无子菜单的单项 -->
          <el-menu-item v-else :index="item.path">
            <el-icon><component :is="item.icon" /></el-icon>
            <span>{{ item.title }}</span>
          </el-menu-item>
        </template>
      </el-menu>
    </el-aside>

    <el-container>
      <!-- 顶部栏 -->
      <el-header class="app-header">
        <div class="header-left">
          <el-icon
            class="collapse-btn"
            @click="isCollapse = !isCollapse"
            size="20"
          >
            <Fold v-if="!isCollapse" />
            <Expand v-else />
          </el-icon>
          <el-breadcrumb separator="/">
            <el-breadcrumb-item>{{ currentRoute.meta?.title || '首页' }}</el-breadcrumb-item>
          </el-breadcrumb>

          <!-- 项目切换：有些负责人同时负责多个项目。
               多项目时是可切换的下拉；只有一个项目时退化为静态项目名（不制造多余交互）。 -->
          <div v-if="projects.length" class="project-picker">
            <el-icon class="project-icon"><OfficeBuilding /></el-icon>
            <el-select
              v-if="projects.length > 1"
              v-model="selectedProjectId"
              class="project-select"
              size="small"
              :loading="switching"
              :disabled="switching"
              placeholder="选择项目"
              @change="onSwitchProject"
            >
              <el-option
                v-for="p in projects"
                :key="p.id"
                :label="p.name"
                :value="p.id"
              />
            </el-select>
            <el-tag v-else size="small" type="info" class="project-static">
              {{ projects[0].name }}
            </el-tag>
          </div>
        </div>
        <div class="header-right">
          <el-dropdown @command="handleCommand">
            <span class="user-info">
              <el-avatar :size="28" :src="auth.userInfo?.avatar_url || ''">
                {{ auth.userInfo?.real_name?.charAt(0) }}
              </el-avatar>
              <span class="user-name">{{ auth.userInfo?.real_name }}</span>
              <el-tag size="small" type="info">{{ auth.userInfo?.role }}</el-tag>
              <el-icon><ArrowDown /></el-icon>
            </span>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item command="profile">个人中心</el-dropdown-item>
                <el-dropdown-item divided command="logout">退出登录</el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </div>
      </el-header>

      <!-- 主内容区 -->
      <el-main class="app-content">
        <!-- key 里带上当前项目：切换项目后强制各页面重新挂载并重拉数据，
             否则会停在「上一个项目」的列表上（项目是所有查询的隐含条件） -->
        <router-view :key="viewKey" />
      </el-main>
    </el-container>
  </el-container>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { useAuthStore } from '@/stores/auth'
import { projectApi } from '@/api'
import { Fold, Expand, ArrowDown, OfficeBuilding } from '@element-plus/icons-vue'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const isCollapse = ref(false)

const currentRoute = computed(() => route)

// ---- 项目切换（多项目负责人） ----
interface ProjectOption {
  id: number
  name: string
  short_name?: string | null
}

const projects = ref<ProjectOption[]>([])
const selectedProjectId = ref<number | null>(null)
const switching = ref(false)

// 当前项目变化时（登录、切换成功、权限刷新）同步下拉的选中值
watch(
  () => auth.currentProjectId,
  (v) => { selectedProjectId.value = v ?? null },
  { immediate: true },
)

// 项目是所有页面的隐含查询条件：切换后靠 key 变化重建 router-view
const viewKey = computed(() => `${route.fullPath}::${auth.currentProjectId ?? 0}`)

onMounted(async () => {
  try {
    projects.value = await projectApi.list()
  } catch {
    // 列表拉不到就退化成「不显示切换器」，不影响其他功能
    projects.value = []
  }
})

/**
 * 切换当前项目。
 *
 * 后端切换后会把权限按新项目重新合成，store 里的 switchProject 已重新拉过
 * /auth/me；这里只负责提示、并在「新项目下当前页无权限」时退回工作台。
 */
async function onSwitchProject(projectId: number) {
  if (!projectId || projectId === auth.currentProjectId) return
  const target = projects.value.find((p) => p.id === projectId)
  switching.value = true
  try {
    await auth.switchProject(projectId)
    ElMessage.success(`已切换到「${target?.name || projectId}」`)
    const perm = route.meta.permission as string | undefined
    if (perm && !auth.hasPermission(perm)) {
      router.replace('/dashboard')
    }
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || e?.message || '项目切换失败')
    // 失败时把下拉回滚到真实当前项目，避免展示与后端不一致
    selectedProjectId.value = auth.currentProjectId
  } finally {
    switching.value = false
  }
}

// 菜单配置 — 业务入口固定为六项，具体可见性仍由权限控制。
const menuItems = computed(() => {
  const perm = (p: string) => auth.hasPermission(p)
  const items: any[] = []

  // 工作台（核心入口）
  if (perm('dashboard')) {
    items.push({ path: '/dashboard', title: '工作台', icon: 'Odometer' })
  }

  // 公司看板（P4 多项目横向对比，总监级）
  if (perm('task_assign')) {
    items.push({ path: '/company-dashboard', title: '公司看板', icon: 'DataBoard' })
  }

  // 现场识别
  if (perm('work_record')) {
    items.push({ path: '/camera-simulator', title: '现场识别', icon: 'VideoCamera' })
  }

  const workChildren: any[] = []
  if (perm('inspection')) workChildren.push({ path: '/work-record/inspection', title: '巡视记录' })
  if (perm('acceptance')) workChildren.push({ path: '/work-record/acceptance', title: '验收管理' })
  if (perm('station')) workChildren.push({ path: '/work-record/station', title: '旁站记录' })
  if (perm('witness')) workChildren.push({ path: '/work-record/witness', title: '见证取样' })
  if (perm('hazard_ai')) workChildren.push({ path: '/inspection-dashboard', title: 'AI巡检统计' })
  if (workChildren.length) {
    items.push({ path: '/work-record', title: '工作记录', icon: 'Document', children: workChildren })
  }

  // 信息库同时承载知识文档与知识图谱。
  if (perm('knowledge_base')) {
    items.push({ path: '/knowledge-base', title: '信息库', icon: 'Reading' })
  }

 // if (perm('todo_center')) {
   // items.push({ path: '/todo-center', title: '待办中心', icon: 'List' })
  //}

  if (perm('settings')) {
    items.push({ path: '/settings', title: '系统设置', icon: 'Setting' })
  }

  // 项目管理（立项仅超管；总监等 member_manage 持有者可查看/切换/分配人员）
  if (perm('member_manage')) {
    items.push({ path: '/project-manage', title: '项目管理', icon: 'OfficeBuilding' })
  }

  // 账号管理（总监可见列表；增删改后端要求 user_manage）
  if (perm('settings') || perm('user_manage')) {
    items.push({ path: '/user-manage', title: '用户管理', icon: 'User' })
  }

  // 邀请码管理：超管看全量，总监（member_manage）看本项目 —— 页面内自动切换接口。
  if (perm('member_manage') || perm('user_manage')) {
    items.push({ path: '/invite-codes', title: '邀请码管理', icon: 'Ticket' })
  }
  // 微信绑定管理：换/解绑、释放被占微信（仅超管）
  if (perm('user_manage')) {
    items.push({ path: '/wechat-bindings', title: '微信绑定管理', icon: 'ChatDotRound' })
  }

  // 角色权限配置（Step 3；role_grant 当前仅超管持有）
  if (perm('role_grant')) {
    items.push({ path: '/role-permissions', title: '角色权限', icon: 'Lock' })
  }
  return items
})

function handleCommand(cmd: string) {
  if (cmd === 'logout') {
    auth.logout()
    router.push('/login')
  } else if (cmd === 'profile') {
    router.push('/profile')
  }
}
</script>

<style scoped>
.app-layout {
  height: 100vh;
  overflow: hidden;
}
.app-sider {
  background: #001529;
  transition: width 0.3s;
  overflow-y: auto;
  overflow-x: hidden;
}
.sider-header {
  height: 56px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-bottom: 1px solid rgba(255,255,255,0.1);
  color: #fff;
  font-size: 18px;
  font-weight: 700;
  letter-spacing: 2px;
  gap: 10px;
}
.sider-logo-window {
  width: 34px;
  height: 34px;
  flex: 0 0 34px;
  overflow: hidden;
  border-radius: 9px;
  background: #fff;
}
.sider-logo {
  width: 100%;
  height: 100%;
  object-fit: cover;
  object-position: center top;
}
.brand-name {
  white-space: nowrap;
}
.app-header {
  height: 56px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 20px;
  border-bottom: 1px solid #e8e8e8;
  background: #fff;
  box-shadow: 0 1px 4px rgba(0,21,42,0.08);
}
.header-left {
  display: flex;
  align-items: center;
  gap: 12px;
}
/* 项目切换器：与面包屑用竖线分隔，明确它是「全局上下文」而不是页面标题 */
.project-picker {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-left: 4px;
  padding-left: 14px;
  border-left: 1px solid #ebeef5;
}
.project-icon { color: #909399; }
.project-select { width: 220px; }
.project-static {
  max-width: 220px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.collapse-btn {
  cursor: pointer;
  color: #666;
}
.collapse-btn:hover { color: #409EFF; }
.header-right {
  display: flex;
  align-items: center;
}
.user-info {
  display: flex;
  align-items: center;
  gap: 8px;
  cursor: pointer;
  color: #333;
}
.user-name {
  font-size: 14px;
  font-weight: 500;
}
.app-content {
  background: #f5f7fa;
  padding: 20px;
  overflow-y: auto;
}

/* 深色侧边栏菜单样式覆盖 */
:deep(.el-menu) { border-right: none; }
:deep(.el-sub-menu__title:hover),
:deep(.el-menu-item:hover) { background-color: #1890ff20 !important; }
:deep(.el-menu-item.is-active) { background-color: #1890ff30 !important; }
</style>
