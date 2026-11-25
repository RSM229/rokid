/** 假说验证：每个调用都重连一次（Connection 复用 bug 绕过） */
const automator = require('miniprogram-automator')

async function freshCall(label, fn) {
  const mini = await automator.connect({ wsEndpoint: 'ws://127.0.0.1:9420' })
  try {
    const r = await fn(mini)
    console.log('[fresh]', label, 'OK:', String(r).slice(0, 100))
    return r
  } catch (e) {
    console.log('[fresh]', label, 'ERR:', e.message)
    return null
  } finally {
    await mini.disconnect().catch(() => {})
  }
}

async function main() {
  // 无项目 hero 降级验证：读 visibleHeroActions（user 1 有项目，只验有项目侧
  // 不受影响 + 数据源 heroActions 的 needProject 结构仍在）
  await freshCall('hero#1', m => m.evaluate(() => {
    const stack = getCurrentPages()
    const page = stack[stack.length - 1]
    return JSON.stringify({
      noProject: page.data.noProject,
      visible: (page.data.visibleHeroActions || []).map(a => a.text),
    })
  }))
  process.exit(0)
}
main()
setTimeout(() => { console.error('GLOBAL TIMEOUT'); process.exit(2) }, 90000)
