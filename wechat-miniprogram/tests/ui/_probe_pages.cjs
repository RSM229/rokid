/* 探测页面栈：newChat 失败说明栈顶不是 agent 页 */
const automator = require('miniprogram-automator')
async function main() {
  const mini = await automator.connect({ wsEndpoint: 'ws://127.0.0.1:9420' })
  try {
    const out = await Promise.race([
      mini.evaluate(() => JSON.stringify(getCurrentPages().map(p => p.route))),
      new Promise((_, rej) => setTimeout(() => rej(new Error('timeout')), 15000)),
    ])
    console.log('pages:', out)
  } finally { await mini.disconnect().catch(() => {}) }
}
main().catch(e => { console.error('ERR:', e.message); process.exit(1) })
