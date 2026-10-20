/**
 * 创建日期：2026-07-18
 * 路由配置：登录页（BlankLayout）+ 业务页（DefaultLayout）
 * 全局守卫：未登录跳转 /login；已登录访问 /login 跳工作台
 * 路由 meta：title（NavBar 标题）、requireAuth（是否需要登录）
 */
import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { useAppStore } from '@/stores/app'
import BlankLayout from '@/layouts/BlankLayout.vue'

const routes: RouteRecordRaw[] = [
  {
    path: '/login',
    name: 'Login',
    component: () => import('@/layouts/BlankLayout.vue'),
    children: [
      {
        path: '',
        name: 'LoginPage',
        component: () => import('@/views/Login.vue'),
        meta: { title: '登录', requireAuth: false },
      },
    ],
  },
  {
    path: '/register',
    name: 'Register',
    component: BlankLayout,
    children: [
      {
        path: '',
        name: 'RegisterPage',
        component: () => import('@/views/Register.vue'),
        meta: {
            title: '注册',
            requireAuth: false
        },
      },
    ],
  },
  {
    // 「忘记密码」自助重置（密保问题 → 设新密码）
    path: '/forgot-password',
    name: 'ForgotPassword',
    component: BlankLayout,
    children: [
      {
        path: '',
        name: 'ForgotPasswordPage',
        component: () => import('@/views/ForgotPassword.vue'),
        meta: { title: '重置密码', requireAuth: false },
      },
    ],
  },
  {
    // 强制改密（管理员重置为一次性口令后）
    path: '/force-password',
    name: 'ForcePassword',
    component: BlankLayout,
    children: [
      {
        path: '',
        name: 'ForcePasswordPage',
        component: () => import('@/views/ForcePassword.vue'),
        meta: { title: '设置新密码', requireAuth: true },
      },
    ],
  },
  {
    path: '/',
    component: () => import('@/layouts/DefaultLayout.vue'),
    children: [
      {
        path: '',
        name: 'Home',
        component: () => import('@/views/Home.vue'),
        meta: { title: '工作台', requireAuth: true },
      },
      // 影像
      {
        path: 'images',
        name: 'ImageList',
        component: () => import('@/views/images/ImageList.vue'),
        meta: { title: '影像采集', requireAuth: true },
      },
      {
        path: 'images/capture',
        name: 'ImageCapture',
        component: () => import('@/views/images/ImageCapture.vue'),
        meta: { title: '拍照取证', requireAuth: true },
      },
      // 文书
      {
        path: 'documents',
        name: 'DocumentList',
        component: () => import('@/views/documents/DocumentList.vue'),
        meta: { title: '监理文书', requireAuth: true },
      },
      {
        path: 'documents/create',
        name: 'DocumentCreate',
        component: () => import('@/views/documents/DocumentCreate.vue'),
        meta: { title: '新建文书', requireAuth: true },
      },
      {
        path: 'documents/:id',
        name: 'DocumentDetail',
        component: () => import('@/views/documents/DocumentDetail.vue'),
        meta: { title: '文书详情', requireAuth: true },
      },
      // 隐患
      {
        path: 'hazards',
        name: 'HazardList',
        component: () => import('@/views/hazards/HazardList.vue'),
        meta: { title: '隐患整改', requireAuth: true },
      },
      {
        path: 'hazards/:id',
        name: 'HazardDetail',
        component: () => import('@/views/hazards/HazardDetail.vue'),
        meta: { title: '隐患详情', requireAuth: true },
      },
      // 规范库
      {
        path: 'knowledge',
        name: 'KnowledgeSearch',
        component: () => import('@/views/knowledge/KnowledgeSearch.vue'),
        meta: { title: '规范库', requireAuth: true },
      },
      {
        path: 'knowledge/guide',
        name: 'AcceptanceGuide',
        component: () => import('@/views/knowledge/AcceptanceGuide.vue'),
        meta: { title: '验收流程指引', requireAuth: true },
      },
      // 工作记录
      {
        path: 'work-records',
        name: 'WorkRecordList',
        component: () => import('@/views/work-records/WorkRecordList.vue'),
        meta: { title: '工作记录', requireAuth: true },
      },
      {
        path: 'work-records/create',
        name: 'WorkRecordCreate',
        component: () => import('@/views/work-records/WorkRecordCreate.vue'),
        meta: { title: '新建工作记录', requireAuth: true },
      },
      {
        path: 'work-records/:id',
        name: 'WorkRecordDetail',
        component: () => import('@/views/work-records/WorkRecordDetail.vue'),
        meta: { title: '工作记录详情', requireAuth: true },
      },
      // 派单任务
      {
        path: 'tasks',
        name: 'TaskList',
        component: () => import('@/views/tasks/TaskList.vue'),
        meta: { title: '我的任务', requireAuth: true },
      },
      {
        path: 'tasks/:id',
        name: 'TaskDetail',
        component: () => import('@/views/tasks/TaskDetail.vue'),
        meta: { title: '任务详情', requireAuth: true },
      },
      // AI 巡检
      {
        path: 'inspection',
        name: 'InspectionList',
        component: () => import('@/views/inspection/InspectionList.vue'),
        meta: { title: 'AI 巡检', requireAuth: true },
      },
      {
        path: 'inspection/start',
        name: 'InspectionStart',
        component: () => import('@/views/inspection/InspectionStart.vue'),
        meta: { title: '启动巡检', requireAuth: true },
      },
      {
        path: 'inspection/:id',
        name: 'InspectionDetail',
        component: () => import('@/views/inspection/InspectionDetail.vue'),
        meta: { title: '巡检详情', requireAuth: true },
      },
      // 台账查询
      {
        path: 'ledger',
        name: 'LedgerList',
        component: () => import('@/views/ledger/LedgerList.vue'),
        meta: { title: '台账查询', requireAuth: true },
      },
      {
        path: 'ledger/:type/:id',
        name: 'LedgerDetail',
        component: () => import('@/views/ledger/LedgerDetail.vue'),
        meta: { title: '台账详情', requireAuth: true },
      },
      // 通知中心
      {
        path: 'notifications',
        name: 'Notifications',
        component: () => import('@/views/Notifications.vue'),
        meta: { title: '通知中心', requireAuth: true },
      },
      // 个人中心
      {
        path: 'profile',
        name: 'Profile',
        component: () => import('@/views/Profile.vue'),
        meta: { title: '我的', requireAuth: true },
      },
    ],
  },
  // 兜底
  { path: '/:pathMatch(.*)*', redirect: '/' },
]

const router = createRouter({
  history: createWebHistory('/mobile/'),
  routes,
  scrollBehavior() {
    return { top: 0 }
  },
})

router.beforeEach((to, _from, next) => {
  const auth = useAuthStore()
  const requireAuth = to.meta.requireAuth !== false

  // 设置文档标题
  if (to.meta.title) {
    document.title = `${to.meta.title} - 监理便携经办端`
  }

  if (!requireAuth) {
    // 已登录访问登录页 -> 跳工作台
    if (to.name === 'LoginPage' && auth.isLoggedIn) {
      next('/')
      return
    }
    next();
    return
  }

  if (!auth.isLoggedIn) {
    next({ name: 'Login', query: { redirect: to.fullPath } })
    return
  }

  // 强制改密锁：管理员重置为一次性口令后，用户被锁在改密页直到改完
  if (auth.user?.must_change_password && to.name !== 'ForcePasswordPage') {
    next({ name: 'ForcePassword' })
    return
  }

  // 初始化应用状态（在线/离线、同步队列）
  const app = useAppStore()
  app.init()

  next()
})

export default router
