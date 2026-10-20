# 监理便携经办端（mobile-h5）

监理平台三端之一：手机浏览器端（便携经办端），面向现场监理人员，提供影像采集、文书经办、隐患整改、规范查询等移动办公能力。

## 技术栈

- Vue 3.5 + TypeScript 5.9 + Vite 7
- Vant 4.x（移动端 UI 组件库）
- Vue Router 4 + Pinia 2
- Axios（HTTP 客户端，复用 cloud-backend）
- @vueuse/core（移动端常用 hooks）
- dayjs（日期格式化）
- idb（IndexedDB 封装，离线队列）
- 构建期生成的原生 Service Worker（可安装到主屏幕、静态资源离线可用）

## 开发

```bash
npm install
npm run dev      # 启动开发服务器：http://localhost:5174
npm run build    # 类型检查 + 生产构建
npm run preview  # 预览构建产物
npm run type-check
```

开发服务器 `/api` 代理到后端 `http://localhost:8000`（cloud-backend）。

## 目录结构

```
mobile-h5/
├── public/              # 静态资源（PWA manifest、图标）
├── src/
│   ├── api/             # API 客户端与离线队列
│   ├── layouts/         # 布局（默认布局含 NavBar+TabBar / 空白布局）
│   ├── router/          # 路由配置与全局守卫
│   ├── stores/          # Pinia 状态（auth / app）
│   ├── styles/          # 全局样式与 Vant 主题
│   ├── utils/           # 工具函数（权限、格式化、震动反馈）
│   └── views/           # 页面（登录、工作台、影像、文书、隐患、规范、通知、个人中心）
└── vite.config.ts
```

## 与后端对接

- 所有 API 走 `/api` 前缀，复用 `cloud-backend`（端口 8000）
- 认证：`POST /api/auth/login`，返回 `access_token` + `permissions`
- 401 响应自动跳转登录页

## PWA

支持添加到主屏幕，manifest 见 `public/manifest.json`。生产构建会根据本次
产物清单生成带版本缓存名的 `dist/sw.js`；Service Worker 不缓存 `/api`、
`/uploads`、`/exports`，发布新版本时会自动清理旧静态缓存。
