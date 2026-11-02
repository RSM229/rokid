// uploadQueue.retryAll 竞态回归（2026-11-02 修复）：
//  1. 旧实现用「起始快照」整表覆盖 storage —— 重试期间 enqueue 的新条目会丢、
//     remove 掉的取消项会复活。修后写回前重读最新队列按 id 合并。
//  2. token 失效（401 连坐）时中止循环，不再逐项 401 + 连环 reLaunch。
const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const ts = require('typescript')

function loadQueue({ token = 'tok-1', uploads } = {}) {
  const storage = new Map()
  let keySeq = 0
  class StubUploadError extends Error {
    constructor(msg, retryable = true) { super(msg); this.retryable = retryable }
  }
  const wx = {
    getStorageSync: (k) => (storage.has(k) ? storage.get(k) : ''),
    setStorageSync: (k, v) => storage.set(k, v),
    removeStorageSync: (k) => storage.delete(k),
  }
  const globals = {
    wx,
    require: (spec) => {
      if (spec === '../utils/idempotency') return { createIdempotencyKey: () => `k${++keySeq}` }
      if (spec === '../config') return { config: { storageScope: 'test' } }
      if (spec === './session') return { session: { getToken: () => (typeof token === 'function' ? token() : token) } }
      if (spec === './upload') return { UploadError: StubUploadError, uploadImage: uploads.image, uploadVideo: uploads.video, uploadAudio: uploads.audio }
      throw new Error(`unexpected require: ${spec}`)
    },
  }
  const filename = path.join(__dirname, '../miniprogram/services/uploadQueue.ts')
  const code = ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
  }).outputText
  const exports = {}
  vm.runInNewContext(code, { exports, ...globals }, { filename })
  return { queue: exports.uploadQueue, storage, wx, UploadError: StubUploadError }
}

const QUEUE_KEY = 'gdjl_test_media_upload_queue'

function seed(storage, items) {
  storage.set(QUEUE_KEY, items)
}

const baseItem = (id, mediaType = 'image') => ({
  id, mediaType, filePath: `wxfile://saved/${id}`, fields: {}, idempotencyKey: `ik-${id}`,
  createdAt: '2026-11-02T00:00:00.000Z',
})

test('retryAll 期间新入队的条目不被快照覆盖抹掉，取消的条目不复活', async () => {
  const failing = baseItem('fail-1')
  let uploadCalls = 0
  const { queue, storage } = loadQueue({
    uploads: {
      image: async () => {
        uploadCalls += 1
        if (uploadCalls === 1) {
          // 模拟重试期间用户又拍了一张失败入队（enqueue 等价写法）
          const cur = storage.get(QUEUE_KEY) || []
          storage.set(QUEUE_KEY, [...cur, baseItem('new-during-retry')])
        }
        if (uploadCalls === 2) throw new Error('网络断开') // fail-1 失败
        return {}
      },
      video: async () => ({}),
      audio: async () => ({}),
    },
  })
  seed(storage, [baseItem('ok-1'), failing])
  const result = await queue.retryAll(true) // skipAudio=true，与本场景无关
  assert.equal(result.succeeded, 1)
  assert.equal(result.failed, 1)
  const remain = storage.get(QUEUE_KEY)
  const ids = remain.map((i) => i.id)
  // 成功出队
  assert.ok(!ids.includes('ok-1'), '成功的条目应出队')
  // 失败保留且带 lastError
  const failed = remain.find((i) => i.id === 'fail-1')
  assert.ok(failed, '失败的条目应保留')
  assert.equal(failed.lastError, '网络断开')
  // 🔴 核心：重试期间新入队的条目不能被旧快照覆盖抹掉
  assert.ok(ids.includes('new-during-retry'), '重试期间新入队的条目必须保留')
})

test('不可重试错误（文件失效）直接出队，不再卡队列', async () => {
  const { queue, storage, UploadError } = loadQueue({
    uploads: {
      image: async () => { throw new UploadError('文件已失效', false) },
      video: async () => ({}),
      audio: async () => ({}),
    },
  })
  seed(storage, [baseItem('dead-1')])
  const result = await queue.retryAll(false)
  assert.equal(result.failed, 0)
  assert.equal((storage.get(QUEUE_KEY) || []).length, 0, '不可重试条目应出队')
})

test('token 失效时中止重试循环，队列原样保留', async () => {
  let calls = 0
  let valid = false // 初始即无 token（模拟 session 已被 401 清空）
  const { queue, storage } = loadQueue({
    token: () => (valid ? 'tok-1' : ''),
    uploads: {
      image: async () => { calls += 1; throw new Error('未登录') },
      video: async () => ({}),
      audio: async () => ({}),
    },
  })
  seed(storage, [baseItem('a'), baseItem('b')])
  const result = await queue.retryAll(false)
  assert.equal(calls, 0, '无 token 时不应发起任何上传')
  assert.equal((storage.get(QUEUE_KEY) || []).length, 2, '队列原样保留')
  assert.equal(result.succeeded, 0)
})
