/**
 * UI 测试：按用户生产库真实对话记录复刻三段场景（2026-11-24 三个改动上线后回归）
 *
 * 复刻来源（生产库 agent_conversation_messages 860-874 + work_records）：
 *   场景A（消息 873/874，11-24 14:49）：发照片 + 「记录今天的工作」
 *     —— 水印无类型词。旧行为：直接建成「待定」。
 *     新期望：先问一句类型（巡视/旁站/验收/见证），不建待定。
 *   场景B（消息 866-869，11-23 17:31）：「记一条验收」→「合格」
 *     —— 旧行为：追问句尾巴每轮复读「还差「验收类型、…」清单。
 *     新期望：尾巴只有「还没写入正式记录。」一句，无栏位清单。
 *   场景C（消息 862/866，11-23 17:04/17:31 两会话）：新会话「记一条验收」
 *     —— 生产库有未填完的验收记录（#211/#212 草稿）。
 *     新期望：先问「接着补还是另建」，不直接从头追问。
 *
 * 跑法：
 *   cli.bat auto --project <项目根> --auto-port 9420
 *   node tests/ui/agent-replay-1124.test.cjs <照片绝对路径.jpg>
 */
const fs = require('fs')
const automator = require('miniprogram-automator')

const WS = 'ws://127.0.0.1:9420'
const IMG_PATH = process.argv[2] || 'D:/rokid/.workbuddy/tmp/my_photo_1124.jpg'
const sleep = ms => new Promise(r => setTimeout(r, ms))
function log(...a) { console.log('[ui]', ...a) }

async function atomic(label, fn, timeoutMs = 30000) {
  const mini = await automator.connect({ wsEndpoint: WS })
  try {
    return await Promise.race([
      fn(mini),
      new Promise((_, rej) => setTimeout(() => rej(new Error(label + ' timeout')), timeoutMs)),
    ])
  } finally {
    await mini.disconnect().catch(() => {})
  }
}

async function readState() {
  const raw = await atomic('read', m => m.evaluate(() => {
    const stack = getCurrentPages()
    const page = stack[stack.length - 1]
    if (!page) return JSON.stringify({ session: 0, msgs: [] })
    return JSON.stringify({
      session: page.currentSessionId,
      msgs: (page.data.messages || []).map(x => ({
        role: x.role,
        t: (x.text || x.reply || '').slice(0, 300),
        failed: !!x.failed,
      })),
    })
  }))
  return JSON.parse(raw || '{}')
}

/** 深读：带图轮走 review-plan 整理卡，需要 reviewPlan/reviewIssues 字段 */
async function readStateDeep() {
  const raw = await atomic('readDeep', m => m.evaluate(() => {
    const stack = getCurrentPages()
    const page = stack[stack.length - 1]
    if (!page) return JSON.stringify({ session: 0, msgs: [] })
    return JSON.stringify({
      session: page.currentSessionId,
      msgs: (page.data.messages || []).map(x => ({
        role: x.role,
        text: (x.text || x.reply || '').slice(0, 300),
        failed: !!x.failed,
        hasReviewPlan: !!x.reviewPlan,
        reviewPlanStatus: (x.reviewPlan || {}).status || '',
        reviewEvents: ((x.reviewEvents || []) || []).slice(0, 3).map(ev => ({
          recordType: ev.recordType || ev.record_type || '',
          missing: (ev.missingFields || ev.missing_fields || []).slice(0, 6),
        })),
        reviewIssues: x.reviewIssues || [],
      })),
    })
  }))
  return JSON.parse(raw || '{}')
}

async function driveSend(msg) {
  return atomic('send', m => m.evaluate(msgArg => {
    const stack = getCurrentPages()
    const page = stack[stack.length - 1]
    if (!page) return 'NO_PAGE'
    page.setData({ input: msgArg })
    if (typeof page.send !== 'function') return 'NO_SEND'
    page.send()
    return 'SENT'
  }, msg))
}

/** 等最后一条 assistant 回复（轮询，带上限） */
async function waitReply(prevCount, label, maxMs = 60000) {
  const deadline = Date.now() + maxMs
  while (Date.now() < deadline) {
    await sleep(5000)
    const s = await readState()
    const asst = (s.msgs || []).filter(x => x.role === 'assistant')
    if (asst.length > prevCount) {
      const last = asst[asst.length - 1]
      if (!last.failed) return { count: asst.length, text: last.t, state: s }
    }
  }
  throw new Error(label + ': no new assistant reply in ' + maxMs + 'ms')
}

async function newChat() {
  const nc = await atomic('newChat', m => m.evaluate(() => {
    const stack = getCurrentPages()
    const page = stack[stack.length - 1]
    if (!page || typeof page.newChat !== 'function') return 'NO_NEWCHAT'
    page.newChat()
    return 'OK session=' + page.currentSessionId
  }))
  if (!String(nc).startsWith('OK')) throw new Error('newChat failed: ' + nc)
  await sleep(2500)
  return String(nc)
}

