/**
 * UI 深走测试：照片建验收全链路闭环 + 「另建」分支（2026-11-24 第二轮）
 *
 * 目标：把改动1 的完整用户旅程走到底，找出任何卡点：
 *   照片 + 「记录今天的工作」→ 问类型 → 答「验收」→ 逐栏作答
 *   → 最终确认 → 「确认生成」→ 记录落库（回执带记录号）
 *   然后新会话「记一条验收」→ 续办问句 → 答「另建」→ 应走全新建档
 *
 * 走查器：每轮解析回执被问栏位（还差一项：X / 还差「X」/ 整理卡 issue），
 * 按答案表作答；同栏重复出现 → 重答 → 再重复答「跳过」→ 仍重复 = 卡住，
 * dump 完整回执退出。
 *
 * 跑法：cli.bat auto --project <项目根> --auto-port 9420
 *   node tests/ui/agent-walk-full.test.cjs <照片绝对路径.jpg>
 */
const fs = require('fs')
const automator = require('miniprogram-automator')

const WS = 'ws://127.0.0.1:9420'
const IMG_PATH = process.argv[2] || 'D:/rokid/.workbuddy/tmp/my_photo_1124.jpg'
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
async function readStateDeep() {
  const raw = await atomic('readDeep', m => m.evaluate(() => {
    const stack = getCurrentPages()
    const page = stack[stack.length - 1]
    if (!page) return JSON.stringify({ session: 0, msgs: [] })
    return JSON.stringify({
      session: page.currentSessionId,
      msgs: (page.data.messages || []).map(x => ({
        role: x.role,
        text: (x.text || x.reply || '').slice(0, 600),
        failed: !!x.failed,
        hasReviewPlan: !!x.reviewPlan,
        reviewPlanStatus: (x.reviewPlan || {}).status || '',
        reviewEvents: ((x.reviewEvents || []) || []).slice(0, 3).map(ev => ({
          recordType: ev.recordType || ev.record_type || '',
          missing: (ev.missingFields || ev.missing_fields || []).slice(0, 8),
        })),
        reviewIssues: x.reviewIssues || [],
      })),
    })
  }))
  return JSON.parse(raw || '{}')
}

/** 消息签名：识别「同一条消息的内容变化」（进度气泡→最终回执是原地替换） */
function sigOf(msg) {
  if (!msg) return '∅'
  return JSON.stringify([
    (msg.text || '').slice(0, 100),
    !!msg.hasReviewPlan,
    msg.reviewIssues || [],
    (msg.reviewEvents || []).map(e => e.recordType),
  ])
}
const snap = r => ({ count: r.count, sig: sigOf(r.raw) })

/** 等新 assistant 回复并返回其深结构。
 *  有效回复 = 非失败 && (text 长度>3 且非进行中文案 || 带 reviewPlan)；
 *  且相对 prev（count 或签名）有变化、连续两次读到相同内容（稳定）。 */
