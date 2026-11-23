const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')

const root = path.join(__dirname, '../miniprogram')

function allWxss() {
  const out = []
  const walk = (dir) => {
    for (const name of fs.readdirSync(dir)) {
      const p = path.join(dir, name)
      const st = fs.statSync(p)
      if (st.isDirectory()) {
        if (name === 'node_modules') continue
        walk(p)
      } else if (name.endsWith('.wxss')) {
        out.push(fs.readFileSync(p, 'utf8'))
      }
    }
  }
  walk(root)
  return out.join('\n')
}

const css = allWxss()

test('字号只能取设计阶梯值（禁止手调中间值）', () => {
  // 阶梯来源：app.wxss 的 --fs-* 令牌（20/22/24/26/28/32/40/48/56）
  // 例外：76rpx 是引导页 emoji 图标字号（.slide-icon），属于图形尺寸不是文字
  const ALLOWED = new Set([20, 22, 24, 26, 28, 32, 40, 48, 56, 76])
  const bad = []
  for (const m of css.matchAll(/font-size\s*:\s*(\d+(?:\.\d+)?)rpx/g)) {
    const v = parseFloat(m[1])
    if (!ALLOWED.has(v)) bad.push(v)
  }
  const uniq = [...new Set(bad)]
  assert.deepEqual(uniq, [], `出现了阶梯外的字号：${uniq.join(', ')}rpx`)
})

test('字重只能取 400/500/600/700', () => {
  const ALLOWED = new Set([400, 500, 600, 700])
  const bad = []
  for (const m of css.matchAll(/font-weight\s*:\s*(\d+)/g)) {
    const v = parseInt(m[1], 10)
    if (!ALLOWED.has(v)) bad.push(v)
  }
  const uniq = [...new Set(bad)]
  assert.deepEqual(uniq, [], `出现了非标准字重：${uniq.join(', ')}`)
})

test('相对行高只能取 1 / 1.35 / 1.5 / 1.65', () => {
  // 1 用于图标字形居中；px 行高是控件固定高度，不在本约束内
  const ALLOWED = new Set(['1', '1.35', '1.5', '1.65'])
  const bad = []
  for (const m of css.matchAll(/line-height\s*:\s*(\d*\.\d+|\d+)(?=[;\s}])/g)) {
    const raw = m[1]
    if (!raw.includes('.') && raw !== '1') continue
    if (!ALLOWED.has(raw)) bad.push(raw)
  }
  const uniq = [...new Set(bad)]
  assert.deepEqual(uniq, [], `出现了阶梯外的行高：${uniq.join(', ')}`)
})

test('字体栈覆盖各厂商中文字体，不只 iOS', () => {
  const appWxss = fs.readFileSync(path.join(root, 'app.wxss'), 'utf8')
  assert.match(appWxss, /PingFang SC/)
  assert.match(appWxss, /HarmonyOS Sans SC/)
  assert.match(appWxss, /MiSans/)
  assert.match(appWxss, /Noto Sans CJK SC/)
  // text/button/input 不保证继承 page，必须显式链一遍
  assert.match(appWxss, /view,\s*\ntext,\s*\nbutton/)
})

test('组件尺寸令牌存在且按钮高度收敛为三档', () => {
  const appWxss = fs.readFileSync(path.join(root, 'app.wxss'), 'utf8')
  assert.match(appWxss, /--h-control: 88rpx;/)
  assert.match(appWxss, /--h-control-lg: 104rpx;/)
  assert.match(appWxss, /--h-row: 96rpx;/)
  assert.match(appWxss, /--icon-md: 44rpx;/)

  // 按钮高度只允许 64（小图标钮）/ 88（常规）/ 104（主按钮）
  const ALLOWED = new Set([56, 64, 88, 104])
  const bad = []
  for (const m of css.matchAll(/\.([a-z0-9-]*(?:btn|button|action)[a-z0-9-]*)\s*\{[^}]*height:\s*(\d+)rpx/g)) {
    const v = parseInt(m[2], 10)
    if (!ALLOWED.has(v)) bad.push(`${m[1]}=${v}`)
  }
  assert.deepEqual([...new Set(bad)], [], `按钮高度超出三档：${bad.join(', ')}`)
})
