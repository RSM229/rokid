interface CacheEntry<T> {
  data: T
  timestamp: number
  promise?: Promise<T>
}

interface CacheOptions {
  ttl?: number
  key?: string
}

const DEFAULT_TTL = 60 * 1000

const cache = new Map<string, CacheEntry<unknown>>()

export function getCached<T>(
  key: string,
  fetcher: () => Promise<T>,
  options: CacheOptions = {},
): Promise<T> {
  const ttl = options.ttl ?? DEFAULT_TTL
  const entry = cache.get(key) as CacheEntry<T> | undefined

  if (entry && Date.now() - entry.timestamp < ttl) {
    return Promise.resolve(entry.data)
  }

  if (entry?.promise) {
    return entry.promise
  }

  const promise = fetcher().then((data) => {
    // 退出登录/切换项目期间发出的旧请求不得重新填充已失效的缓存。
    if (cache.get(key)?.promise === promise) cache.set(key, { data, timestamp: Date.now() })
    return data
  }).catch((error) => {
    if (cache.get(key)?.promise === promise) cache.delete(key)
    throw error
  })

  cache.set(key, { data: undefined as unknown as T, timestamp: 0, promise })
  return promise
}

export function invalidateCache(key: string): void {
  cache.delete(key)
}

export function invalidateAll(): void {
  cache.clear()
}

export function invalidateByPrefix(prefix: string): void {
  for (const key of cache.keys()) {
    if (key.startsWith(prefix)) {
      cache.delete(key)
    }
  }
}

export const CACHE_KEYS = {
  HAZARD_LIST: '/hazard/list',
  WORK_RECORD_STATS: '/work-record/stats',
  TASK_TODO_STATS: '/task/todo-stats',
  KB_LIST: '/kb',
  KB_PLAZA: '/kb/plaza',
  INSPECTION_STATS: '/inspection/stats',
  INSPECTION_TREND: '/inspection/trend',
  INSPECTION_CATEGORY: '/inspection/category-distribution',
} as const
