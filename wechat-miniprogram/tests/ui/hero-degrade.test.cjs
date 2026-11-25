/* UI 测试：无项目用户 Hero 卡降级展示的完整链路（2026-11-25 改动回归）
 *
 * 场景复刻生产 user 32 卡点：无项目 inspector 在 agent 首页。
 * 断言：
 *   H1 降级可见：语音记录卡显示且带「（需先加入项目）」后缀
 *   H2 点击拦截：点降级卡 → 弹 guideJoinProject 模态（标题「需要先加入项目」）
 *   H3 读类不受影响：查规范卡无后缀
 *   H4 有项目回归：注入有项目态后无后缀、无「加入项目」卡
 *
 * 跑法：node tests/ui/hero-degrade.test.cjs
 * 前置：node tests/ui/_inject_token.cjs no-project
 */
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

async function readHero() {
  const raw = await atomic('readHero', m => m.evaluate(() => {
    const stack = getCurrentPages()
    const page = stack[stack.length - 1]
    if (!page) return '{}'
    return JSON.stringify({
      noProject: !!page.data.noProject,
      visible: (page.data.visibleHeroActions || []).map(a => ({ text: a.text, command: a.command })),
    })
  }))
  return JSON.parse(raw || '{}')
}

function report(id, pass, detail) {
  log((pass ? 'PASS ' : 'FAIL ') + id + ' | ' + detail)
  if (!pass) process.exitCode = 1
}

async function main() {
  // ---- H1/H3：无项目态的降级展示 ----
  log('--- 无项目态（复刻 user 32）---')
  const hero = await readHero()
  const voice = hero.visible.find(a => a.command === '工作记录')
  const kb = hero.visible.find(a => a.command === '知识库')
  const join = hero.visible.find(a => a.command === 'join-project')
  report('H1-语音卡降级可见带后缀',
    !!voice && voice.text.includes('（需先加入项目）'),
    'text=' + (voice ? voice.text : 'MISSING'))
  report('H3-查规范卡无后缀',
    !!kb && !kb.text.includes('需先加入项目'),
    'text=' + (kb ? kb.text : 'MISSING'))
  report('H1b-加入项目卡在',
    !!join, 'text=' + (join ? join.text : 'MISSING'))

  // ---- H2：点降级卡弹 guideJoinProject 模态 ----
  // wx.showModal 无法直接读，但可以在页面上 hook：提前包一层记录调用
  const modal = await atomic('tapVoiceCard', m => m.evaluate(() => {
    return new Promise(resolve => {
      const stack = getCurrentPages()
      const page = stack[stack.length - 1]
      // hook showModal 记录参数（一次性的，验证后还原）
      const orig = wx.showModal
      let captured = null
      wx.showModal = (opts) => {
        captured = { title: opts.title, content: opts.content, confirmText: opts.confirmText }
        wx.showModal = orig
        // 不真弹、直接调 success(false) 走取消路径，避免卡在模态上
        if (typeof opts.success === 'function') opts.success({ confirm: false, cancel: true })
        return
      }
      // 模拟点击语音卡（handleActionTap → '工作记录' → startVoiceWorkFromChat）
      try {
        page.handleActionTap({ currentTarget: { dataset: { command: '工作记录' } } })
      } catch (e) {
        captured = { error: String(e) }
      }
      setTimeout(() => {
        if (!captured) { wx.showModal = orig; resolve(JSON.stringify({ captured: null })) }
        else resolve(JSON.stringify(captured))
      }, 800)
    })
  }), 15000)
  const md = JSON.parse(modal || '{}')
  report('H2-点击降级卡弹加入项目引导',
    md.title === '需要先加入项目' && /语音记录会生成现场工作记录/.test(md.content || ''),
    'modal=' + JSON.stringify(md).slice(0, 120))
  report('H2b-模态有一键直达按钮',
    md.confirmText === '去加入项目',
    'confirmText=' + md.confirmText)

  // ---- H4：有项目态回归 ----
  log('--- 有项目态回归 ---')
  await atomic('reinject', m => m.evaluate(() => {
    const apiBaseUrl = 'https://www.baize.gdces.cn/api'
    const scope = encodeURIComponent(apiBaseUrl)
    const info = wx.getStorageSync(`gdjl_${scope}_user_info`) || {}
    info.project_id = 15
    info.project_name = '沙盛四路等四条道路工程'
    wx.setStorageSync(`gdjl_${scope}_user_info`, info)
    wx.reLaunch({ url: '/pages/agent/index' })
    return 'OK'
  }))
  await sleep(5000)
  const hero2 = await readHero()
  const voice2 = hero2.visible.find(a => a.command === '工作记录')
  const join2 = hero2.visible.find(a => a.command === 'join-project')
  report('H4-有项目语音卡无后缀',
    !!voice2 && !voice2.text.includes('需先加入项目') && !hero2.noProject,
    'text=' + (voice2 ? voice2.text : 'MISSING') + ' noProject=' + hero2.noProject)
  report('H4b-有项目无加入项目卡',
    !join2, 'join=' + (join2 ? join2.text : 'absent'))
}

main().then(() => { setTimeout(() => process.exit(process.exitCode || 0), 300) })
  .catch(e => { console.error('[ui] FATAL:', e.message); process.exit(1) })
setTimeout(() => { console.error('[ui] GLOBAL TIMEOUT'); process.exit(2) }, 120000)

