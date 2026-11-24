/* 深度读消息结构：带图轮走 review-plans 整理卡，气泡结构不同 */
const automator = require('miniprogram-automator')
const WS = 'ws://127.0.0.1:9420'
const sleep = ms => new Promise(r => setTimeout(r, ms))

async function atomic(label, fn, timeoutMs = 30000) {
  const mini = await automator.connect({ wsEndpoint: WS })
  try {
    return await Promise.race([
      fn(mini),
      new Promise((_, rej) => setTimeout(() => rej(new Error(label + ' timeout')), timeoutMs)),
    ])
  } finally { await mini.disconnect().catch(() => {}) }
}

async function main() {
  // 持续读 90 秒，把所有字段 dump 出来
  const raw = await atomic('dump', m => m.evaluate(() => {
    const stack = getCurrentPages()
    const page = stack[stack.length - 1]
    if (!page) return '{}'
    const msgs = (page.data.messages || []).map(x => ({
      role: x.role,
      text: (x.text || '').slice(0, 150),
      reply: (x.reply || '').slice(0, 150),
      hasReviewPlan: !!x.reviewPlan,
      reviewPlanStatus: (x.reviewPlan || {}).status || '',
      reviewEvents: ((x.reviewEvents || []) || []).slice(0, 3).map(ev => ({
        recordType: ev.recordType || ev.record_type || '',
        missing: (ev.missingFields || ev.missing_fields || []).slice(0, 6),
      })),
      reviewIssues: x.reviewIssues || [],
      status: x.status || x.loading || undefined,
    }))
    return JSON.stringify({ session: page.currentSessionId, count: msgs.length, msgs })
  }), 100000)
  const s = JSON.parse(raw || '{}')
  console.log('session:', s.session, '| msgs:', s.count)
  ;(s.msgs || []).forEach((m, i) => {
    console.log(`#${i} [${m.role}]`, JSON.stringify(m).slice(0, 400))
  })
}
main().catch(e => { console.error('ERR:', e.message); process.exit(1) })