const results = []
function report(name, pass, detail) {
  results.push({ name, pass, detail })
  log((pass ? '✅' : '❌') + ' ' + name + ' | ' + detail)
}

async function main() {
  const imgBase64 = fs.readFileSync(IMG_PATH).toString('base64')
  log('photo loaded:', IMG_PATH, Math.round(imgBase64.length / 1024) + 'KB(b64)')

  await atomic('reLaunch', m => m.evaluate(() => {
    wx.reLaunch({ url: '/pages/agent/index' })
    return 'OK'
  }))
  await sleep(3500)

  // ============ 场景A：照片 + 「记录今天的工作」（复刻 873/874） ============
  log('--- 场景A：照片无类型词 → 应先问类型，不建待定 ---')
  await newChat()
  const s0 = await readState()
  const aBase = (s0.msgs || []).filter(x => x.role === 'assistant').length

  const wf = await atomic('writeFile', m => m.evaluate(b64 => {
    return new Promise(resolve => {
      const fsMgr = wx.getFileSystemManager()
      const filePath = `${wx.env.USER_DATA_PATH}/replay_photo_1124.jpg`
      fsMgr.writeFile({
        filePath,
        data: b64,
        encoding: 'base64',
        success: () => resolve('WROTE ' + filePath),
        fail: err => resolve('FAIL ' + (err.errMsg || JSON.stringify(err))),
      })
    })
  }, imgBase64), 60000)
  if (!String(wf).startsWith('WROTE')) throw new Error('write image failed: ' + wf)
  const simPath = String(wf).replace('WROTE ', '')

  // 发图 + 文字：走与生产 873 一致的入口（attachments + input → send）
  const sentA = await atomic('sendAttachments', m => m.evaluate(path => {
    const stack = getCurrentPages()
    const page = stack[stack.length - 1]
    if (!page || typeof page.send !== 'function') return 'NO_METHOD'
    const attachment = {
      id: 'ui-replay-img-1',
      kind: 'image',
      path,
      name: 'replay_photo_1124.jpg',
      ext: 'jpg',
      size: 577425,
    }
    page.setData({ attachments: [attachment], input: '记录今天的工作' })
    Promise.resolve(page.send()).catch(err => console.error('[ui-test] send error', err))
    return 'SENT'
  }, simPath), 30000)
  if (sentA !== 'SENT') throw new Error('sceneA send failed: ' + sentA)

  log('waiting sceneA reply (photo upload + OCR + plan, up to 120s) ...')
  // 带图轮走 review-plans 整理卡链：等最后一条 assistant 且结构稳定
  let aText = ''
  let aPlan = null
  const deadlineA = Date.now() + 120000
  let aCount = -1
  while (Date.now() < deadlineA) {
    await sleep(8000)
    const s = await readStateDeep()
    const asst = (s.msgs || []).filter(x => x.role === 'assistant')
    const last = asst[asst.length - 1]
    if (last && (last.text || last.hasReviewPlan)) {
      aText = last.text || ''
      aPlan = last
      aCount = asst.length
      if (!/整理中|正在|识别中/.test(aText)) break // 等非进行中文案
    }
  }
  if (aCount < 0) throw new Error('sceneA: no assistant reply in 120s')
  log('sceneA reply text:', JSON.stringify(aText.slice(0, 120)))
  log('sceneA reviewPlan:', JSON.stringify({
    has: !!aPlan.hasReviewPlan, status: aPlan.reviewPlanStatus,
    issues: aPlan.reviewIssues, events: aPlan.reviewEvents,
  }))
  // A1：先问类型（reviewPlan issue 或正文问类型），不建「待定」正式记录
  const asksType = /记录类型/.test(aText)
    || (aPlan.reviewIssues || []).some(i => /记录类型/.test(i))
  const builtTbd = /已记录：待定/.test(aText) && !/待确认草稿/.test(aText)
  report('A1-先问类型不建待定', asksType && !builtTbd,
    '问类型=' + asksType + ' 直接建待定=' + builtTbd + ' | ' + (aText || '').slice(0, 90))

  // A2：答「验收」→ 整理卡更新为验收类型（照片已挂在证据链上）
  await sleep(1500)
  const sentA2 = await driveSend('验收')
  if (sentA2 !== 'SENT') throw new Error('sceneA2 send failed: ' + sentA2)
  log('waiting sceneA2 (up to 90s) ...')
  let a2Text = ''
  let a2Plan = null
  const deadlineA2 = Date.now() + 90000
  while (Date.now() < deadlineA2) {
    await sleep(6000)
    const s = await readStateDeep()
    const asst = (s.msgs || []).filter(x => x.role === 'assistant')
    const last = asst[asst.length - 1]
    if (last && asst.length > aCount) {
      a2Text = last.text || ''
      a2Plan = last
      if (!/整理中|正在|识别中/.test(a2Text)) break
    }
  }
  log('sceneA2 text:', JSON.stringify((a2Text || '').slice(0, 120)))
  log('sceneA2 plan:', JSON.stringify({
    has: !!(a2Plan || {}).hasReviewPlan,
    events: (a2Plan || {}).reviewEvents, issues: (a2Plan || {}).reviewIssues,
  }))
  const a2IsAcceptance = /验收/.test(a2Text)
    || ((a2Plan || {}).reviewEvents || []).some(ev => /验收/.test(ev.recordType || ''))
  report('A2-答类型后转验收整理', a2IsAcceptance, (a2Text || '').slice(0, 90))

  // A3：尾巴无栏位清单
  const hasListTail = /还差「.+、/.test(a2Text)
  report('A3-尾巴无栏位清单', !hasListTail, '清单尾巴=' + hasListTail)

  // ============ 场景B：纯文字「记一条验收」→「合格」（复刻 866-869） ============
  log('--- 场景B：验收追问轮尾巴只留状态句 ---')
  await newChat()
  const sB = await readState()
  const bBase = (sB.msgs || []).filter(x => x.role === 'assistant').length
  const sentB = await driveSend('记一条验收')
  if (sentB !== 'SENT') throw new Error('sceneB send failed: ' + sentB)
  const b1 = await waitReply(bBase, 'sceneB-turn1', 90000)
  log('sceneB turn1 reply:', JSON.stringify(b1.text))
  const b1HasList = /还差「.+、/.test(b1.text)
  report('B1-尾巴无栏位清单', !b1HasList, '清单尾巴=' + b1HasList + ' | ' + (b1.text || '').slice(0, 80))

  await sleep(1500)
  const sentB2 = await driveSend('合格')
  if (sentB2 !== 'SENT') throw new Error('sceneB2 send failed: ' + sentB2)
  const b2 = await waitReply(b1.count, 'sceneB-turn2', 90000)
  log('sceneB turn2 reply:', JSON.stringify(b2.text))
  const b2HasList = /还差「.+、/.test(b2.text)
  const absorbed = /合格/.test(b2.text)
  report('B2-合格被吸收且尾巴无清单', absorbed && !b2HasList,
    '吸收=' + absorbed + ' 清单=' + b2HasList + ' | ' + (b2.text || '').slice(0, 80))

  // ============ 场景C：新会话「记一条验收」→ 先问接哪条（复刻 17:04/17:31 两会话） ============
  log('--- 场景C：跨会话续办询问 ---')
  await newChat()
  const sC = await readState()
  const cBase = (sC.msgs || []).filter(x => x.role === 'assistant').length
  const sentC = await driveSend('记一条验收')
  if (sentC !== 'SENT') throw new Error('sceneC send failed: ' + sentC)
  const c1 = await waitReply(cBase, 'sceneC-turn1', 90000)
  log('sceneC turn1 reply:', JSON.stringify(c1.text))
  const asksContinue = /接着|另建/.test(c1.text) && /没填完|补完/.test(c1.text)
  report('C1-先问接着补还是另建', asksContinue,
    (c1.text || '').slice(0, 100))

  if (asksContinue) {
    // C2：答「接着补」→ 转入续办（问具体缺栏）
    await sleep(1500)
    const sentC2 = await driveSend('接着补')
    if (sentC2 !== 'SENT') throw new Error('sceneC2 send failed: ' + sentC2)
    const c2 = await waitReply(c1.count, 'sceneC-turn2', 90000)
    log('sceneC turn2 reply:', JSON.stringify(c2.text))
    const continues = /验收|补/.test(c2.text)
    report('C2-接着补转入续办', continues, (c2.text || '').slice(0, 100))
  }

  // ============ 汇总 ============
  const failed = results.filter(r => !r.pass)
  console.log('\n========== 结果汇总 ==========')
  results.forEach(r => console.log((r.pass ? '  ✅ ' : '  ❌ ') + r.name + ' | ' + r.detail))
  console.log('通过 ' + (results.length - failed.length) + '/' + results.length)
  if (failed.length) {
    console.log('\nUI_REPLAY_FAILED')
    process.exit(1)
  }
  console.log('\nUI_REPLAY_PASS')
  process.exit(0)
}

main().catch(e => {
  console.error('[ui] FAILED:', e.message)
  results.forEach(r => console.log((r.pass ? '  ✅ ' : '  ❌ ') + r.name + ' | ' + r.detail))
  process.exit(1)
})
setTimeout(() => { console.error('[ui] GLOBAL TIMEOUT 600s'); process.exit(2) }, 600000)
