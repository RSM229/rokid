/* 「另建」分支单独走查：新会话「记一条验收」→ 答「另建」→ 应走全新建档 */
const automator = require('miniprogram-automator')
const WS = 'ws://127.0.0.1:9420'
const sleep = ms => new Promise(r => setTimeout(r, ms))
function log(...a) { console.log('[ui]', new Date().toISOString().slice(11, 19), ...a) }

async function atomic(label, fn, timeoutMs = 30000) {
  const mini = await automator.connect({ wsEndpoint: WS })
  try {
    return await Promise.race([
      fn(mini),
      new Promise((_, rej) => setTimeout(() => rej(new Error(label + ' timeout')), timeoutMs)),
    ])
  } finally { await mini.disconnect().catch(() => {}) }
}

async function readLast() {
  const raw = await atomic('read', m => m.evaluate(() => {
    const stack = getCurrentPages()
    const page = stack[stack.length - 1]
    if (!page) return '{}'
    const msgs = (page.data.messages || []).filter(x => x.role === 'assistant')
    const last = msgs[msgs.length - 1] || {}
    return JSON.stringify({ count: msgs.length, text: (last.text || '').slice(0, 400) })
  }))
  return JSON.parse(raw || '{}')
}

async function waitNew(prevCount, label, maxMs = 90000) {
  const deadline = Date.now() + maxMs
  let lastText = null, stable = 0
  while (Date.now() < deadline) {
    await sleep(6000)
    const s = await readLast()
    if (s.count > prevCount) {
      const t = s.text || ''
      if (t.length > 3 && !/整理中|识别中|正在/.test(t)) {
        if (t === lastText) { if (++stable >= 2) return s } else { stable = 0; lastText = t }
      }
    }
  }
  throw new Error(label + ': timeout')
}

async function driveSend(msg) {
  return atomic('send', m => m.evaluate(msgArg => {
    const stack = getCurrentPages()
    const page = stack[stack.length - 1]
    if (!page) return 'NO_PAGE'
    page.setData({ input: msgArg })
    page.send()
    return 'SENT'
  }, msg))
}

async function main() {
  await atomic('reLaunch', m => m.evaluate(() => { wx.reLaunch({ url: '/pages/agent/index' }); return 'OK' }))
  await sleep(4000)
  // reLaunch 后页面栈顶应是 agent；若没切过去（偶发）重试一次
  let top = await atomic('checkPage', m => m.evaluate(() => getCurrentPages().map(p => p.route).join(',')))
  if (!String(top).includes('agent')) {
    await atomic('reLaunch2', m => m.evaluate(() => { wx.reLaunch({ url: '/pages/agent/index' }); return 'OK' }))
    await sleep(4000)
  }
  const nc = await atomic('newChat', m => m.evaluate(() => {
    const stack = getCurrentPages()
    const page = stack[stack.length - 1]
    if (!page || typeof page.newChat !== 'function') return 'NO_NEWCHAT'
    page.newChat()
    return 'OK'
  }))
  if (!String(nc).startsWith('OK')) throw new Error('newChat failed: ' + nc)
  await sleep(2500)
  let s = await readLast()
  const base = s.count

  const s1 = await driveSend('记一条验收')
  if (s1 !== 'SENT') throw new Error('send1 failed')
  const r1 = await waitNew(base, 'turn1')
  log('turn1:', JSON.stringify((r1.text || '').slice(0, 200)))
  const asksContinue = /接着.*补|另建/.test(r1.text || '')
  console.log('续办问句出现: ' + (asksContinue ? '✅' : '❌'))

  if (!asksContinue) { console.log('CONTINUE_BRANCH_NOT_TRIGGERED'); process.exit(0) }
  await sleep(1500)
  const s2 = await driveSend('另建')
  if (s2 !== 'SENT') throw new Error('send2 failed')
  const r2 = await waitNew(r1.count, 'turn2')
  log('turn2:', JSON.stringify((r2.text || '').slice(0, 300)))
  const noOld = !/#21\d/.test(r2.text || '')
  const fresh = /还差|验收|草稿/.test(r2.text || '')
  console.log((noOld ? '✅' : '❌') + ' 不引用旧记录号')
  console.log((fresh ? '✅' : '❌') + ' 开始全新建档')
  console.log(noOld && fresh ? 'ANOTHER_PASS' : 'ANOTHER_FAIL')
  process.exit(0)
}
main().catch(e => { console.error('FAILED:', e.message); process.exit(1) })
setTimeout(() => { console.error('TIMEOUT'); process.exit(2) }, 300000)
