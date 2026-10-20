// 创建日期：2026-07-18
// 移动端（便携经办端）Vite 配置：端口 5174，代理后端 localhost:8000，集成 PWA 与 Vant 自动按需引入
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import AutoImport from 'unplugin-auto-import/vite'
import Components from 'unplugin-vue-components/vite'
import { VantResolver } from '@vant/auto-import-resolver'
import { resolve } from 'path'
import { createHash } from 'node:crypto'

/**
 * 生成无第三方运行时依赖的 Service Worker。
 *
 * 仅预缓存当前构建的静态资源；API、上传和导出接口永不缓存，避免把鉴权数据
 * 写入 Cache Storage。缓存名由产物清单计算，每次发布自动滚动并清理旧版本。
 */
function pwaServiceWorker() {
  return {
    name: 'gdjl-pwa-service-worker',
    generateBundle(_options: unknown, bundle: Record<string, { fileName: string }>) {
      const generatedAssets = Object.values(bundle)
        .map((entry) => `/${entry.fileName}`)
        .filter((path) => path !== '/sw.js')
        .sort()
      const precacheUrls = [
        '/',
        '/index.html',
        '/manifest.json',
        '/icons/icon-192.svg',
        '/icons/icon-512.svg',
        ...generatedAssets,
      ]
      const version = createHash('sha256').update(precacheUrls.join('\n')).digest('hex').slice(0, 16)
      const source = `
const CACHE_PREFIX = 'gdjl-mobile-h5-'
const CACHE_NAME = CACHE_PREFIX + '${version}'
const PRECACHE_URLS = ${JSON.stringify(precacheUrls)}
const NEVER_CACHE_PREFIXES = ['/api/', '/uploads/', '/exports/']

self.addEventListener('install', (event) => {
  event.waitUntil(caches.open(CACHE_NAME).then((cache) => cache.addAll(PRECACHE_URLS)))
  self.skipWaiting()
})

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(
        keys.filter((key) => key.startsWith(CACHE_PREFIX) && key !== CACHE_NAME)
          .map((key) => caches.delete(key))
      ))
      .then(() => self.clients.claim())
  )
})

self.addEventListener('fetch', (event) => {
  const request = event.request
  const url = new URL(request.url)
  if (
    request.method !== 'GET' ||
    url.origin !== self.location.origin ||
    NEVER_CACHE_PREFIXES.some((prefix) => url.pathname.startsWith(prefix))
  ) {
    return
  }

  if (request.mode === 'navigate') {
    event.respondWith(
      fetch(request).catch(() => caches.match('/index.html'))
    )
    return
  }

  event.respondWith(
    caches.match(request).then((cached) => cached || fetch(request).then((response) => {
      if (response.ok && response.type === 'basic') {
        const copy = response.clone()
        caches.open(CACHE_NAME).then((cache) => cache.put(request, copy))
      }
      return response
    }))
  )
})
`
      this.emitFile({ type: 'asset', fileName: 'sw.js', source })
    },
  }
}

export default defineConfig({
  base: '/mobile/',

  plugins: [
    vue(),
    AutoImport({
      imports: ['vue', 'vue-router', 'pinia'],
      resolvers: [VantResolver()],
      dts: 'src/auto-imports.d.ts',
    }),
    Components({
      resolvers: [VantResolver()],
      dts: 'src/components.d.ts',
    }),
    pwaServiceWorker(),
  ],
  resolve: {
    alias: {
      '@': resolve(__dirname, 'src'),
    },
  },
  server: {
    port: 5174,
    host: true,
    allowedHosts: ['mobile-h5', 'localhost', '192.168.67.195', '183.3.131.92', '.ngrok.io'],
    proxy: {
      '/api': {
        target: 'http://backend:8000',
        changeOrigin: true,
      },
      '/uploads': {
        target: 'http://backend:8000',
        changeOrigin: true,
      },
      '/exports': {
        target: 'http://backend:8000',
        changeOrigin: true,
      },
    },
  },
  build: {
    target: 'es2015',
    outDir: 'dist',
    chunkSizeWarningLimit: 800,
    rollupOptions: {
      output: {
        manualChunks: {
          vant: ['vant'],
          vendor: ['vue', 'vue-router', 'pinia', 'axios'],
        },
      },
    },
  },
})
