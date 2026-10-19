/**
 * 创建日期：2026-07-18
 * 离线队列管理：基于 IndexedDB（idb 封装）
 * - 离线时将写请求入队
 * - 联网时批量重放
 * - 提供待同步数量
 */
import { openDB, type IDBPDatabase } from 'idb'

const DB_NAME = 'gdjl-mobile'
const DB_VERSION = 1
const STORE_NAME = 'request-queue'

export interface QueuedRequest {
  id?: number
  method: 'GET' | 'POST' | 'PUT' | 'DELETE'
  url: string
  data?: unknown
  createdAt: number
  retries: number
  idempotencyKey: string
}

interface StoredFormEntry {
  name: string
  value: string | Blob
  filename?: string
}

type StoredPayload =
  | { kind: 'empty' }
  | { kind: 'json'; value: unknown }
  | { kind: 'form'; entries: StoredFormEntry[] }

let dbPromise: Promise<IDBPDatabase> | null = null

function getDB(): Promise<IDBPDatabase> {
  if (!dbPromise) {
    dbPromise = openDB(DB_NAME, DB_VERSION, {
      upgrade(db) {
        if (!db.objectStoreNames.contains(STORE_NAME)) {
          const store = db.createObjectStore(STORE_NAME, {
            keyPath: 'id',
            autoIncrement: true,
          })
          store.createIndex('createdAt', 'createdAt')
        }
      },
    })
  }
  return dbPromise
}

/** 判断是否只读请求（只读请求不入队，离线直接跳过） */
function isReadMethod(method: string): boolean {
  return method.toUpperCase() === 'GET'
}

/**
 * 离线时入队
 * @returns true 表示已入队；false 表示是只读请求未入队
 */
export async function queueRequest(
  method: 'GET' | 'POST' | 'PUT' | 'DELETE',
  url: string,
  data?: unknown,
): Promise<boolean> {
  if (isReadMethod(method)) return false
  const db = await getDB()
  let payload: StoredPayload
  if (data instanceof FormData) {
    const entries: StoredFormEntry[] = []
    data.forEach((value, name) => {
      if (typeof value === 'string') entries.push({ name, value })
      else entries.push({ name, value, filename: value.name })
    })
    payload = { kind: 'form', entries }
  } else if (data === undefined || data === null || data === '') {
    payload = { kind: 'empty' }
  } else {
    let value = data
    if (typeof data === 'string') {
      try {
        value = JSON.parse(data)
      } catch {
        value = data
      }
    }
    payload = { kind: 'json', value }
  }
  const req: QueuedRequest = {
    method,
    url,
    data: payload,
    createdAt: Date.now(),
    retries: 0,
    idempotencyKey: crypto.randomUUID(),
  }
  await db.add(STORE_NAME, req)
  return true
}

/** 获取待同步数量 */
export async function getPendingCount(): Promise<number> {
  try {
    const db = await getDB()
    return await db.count(STORE_NAME)
  } catch {
    return 0
  }
}

/** 清空队列 */
export async function clearQueue(): Promise<void> {
  const db = await getDB()
  await db.clear(STORE_NAME)
}

/**
 * 联网时批量重放队列
 * - 单条失败重试次数 +1，超过 3 次丢弃并记录
 * - 全部成功后清空队列
 */
export async function syncQueue(): Promise<{ success: number; failed: number }> {
  if (typeof navigator !== 'undefined' && !navigator.onLine) {
    return { success: 0, failed: 0 }
  }
  const db = await getDB()
  const all = (await db.getAll(STORE_NAME)) as QueuedRequest[]
  if (all.length === 0) return { success: 0, failed: 0 }

  let success = 0
  let failed = 0
  const MAX_RETRIES = 3

  for (const item of all) {
    try {
      const payload = item.data as StoredPayload | undefined
      let body: BodyInit | undefined
      const headers: Record<string, string> = {
        'Idempotency-Key': item.idempotencyKey || crypto.randomUUID(),
      }
      const token = localStorage.getItem('token')
      if (token) headers.Authorization = `Bearer ${token}`

      if (payload?.kind === 'form') {
        const form = new FormData()
        payload.entries.forEach((entry) => {
          if (typeof entry.value === 'string') form.append(entry.name, entry.value)
          else form.append(entry.name, entry.value, entry.filename)
        })
        body = form
      } else if (payload?.kind === 'json') {
        headers['Content-Type'] = 'application/json'
        body = JSON.stringify(payload.value)
      }

      const requestUrl = item.url.startsWith('http')
        ? item.url
        : `/api${item.url.startsWith('/') ? item.url : `/${item.url}`}`
      const response = await fetch(requestUrl, {
        method: item.method,
        headers,
        body,
      })
      if (!response.ok) throw new Error(`离线请求重放失败(${response.status})`)
      success++
      if (item.id) await db.delete(STORE_NAME, item.id)
    } catch (e) {
      failed++
      item.retries = (item.retries || 0) + 1
      if (item.retries >= MAX_RETRIES && item.id) {
        // 超过重试上限，丢弃
        await db.delete(STORE_NAME, item.id)
        console.warn('离线请求重试超限，已丢弃', item.url, e)
      } else if (item.id) {
        await db.put(STORE_NAME, item)
      }
    }
  }
  return { success, failed }
}
