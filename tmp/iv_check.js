/**
 * 图片预览组件专项验证（2026-11-13）
 * 目标：工作记录详情照片点开 → 出现「1 / N」计数与 ‹ › 按钮 → 点「下一张」能推进 → 关闭成功。
 * 用法：NODE_PATH=wechat-miniprogram/node_modules node iv_check.js --port=9431
 */
const automator = require('miniprogram-automator')
// 新版开发者工具 Tool.getInfo 不再返回 SDKVersion，automator 的 checkVersion 会崩 → 跳过校验
try {
  const mod = require('miniprogram-automator/out/MiniProgram')
  const MPClass = mod && (mod.default || mod)
  if (MPClass && MPClass.prototype) MPClass.prototype.checkVersion = async function () {}
} catch (e) { console.warn('patch checkVersion failed', e.message) }

const PORT = (process.argv.find((a) => a.startsWith('--port=')) || '').split('=')[1] || '9431'
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
const SHOT = 'D:/rokid/rokid/tmp/iv_check'

;(async () => {
  const mp = await automator.connect({ wsEndpoint: `ws://127.0.0.1:${PORT}` })
  const logs = []
  mp.on('console', (msg) => {
    if (msg.type === 'error' || msg.type === 'warning') logs.push(`${msg.type}: ${msg.args.join(' ')}`)
  })

  // automator 的 reLaunch 在本环境不可用，用 evaluate 驱动跳转
  await mp.evaluate((u) => wx.reLaunch({ url: u }), '/pages/work/index')
  await sleep(2500)
  const page = await mp.currentPage()
  console.log('current route:', page.path)

  // 1) 构造 3 张假照片 → 点第 1 张进预览
  const opened = await mp.evaluate(() => {
    const p = getCurrentPages().pop()
    p.setData({
      detailPhotos: ['https://example.com/a.jpg', 'https://example.com/b.jpg', 'https://example.com/c.jpg'],
      showDetail: true,
    })
    p.previewDetailPhoto({ currentTarget: { dataset: { url: 'https://example.com/a.jpg' } } })
    return {
      pageVisible: p.data.imageViewerVisible,
      pageCount: (p.data.imageViewerUrls || []).length,
      pageCurrent: p.data.imageViewerCurrent,
    }
  })
  console.log('page state after tap photo:', JSON.stringify(opened))

  await sleep(1000)
  await mp.screenshot({ path: `${SHOT}_1_open.png` })

  // 2) 组件内部：open / activeIndex / 计数
  const compState = await mp.evaluate(() => {
    const p = getCurrentPages().pop()
    const c = p.selectComponent('#iv-viewer')
    if (!c) return { found: false }
    return { found: true, open: c.data.open, activeIndex: c.data.activeIndex, urls: (c.data.urls || []).length }
  })
  console.log('component state:', JSON.stringify(compState))

  // 3) 点「下一张」按钮两次
  const afterNext = await mp.evaluate(() => {
    const c = getCurrentPages().pop().selectComponent('#iv-viewer')
    c.next()
    return { activeIndex: c.data.activeIndex, open: c.data.open }
  })
  console.log('after next #1:', JSON.stringify(afterNext))
  await sleep(600)
  await mp.screenshot({ path: `${SHOT}_2_next.png` })

  const afterNext2 = await mp.evaluate(() => {
    const c = getCurrentPages().pop().selectComponent('#iv-viewer')
    c.next()
    c.next() // 循环：3 → 1
    return { activeIndex: c.data.activeIndex }
  })
  console.log('after next #2,#3 (circular):', JSON.stringify(afterNext2))

  // 4) 关闭：组件 close() 触发 bind:close → 页面置 false
  const afterClose = await mp.evaluate(() => {
    const p = getCurrentPages().pop()
    p.selectComponent('#iv-viewer').close()
    return { pageVisible: p.data.imageViewerVisible, compOpen: p.selectComponent('#iv-viewer').data.open }
  })
  console.log('after close:', JSON.stringify(afterClose))
  await sleep(400)
  await mp.screenshot({ path: `${SHOT}_3_closed.png` })

  const ok =
    opened.pageVisible === true && opened.pageCount === 3 && opened.pageCurrent === 0 &&
    compState.found && compState.open === true && compState.urls === 3 &&
    afterNext.activeIndex === 1 && afterNext2.activeIndex === 1 && afterClose.pageVisible === false
  console.log(ok ? 'RESULT: PASS' : 'RESULT: FAIL')
  console.log('console errors/warns:', logs.length ? logs : '(none)')
  await mp.disconnect()
  process.exit(ok ? 0 : 2)
})().catch((e) => {
  console.error('FATAL', e && e.message, e && e.stack)
  process.exit(1)
})
