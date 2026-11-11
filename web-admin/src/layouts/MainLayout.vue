<template>
  <el-container class="app-layout">
    <!-- 左侧导航 -->
    <el-aside :width="isCollapse ? '64px' : '200px'" class="app-sider">
      <div class="sider-header">
        <span class="sider-logo-window">
          <img src="/app-logo.png" alt="广东建设智慧监理 Logo" class="sider-logo" />
        </span>
        <span v-if="!isCollapse" class="brand-name">广东建设智慧监理</span>
      </div>
      <el-menu
        :default-active="$route.path"
        router
        :collapse="isCollapse"
        background-color="#001529"
        text-color="rgba(255,255,255,0.72)"
        active-text-color="#ffffff"
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
          <el-menu-item v-else :index="item.path" :class="{ 'menu-sep': item.dividerBefore }">
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
               公司级账号（超管/管理员）默认在「全部项目」视角，需要聚焦时再选具体项目。 -->
          <div v-if="projects.length" class="project-picker">
            <el-icon class="project-icon"><OfficeBuilding /></el-icon>
            <el-select
              v-if="projects.length > 1 || auth.isCompanyLevel"
              v-model="selectedProjectId"
              class="project-select"
              size="small"
              filterable
              :loading="switching"
              :disabled="switching"
              placeholder="选择项目"
              @change="onSwitchProject"
            >
              <el-option v-if="auth.isCompanyLevel" label="全部项目" :value="0" />
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
            <!-- 「全部项目」视角：跨项目汇总可见，新建时弹窗选写入项目（不锁成只读） -->
            <el-tooltip
              v-if="auth.allProjects"
              :content="writeProjectHint"
              placement="bottom"
            >
              <el-tag size="small" type="success" effect="plain" class="project-static">
                可读写
              </el-tag>
            </el-tooltip>
          </div>
        </div>
        <div class="header-right">
          <el-dropdown @command="handleCommand">
            <span class="user-info">
              <el-avatar :size="28" :src="auth.userInfo?.avatar_url || ''">
                {{ auth.userInfo?.real_name?.charAt(0) }}
              </el-avatar>
              <span class="user-name">{{ auth.userInfo?.real_name }}</span>
              <el-tag size="small" type="info">{{ displayRoleLabel }}</el-tag>
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
    <!-- 「全部项目」视角下新建时选写入归属项目。
         注意：**不能挂在 App.vue**——入口 chunk 引入它会让 rollup 把 Vue 运行时
         错分到 charts chunk，运行时 TDZ 报错整站白屏（2026-10-20 踩过）。
         挂在这里是路由级懒加载 chunk，不参与入口分包。 -->
    <ProjectPickDialog />

    <!-- 监理小助手：全站悬浮球。挂在布局层，路由切换不卸载会话状态；
         同样不放进 App.vue，避免入口 chunk 被牵连分包。 -->
    <AssistantBall />
  </el-container>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { useAuthStore } from '@/stores/auth'
import { roleLabel } from '@/constants/role'
import ProjectPickDialog from '@/components/ProjectPickDialog.vue'
import AssistantBall from '@/components/assistant/AssistantBall.vue'
import { projectApi } from '@/api'
import { Fold, Expand, ArrowDown, OfficeBuilding } from '@element-plus/icons-vue'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()

// 顶栏角色标签：公司级角色（超管/管理员）优先展示，否则展示项目岗位中文
const displayRoleLabel = computed(
  () => roleLabel(auth.userInfo?.org_role) || roleLabel(auth.userInfo?.role)
)

// 是否超级管理员（高危页面按 org_role 收敛可见性）
const isSuper = computed(() => auth.userInfo?.org_role === 'super_admin')
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

// 当前项目变化时（登录、切换成功、权限刷新）同步下拉的选中值；
// 「全部项目」视角用 0 表示（仅公司级账号有这个选项）
watch(
  () => auth.currentProjectId,
  (v) => { selectedProjectId.value = auth.allProjects ? 0 : (v ?? null) },
  { immediate: true },
)
// 视角切换（全部 ↔ 具体项目）也要把下拉拨回去
watch(
  () => auth.allProjects,
  (all) => { selectedProjectId.value = all ? 0 : (auth.currentProjectId ?? null) },
)

// 项目是所有页面的隐含查询条件：切换后靠 key 变化重建 router-view
const viewKey = computed(
  () => `${route.fullPath}::${auth.allProjects ? 'all' : (auth.currentProjectId ?? 0)}`,
)

