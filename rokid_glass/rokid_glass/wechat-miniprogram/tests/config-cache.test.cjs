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

test('mock opt-in is limited to develop; custom HTTPS port is preserved', () => {
  for (const envVersion of ['develop', 'trial', 'release']) {
    const { config } = load('config.ts', { wx: {
      getExtConfigSync: () => ({ mockWechatLogin: true, apiBaseUrl: 'https://test.example:4432/api/' }),
      getAccountInfoSync: () => ({ miniProgram: { envVersion } }),
    } })
    assert.equal(config.mockWechatLogin, envVersion === 'develop')
    assert.equal(config.apiBaseUrl, 'https://test.example:4432/api')
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
