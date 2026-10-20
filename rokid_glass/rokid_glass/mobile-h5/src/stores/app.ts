/**
 * 创建日期：2026-07-18
 * 应用状态：在线/离线状态、待同步离线数据数量
 * 监听 navigator.onLine 与 online/offline 事件，自动同步队列
 */
import { defineStore } from 'pinia'
import { ref } from 'vue'
import { getPendingCount, syncQueue } from '@/api/offline'

export const useAppStore = defineStore('app', () => {
  let initialized = false
  const online = ref<boolean>(
    typeof navigator !== 'undefined' ? navigator.onLine : true,
  )
  const pendingSyncCount = ref<number>(0)
  const syncing = ref<boolean>(false)

  /** 刷新待同步数量 */
  async function refreshPendingCount() {
    try {
      pendingSyncCount.value = await getPendingCount()
    } catch {
      pendingSyncCount.value = 0
    }
  }

  /** 触发离线队列同步 */
  async function trySync() {
    if (!online.value || syncing.value || pendingSyncCount.value === 0) return
    syncing.value = true
    try {
      await syncQueue()
      await refreshPendingCount()
    } catch (e) {
      console.warn('离线队列同步失败', e)
    } finally {
      syncing.value = false
    }
  }

  /** 初始化：绑定事件 + 首次刷新 */
  function init() {
    if (typeof window === 'undefined' || initialized) return
    initialized = true
    window.addEventListener('online', () => {
      online.value = true
      trySync()
    })
    window.addEventListener('offline', () => {
      online.value = false
    })
    refreshPendingCount()
  }

  return {
    online,
    pendingSyncCount,
    syncing,
    refreshPendingCount,
    trySync,
    init,
  }
})