// 全部项目视角下写操作的落点提示（未选过就没名字，提示会点新建时再选）
const writeProjectHint = computed(() => {
  const list = Array.isArray(projects.value) ? projects.value : []
  const name = list.find((p) => p.id === auth.writeProjectId)?.name
  return name
    ? `跨项目汇总视图；新建/修改会写入「${name}」（每次操作前可改）`
    : '跨项目汇总视图；新建/修改时会让你先选择写入哪个项目'
})

onMounted(async () => {
  try {
    const list = await projectApi.list()
    // 接口异常时可能返回非数组，这里兜一层，避免后续 .find/.map 把整页打挂
    projects.value = Array.isArray(list) ? list : []
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
  // 0 = 「全部项目」视角：只改读取范围，不动 users.project_id（那是写入归属）
  if (projectId === 0) {
    if (!auth.isCompanyLevel) return
    auth.setAllProjects(true)
    ElMessage.success('已切换到「全部项目」视角')
    return
  }
  // 从「全部项目」切回具体项目：先把视角关掉，再走原来的切项目逻辑
  auth.setAllProjects(false)
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

  // 工程影像归档（水印照片 → 归类 → 台账/缺口 → 两份文书同步资料库）
  if (perm('work_record')) {
    items.push({ path: '/image-archive', title: '影像归档', icon: 'Picture' })
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

  // 管理员与跨项目查看授权：仅超管（高危——管 org 角色与跨项目授权，
  // 不能让持有 role_grant 的管理员进入，故按 org_role 而非权限 key 收敛）。
  if (isSuper.value) {
    items.push({ path: '/admin-grants', title: '管理员与授权', icon: 'Stamp' })
  }

  // 系统设置归入「系统管理」分组，置于全部业务/账号功能之后，并用分隔线与上方区分。
  if (perm('settings')) {
    items.push({ path: '/settings', title: '系统设置', icon: 'Setting', dividerBefore: true })
  }

  // 「角色权限」配置页已下线（2026-10-20）：权限矩阵改为后端/DB 维护，
  // 界面入口移除可避免误触「恢复默认」把配置打回代码默认值。
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
  border-bottom: 1px solid rgba(255,255,255,0.08);
  color: #fff;
  font-size: 17px;
  font-weight: 600;
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
  border-bottom: 1px solid var(--el-border-color-lighter);
  background: #fff;
  box-shadow: 0 1px 3px rgba(31, 35, 41, 0.05);
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
.collapse-btn:hover { color: var(--el-color-primary); }
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

/* 深色侧边栏菜单样式覆盖（A 深蓝精修） */
:deep(.el-menu) {
  border-right: none;
  --el-menu-item-height: 46px;
}
:deep(.el-menu-item),
:deep(.el-sub-menu__title) {
  position: relative;
  transition: background-color 0.15s, color 0.15s;
}
:deep(.el-sub-menu__title:hover),
:deep(.el-menu-item:hover) {
  background-color: rgba(255, 255, 255, 0.06) !important;
  color: #fff !important;
}
/* 展开态选中 = 内缩圆角胶囊（去掉左侧竖条，重量更轻、指示性更强） */
:deep(.el-menu:not(.el-menu--collapse) .el-menu-item.is-active:not(.menu-sep)) {
  background-color: rgba(59, 130, 246, 0.28) !important;
  border-radius: 6px;
  color: #fff !important;
  font-weight: 600;
  margin: 4px 9px;
}
/* 分组分隔线（系统设置前）：常态留 8px 间距 + 顶部淡线 */
:deep(.el-menu-item.menu-sep) {
  border-top: 1px solid rgba(255, 255, 255, 0.12);
  margin-top: 8px;
}
/* 分隔项同时选中时，保持顶部间距（不被上面的 margin 覆盖） */
:deep(.el-menu:not(.el-menu--collapse) .el-menu-item.is-active.menu-sep) {
  background-color: rgba(59, 130, 246, 0.28) !important;
  border-radius: 6px;
  color: #fff !important;
  font-weight: 600;
  margin: 12px 9px 4px;
}
/* 收起态（64px 图标列）：选中 = 满宽底色 + 左侧 2px 主色指示，不内缩（否则图标偏移） */
:deep(.el-menu--collapse .el-menu-item.is-active) {
  background-color: rgba(59, 130, 246, 0.30) !important;
  color: #fff !important;
  font-weight: 600;
  border-left: 2px solid #3b82f6;
}
/* 子菜单面板底色与侧栏统一（弹出层默认是白的，在深蓝侧栏里很跳） */
:deep(.el-menu--inline) {
  background-color: #000c17 !important;
}
:deep(.el-menu--inline .el-menu-item.is-active) {
  background-color: rgba(59, 130, 246, 0.30) !important;
}
</style>
