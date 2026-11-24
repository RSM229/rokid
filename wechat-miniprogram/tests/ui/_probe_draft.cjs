/* 查新会话 loadPendingDrafts：恢复草稿的入口与条件 */
const automator = require('miniprogram-automator')
const WS = 'ws://127.0.0.1:9420'
const sleep = ms => new Promise(r => setTimeout(r, ms))
async function atomic(label, fn, timeoutMs = 30000) {
  const mini = await automator.connect({ wsEndpoint: WS })
  try { return await Promise.race([fn(mini), new Promise((_, rej) => setTimeout(() => rej(new Error(label + ' timeout')), timeoutMs))]) }
  finally { await mini.disconnect().catch(() => {}) }
}
async function main() {
  // 清掉测试草稿前先看前端 newChat 后为什么恢复了 plan #86
  const out = await atomic('read', m => m.evaluate(() => {
    const stack = getCurrentPages()
    const page = stack[stack.length - 1]
    const msgs = (page.data.messages || []).filter(x => x.role === 'user')
    return JSON.stringify({
      session: page.currentSessionId,
      userMsgs: msgs.map(x => x.text.slice(0, 60)),
      hasPendingDraft: !!(page.data.pendingDraft || page.pendingDraft),
      drafts: (page.data.drafts || []).length,
    })
  }))
  console.log('state:', out)
}
main().catch(e => { console.error('ERR:', e.message); process.exit(1) })
