const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const ts = require('typescript')

function load(relative, globals = {}) {
  const filename = path.join(__dirname, '../miniprogram', relative)
  const code = ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
  }).outputText
  const exports = {}
  vm.runInNewContext(code, { exports, ...globals }, { filename })
  return exports
}

test('real WeChat login is default in develop, trial and release', () => {
  for (const envVersion of ['develop', 'trial', 'release']) {
    const { config } = load('config.ts', { wx: {
      getExtConfigSync: () => ({}),
      getAccountInfoSync: () => ({ miniProgram: { envVersion } }),
    } })
    assert.equal(config.mockWechatLogin, false)
  }
})

test('PREFER_LOCAL_BACKEND=false disables local backend; production URL always used', () => {
  for (const envVersion of ['develop', 'trial', 'release']) {
    const { config } = load('config.ts', { wx: {
      getExtConfigSync: () => ({
        mockWechatLogin: true,
        useLocalBackend: true,
        localApiBaseUrl: 'http://127.0.0.1:8000/api/',
        apiBaseUrl: 'https://test.example:4432/api/',
      }),
      getAccountInfoSync: () => ({ miniProgram: { envVersion } }),
    } })
    // 铁律：联调直连生产后端，本地后端开关恒为 false（不受 ext.useLocalBackend 覆盖）
    assert.equal(config.useLocalBackend, false)
    assert.equal(config.apiBaseUrl, 'https://test.example:4432/api')
    // 🔴 2026-10-29 决策已定：mockWechatLogin 跟随「mock 仅限本地」铁律 ——
    // 只在 useLocalBackend=true 且 develop 且 ext.mockWechatLogin=true 时开启。
    // PREFER_LOCAL_BACKEND=false 时 useLocalBackend 恒 false，故任何环境/ext 下
    // 直连生产都不会开 mock 登录（旧行为「直连生产+develop 开 mock」是张力，已消除）。
    assert.equal(config.mockWechatLogin, false)
  }
})

test('production backend is always used when no local preference is set', () => {
  for (const envVersion of ['develop', 'trial', 'release']) {
    const { config } = load('config.ts', { wx: {
      getExtConfigSync: () => ({}),
      getAccountInfoSync: () => ({ miniProgram: { envVersion } }),
    } })
    assert.equal(config.useLocalBackend, false)
    assert.equal(config.apiBaseUrl, 'https://www.baize.gdces.cn:4432/api')
  }
})

test('old in-flight response cannot overwrite a fresh account/project cache', async () => {
  const cache = load('services/cache.ts')
  let resolveOld
  const old = cache.getCached('records', () => new Promise(resolve => { resolveOld = resolve }))
  cache.invalidateAll()
  assert.equal(await cache.getCached('records', async () => 'new account'), 'new account')
  resolveOld('old account')
  await old
  assert.equal(await cache.getCached('records', async () => 'unexpected request'), 'new account')
})

test('local and production sessions cannot read or clear each other', () => {
  const storage = new Map()
  const wx = {
    getStorageSync: key => storage.get(key),
    setStorageSync: (key, value) => storage.set(key, value),
    removeStorageSync: key => storage.delete(key),
  }
  const sessionFor = storageScope => load('services/session.ts', {
    wx,
    require: () => ({ config: { storageScope } }),
  }).session
  const local = sessionFor('http_127_0_0_1_8000_api')
  const production = sessionFor('https_production_api')
  production.save('production-token', { username: 'production-user' })
  production.saveBindToken('production-binding')
  assert.equal(local.getToken(), '')
  assert.equal(local.getUser(), null)
  local.save('local-token', { username: 'local-user' })
  local.clear()
  assert.equal(production.getToken(), 'production-token')
  assert.equal(production.getBindToken(), 'production-binding')
})

test('old failed request cannot evict a fresh account/project cache', async () => {
  const cache = load('services/cache.ts')
  let rejectOld
  const old = cache.getCached('records', () => new Promise((resolve, reject) => { rejectOld = reject }))
  cache.invalidateAll()
  await cache.getCached('records', async () => 'new account')
  rejectOld(new Error('old network error'))
  await assert.rejects(old)
  assert.equal(await cache.getCached('records', async () => 'unexpected request'), 'new account')
})
