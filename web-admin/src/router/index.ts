import { createRouter, createWebHistory } from 'vue-router'
import { useAuthStore } from '@/stores/auth'

const routes = [
  {
    path: '/login',
    name: 'Login',
    component: () => import('@/views/Login.vue'),
    meta: { public: true },
  },
  {
    // 「忘记密码」自助重置：密保问题校验 → 设新密码（公开，无需登录）
    path: '/forgot-password',
    name: 'ForgotPassword',
    component: () => import('@/views/ForgotPassword.vue'),
    meta: { public: true },
  },
  {
    // 强制改密：管理员把账号重置为一次性口令后，用户登录必先进这里
    // （顶层路由，不挂在 MainLayout 下 —— 不给侧边菜单，避免绕过）
    path: '/force-password',
    name: 'ForcePassword',
    component: () => import('@/views/ForcePassword.vue'),
    meta: { title: '设置新密码' },
  },
  {
    path: '/',
    component: () => import('@/layouts/MainLayout.vue'),
    redirect: '/dashboard',
    children: [
      // 工作台（智能体 + 工作统计）
      {
        path: 'dashboard',
        name: 'Dashboard',
        component: () => import('@/views/Dashboard.vue'),
        meta: { title: '工作台', icon: 'Odometer', permission: 'dashboard' },
      },

      // 公司看板（P4 多项目横向对比，总监级）
      {
        path: 'company-dashboard',
        name: 'CompanyDashboard',
        component: () => import('@/views/CompanyDashboard.vue'),
        meta: { title: '公司看板', icon: 'DataBoard', permission: 'task_assign' },
      },

      // 信息库（知识检索 + 知识图谱）
      {
        path: 'knowledge-base',
        name: 'KnowledgeBase',
        component: () => import('@/views/KnowledgeBase.vue'),
        meta: { title: '信息库', icon: 'Reading', permission: 'knowledge_base' },
      },

      // 现场识别（独立功能）
      {
        path: 'camera-simulator',
        name: 'CameraSimulator',
        component: () => import('@/views/CameraSimulator.vue'),
        meta: { title: '现场识别', icon: 'VideoCamera', permission: 'work_record' },
      },

      // 系统设置（仅总监）
      {
        path: 'settings',
        name: 'Settings',
        component: () => import('@/views/Settings.vue'),
        meta: { title: '系统设置', icon: 'Setting', permission: 'settings' },
      },
      {
        path: 'profile',
        name: 'Profile',
        component: () => import('@/views/Profile.vue'),
        meta: { title: '个人中心', permission: 'user_center' },
      },
      {
        path: 'user-manage',
        name: 'UserManage',
        component: () => import('@/views/UserManage.vue'),
        meta: { title: '用户管理', icon: 'User', permission: 'settings' },
      },
      {
        // 项目管理：查看/切换项目（有 project_create 者才能新建 = 仅超管）
        path: 'project-manage',
        name: 'ProjectManage',
        component: () => import('@/views/ProjectManage.vue'),
        meta: { title: '项目管理', icon: 'OfficeBuilding', permission: 'member_manage' },
      },
      {
        // 邀请码管理：`member_manage` —— 超管（全量）与总监（本项目）都持有，
        // 页面内按 `user_manage` 决定走全局接口还是项目级接口。
        path: 'invite-codes',
        name: 'InviteCodes',
        component: () => import('@/views/InviteCodes.vue'),
        meta: { title: '邀请码管理', icon: 'Ticket', permission: 'member_manage' },
      },
      {
      // 微信绑定管理：权限与后端一致（user_manage，仅超管持有）。
      path: 'wechat-bindings',
      name: 'WechatBindings',
      component: () => import('@/views/WechatBindings.vue'),
      meta: { title: '微信绑定管理', icon: 'ChatDotRound', permission: 'user_manage' },
      },
      {
        // 管理员与跨项目查看授权（仅超管）：授权谁是管理员 + 授权谁能看别的管理员的
        // 项目。与「角色权限」页无关（矩阵已固定）。
        path: 'admin-grants',
        name: 'AdminGrants',
        component: () => import('@/views/AdminGrants.vue'),
        meta: { title: '管理员与授权', icon: 'Stamp', permission: 'role_grant' },
      },
      // 「角色权限」页已下线（2026-10-20）：矩阵改由后端/DB 维护，入口移除。
     // {
       // path: 'todo-center',
        //name: 'TodoCenter',
        //component: () => import('@/views/TodoCenter.vue'),
        //meta: { title: '待办中心', icon: 'List', permission: 'todo_center' },
      //},

      // ---- 工作记录模块（保留原有功能）----
      {
        path: 'work-record/inspection',
        name: 'WorkInspection',
        component: () => import('@/views/work/Inspection.vue'),
        meta: { title: '巡视记录', icon: 'View', permission: 'inspection' },
      },
      {
        path: 'work-record/acceptance',
        name: 'WorkAcceptance',
        component: () => import('@/views/work/Acceptance.vue'),
        meta: { title: '验收管理', icon: 'CircleCheck', permission: 'acceptance' },
      },
      {
        path: 'work-record/witness',
        name: 'WorkWitness',
        component: () => import('@/views/work/Witness.vue'),
        meta: { title: '见证取样', icon: 'Camera', permission: 'witness' },
      },
      {
        path: 'work-record/station',
        name: 'WorkStation',
        component: () => import('@/views/work/Station.vue'),
        meta: { title: '旁站记录', icon: 'Monitor', permission: 'station' },
      },

      // ---- AI 隐患识别旧入口：统一回到工作台对话 ----
      {
        path: 'hazard-ai',
        name: 'HazardAI',
        redirect: '/dashboard',
        meta: { title: 'AI隐患识别', icon: 'Warning', permission: 'hazard_ai' },
      },

      // ---- 派单管理 ----
      {
        path: 'task-manage',
        name: 'TaskManage',
        component: () => import('@/views/TaskManage.vue'),
        meta: { title: '派单管理', icon: 'Promotion', permission: 'task_assign' },
      },

      // ---- 文书审批 ----
      {
        path: 'documents',
        name: 'Documents',
        component: () => import('@/views/Documents.vue'),
        meta: { title: '文书审批', icon: 'EditPen', permission: 'document_approve' },
      },

      // ---- 电子签章 ----
      {
        path: 'signatures',
        name: 'Signatures',
        component: () => import('@/views/Signatures.vue'),
        meta: { title: '签章管理', icon: 'Stamp', permission: 'signature_manage' },
      },

      // ---- 验收流程模板配置 ----
      {
        path: 'acceptance-templates',
        name: 'AcceptanceTemplates',
        component: () => import('@/views/AcceptanceTemplates.vue'),
        meta: { title: '验收模板配置', icon: 'Files', permission: 'acceptance_template' },
      },

      // ---- AI 巡检统计 ----
      {
        path: 'inspection-dashboard',
        name: 'InspectionDashboard',
        component: () => import('@/views/InspectionDashboard.vue'),
        meta: { title: 'AI 巡检统计', icon: 'TrendCharts', permission: 'hazard_ai' },
      },

      // ---- 台账管理 ----
      {
        path: 'ledger',
        name: 'Ledger',
        component: () => import('@/views/Ledger.vue'),
        meta: { title: '台账管理', icon: 'Notebook', permission: 'ledger' },
      },

      // ---- 图纸管理 ----
      {
        path: 'drawing-manage',
        name: 'DrawingManage',
        component: () => import('@/views/DrawingManage.vue'),
        meta: { title: '图纸管理', icon: 'Document', permission: 'work_record' },
      },

      // ---- 档案管理 ----
      {
        path: 'archive-manage',
        name: 'ArchiveManage',
        component: () => import('@/views/ArchiveManage.vue'),
        meta: { title: '档案管理', icon: 'FolderOpened', permission: 'work_record' },
      },

      // 兜底：未匹配路由（典型=在已下线页面如 /role-permissions 上刷新）
      // 一律重定向回工作台，否则 router-view 无匹配 → 整页白屏（2026-10-20 踩过）
      { path: '/:pathMatch(.*)*', redirect: '/dashboard' },
    ],
  },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

router.beforeEach((to, _from, next) => {
  const auth = useAuthStore()
  if (to.meta.public) {
    next()
    return
  }
  if (!auth.token) {
    next('/login')
    return
  }
  // 强制改密锁：管理员重置为一次性口令后，用户被锁在改密页直到改完
  if (auth.userInfo?.must_change_password && to.path !== '/force-password') {
    next('/force-password')
    return
  }
  if (to.meta.permission && !auth.hasPermission(to.meta.permission as string)) {
    // 无权限时跳转到工作台
    next('/dashboard')
    return
  }
  next()
})

export default router