async function waitReplyDeep(prev, label, maxMs = 90000) {
  const deadline = Date.now() + maxMs
  let lastSig = null
  let stable = 0
  while (Date.now() < deadline) {
    await sleep(6000)
    const s = await readStateDeep()
    const asst = (s.msgs || []).filter(x => x.role === 'assistant')
    if (!asst.length) continue
    const last = asst[asst.length - 1]
    const t = last.text || ''
    const meaningful = !last.failed
      && ((t.length > 3 && !/整理中|识别中|正在处理/.test(t)) || last.hasReviewPlan)
    const changed = asst.length > prev.count || sigOf(last) !== prev.sig
    if (meaningful && changed) {
      const sig = sigOf(last)
      if (sig === lastSig) {
        stable++
        if (stable >= 2) return { count: asst.length, text: t, raw: last, state: s }
      } else {
        stable = 0
        lastSig = sig
      }
    } else {
      lastSig = null
      stable = 0
    }
  }
  throw new Error(label + ': no new assistant reply in ' + maxMs + 'ms')
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
/* ---- 栏位解析：从回执文本里挖出「当前被问的栏位」 ---- */
/** 匹配「还差一项：X」「还差「X」」「事项N：请先确认记录类型」等句式 */
function parseAskedField(text, raw) {
  // 整理卡 issue（带图轮）：「事项 1 请选择工作记录类型」
  if ((raw && raw.reviewIssues || []).some(i => /记录类型/.test(i))) return '记录类型'
  let m = text.match(/还差一项[:：]\s*(.+?)（/)
  if (m) return m[1].trim()
  m = text.match(/还差一项[:：]\s*(.+)$/)
  if (m) return m[1].trim()
  m = text.match(/还差「(.+?)」/)
  if (m) return m[1].trim()
  // 「接着说一下是哪一类验收就行」→ 验收类型
  if (/哪一类验收/.test(text)) return '验收类型'
  return null
}

/** 栏位答案表：问什么答什么（模拟真实用户逐栏作答） */
const FIELD_ANSWERS = {
  '记录类型': '验收',
  '验收类型': '隐蔽工程验收',
  '施工单位自检结论': '自检合格',
  '监理验收结论': '同意验收',
  '举牌验收照片': '照片已发过了，用刚才那张',
  '验收部位、工序、材料、设备进场': 'ssy37-38段雨水管道，现场照片里就是',
  '验收内容（验收要点）': '管道基础、安管、接口外观检查',
}

/** 走查一轮：解析栏位 → 查重 → 作答。返回 {done, field, answer, stuck} */
function planNextMove(text, raw, askedCount, log) {
  const field = parseAskedField(text, raw)
  if (!field) {
    // 没问栏位：看是否到了确认/完成阶段
    if (/确认生成|生成正式/.test(text)) return { done: 'confirm' }
    if (/已写入正式记录|已记录：/.test(text)) return { done: 'written' }
    return { done: 'unknown' }
  }
  const count = (askedCount[field] || 0) + 1
  askedCount[field] = count
  if (count === 1) {
    const answer = FIELD_ANSWERS[field] != null ? FIELD_ANSWERS[field] : '好，' + field + '这栏先跳过'
    return { field, answer, mode: 'answer' }
  }
  if (count === 2) return { field, answer: FIELD_ANSWERS[field] || field, mode: 're-answer' }
  if (count === 3) return { field, answer: '跳过', mode: 'skip' }
  return { field, answer: null, mode: 'stuck', stuck: '同栏「' + field + '」连续 3 轮仍在问' }
}
async function main() {
  const imgBase64 = fs.readFileSync(IMG_PATH).toString('base64')
  log('photo loaded:', IMG_PATH, Math.round(imgBase64.length / 1024) + 'KB(b64)')

  await atomic('reLaunch', m => m.evaluate(() => {
    wx.reLaunch({ url: '/pages/agent/index' })
    return 'OK'
  }))
  await sleep(3500)

  // ============ 阶段1：照片 + 记录今天的工作 → 问类型 → 答「验收」 ============
  log('--- 阶段1：照片→问类型→答验收→逐栏走到底 ---')
  await newChat()
  let s = await readStateDeep()
  let base = (s.msgs || []).filter(x => x.role === 'assistant').length

  const wf = await atomic('writeFile', m => m.evaluate(b64 => {
    return new Promise(resolve => {
      const fsMgr = wx.getFileSystemManager()
      const filePath = `${wx.env.USER_DATA_PATH}/walk_photo_1124.jpg`
      fsMgr.writeFile({
        filePath, data: b64, encoding: 'base64',
        success: () => resolve('WROTE ' + filePath),
        fail: err => resolve('FAIL ' + (err.errMsg || JSON.stringify(err))),
      })
    })
  }, imgBase64), 60000)
  if (!String(wf).startsWith('WROTE')) throw new Error('write image failed: ' + wf)
  const simPath = String(wf).replace('WROTE ', '')

  const sent = await atomic('sendAttachments', m => m.evaluate(path => {
    const stack = getCurrentPages()
    const page = stack[stack.length - 1]
    if (!page || typeof page.send !== 'function') return 'NO_METHOD'
    page.setData({
      attachments: [{ id: 'ui-walk-img-1', kind: 'image', path, name: 'walk_photo_1124.jpg', ext: 'jpg', size: 577425 }],
      input: '记录今天的工作',
    })
    Promise.resolve(page.send()).catch(err => console.error('[ui-test] send error', err))
    return 'SENT'
  }, simPath), 30000)
  if (sent !== 'SENT') throw new Error('photo send failed: ' + sent)

  log('waiting photo turn (upload+OCR+plan, up to 150s) ...')
  const sigPrev = { count: base, sig: '∅' }
  let r = await waitReplyDeep(sigPrev, 'photo-turn', 150000)
  log('photo reply:', JSON.stringify((r.text || '').slice(0, 120)))
  log('photo plan:', JSON.stringify({ issues: r.raw.reviewIssues, events: r.raw.reviewEvents }))
  const photoReplyText = r.text || ''
  const photoReplyIssues = r.raw.reviewIssues || []
  // ============ 阶段2：走查循环（最多 15 轮） ============
  const askedCount = {}
  const turnLog = []
  let writtenRecordNo = null
  let confirmed = false

  for (let i = 0; i < 15; i++) {
    const move = planNextMove(r.text, r.raw, askedCount, log)
    if (move.done) {
      if (move.done === 'confirm' && !confirmed) {
        log('turn ' + i + ': 到达确认阶段 → 答「确认生成」')
        turnLog.push({ turn: i, type: 'confirm' })
        await sleep(1500)
        const sc = await driveSend('确认生成')
        if (sc !== 'SENT') throw new Error('confirm send failed: ' + sc)
        confirmed = true
        r = await waitReplyDeep({ count: r.count, sig: sigOf(r.raw) }, 'confirm-turn', 90000)
        log('confirm reply:', JSON.stringify((r.text || '').slice(0, 150)))
        const no = (r.text || '').match(/#(\d+)/)
        if (no) writtenRecordNo = no[1]
        continue
      }
      if (move.done === 'written') {
        const no = (r.text || '').match(/#(\d+)/)
        if (no) writtenRecordNo = no[1]
        log('turn ' + i + ': 已写入正式记录 #' + writtenRecordNo)
        break
      }
      // unknown：没问栏位也没确认没写入 —— dump 出来看是什么
      log('turn ' + i + ': 未见栏位问句/确认/写入 → dump 回执判断')
      turnLog.push({ turn: i, type: 'unknown', text: r.text })
      // 试探性继续：发一个中性追问
      await sleep(1500)
      const sc = await driveSend('继续')
      if (sc !== 'SENT') throw new Error('unknown send failed: ' + sc)
      r = await waitReplyDeep({ count: r.count, sig: sigOf(r.raw) }, 'unknown-turn-' + i, 90000)
      log('unknown reply:', JSON.stringify((r.text || '').slice(0, 150)))
      continue
    }
    if (move.mode === 'stuck') {
      console.error('\n[STUCK] ' + move.stuck)
      console.error('回执全文:\n' + (r.text || ''))
      console.error('reviewPlan:', JSON.stringify(r.raw))
      console.error('\nWALK_STUCK')
      process.exit(3)
    }
    log('turn ' + i + ': 问「' + move.field + '」(' + move.mode + ') → 答「' + move.answer + '」')
    turnLog.push({ turn: i, type: move.mode, field: move.field, answer: move.answer })
    await sleep(1500)
    const sc = await driveSend(move.answer)
    if (sc !== 'SENT') throw new Error('turn ' + i + ' send failed: ' + sc)
    r = await waitReplyDeep({ count: r.count, sig: sigOf(r.raw) }, 'walk-turn-' + i, 90000)
    log('turn ' + i + ' reply:', JSON.stringify((r.text || '').slice(0, 150)))
  }
  log('阶段2结束: writtenRecordNo=' + writtenRecordNo + ' confirmed=' + confirmed)
  // ============ 阶段3：另建分支（新会话 → 续办问句 → 答「另建」） ============
  log('--- 阶段3：新会话「记一条验收」→ 答「另建」 ---')
  await newChat()
  s = await readStateDeep()
  base = (s.msgs || []).filter(x => x.role === 'assistant').length
  const sb = await driveSend('记一条验收')
  if (sb !== 'SENT') throw new Error('fresh send failed: ' + sb)
  let r3 = await waitReplyDeep({ count: base, sig: '∅' }, 'fresh-turn', 90000)
  log('fresh reply:', JSON.stringify((r3.text || '').slice(0, 150)))
  const asksContinue = /接着.*补|另建/.test(r3.text || '')
  log('续办问句出现: ' + asksContinue)

  let freshStarted = null
  if (asksContinue) {
    await sleep(1500)
    const sa = await driveSend('另建')
    if (sa !== 'SENT') throw new Error('reply-另建 send failed: ' + sa)
    const r3b = await waitReplyDeep({ count: r3.count, sig: sigOf(r3.raw) }, 'another-turn', 90000)
    log('另建 reply:', JSON.stringify((r3b.text || '').slice(0, 200)))
    // 另建后应开始全新建档（问栏位），不应出现「#212」等旧记录号
    const noOldRecord = !/#21[0-9]/.test(r3b.text || '')
    const asksField = parseAskedField(r3b.text || '', r3b.raw) != null || /记「|验收/.test(r3b.text || '')
    freshStarted = noOldRecord && asksField
    r3 = r3b
  }

  // ============ 汇总 ============
  const photoTurnAskedType = /记录类型/.test((photoReplyText || '').slice(0, 300))
    || (photoReplyIssues || []).some(i => /记录类型/.test(i))
  console.log('\n========== 深走结果 ==========')
  console.log((photoTurnAskedType ? '✅' : '❌') + ' P1-照片先问类型 | issue=' + JSON.stringify(photoReplyIssues))
  console.log((writtenRecordNo ? '✅' : '❌') + ' P2-写入正式记录 | #' + writtenRecordNo)
  console.log((confirmed ? '✅' : '❌') + ' P3-确认生成触发')
  console.log((freshStarted === null ? '⏭' : freshStarted ? '✅' : '❌') + ' P4-另建走全新建档')
  console.log('逐轮轨迹:')
  turnLog.forEach(t => console.log('  ' + JSON.stringify(t)))
  const allPass = photoTurnAskedType && writtenRecordNo && confirmed
  console.log(allPass ? '\nWALK_FULL_PASS' : '\nWALK_PARTIAL')
  process.exit(0)
}

main().catch(e => {
  console.error('[ui] FAILED:', e.message)
  process.exit(1)
})
setTimeout(() => { console.error('[ui] GLOBAL TIMEOUT 900s'); process.exit(2) }, 900000)





