const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')

const root = path.join(__dirname, '../miniprogram')
const loginWxml = fs.readFileSync(path.join(root, 'pages/login/index.wxml'), 'utf8')
const loginWxss = fs.readFileSync(path.join(root, 'pages/login/index.wxss'), 'utf8')
const loginJson = fs.readFileSync(path.join(root, 'pages/login/index.json'), 'utf8')
const appWxss = fs.readFileSync(path.join(root, 'app.wxss'), 'utf8')

test('登录页复刻 jl 的平面形态：光晕 + 横版 logo + 大字标题', () => {
  // jl 的观感来自这四件：背景光晕、横版品牌 logo、大标题、极简结构
  assert.match(loginWxml, /class="flat-glow flat-glow-a"/)
  assert.match(loginWxml, /class="flat-glow flat-glow-b"/)
  assert.match(loginWxml, /<image class="logo" src="\/assets\/brand-logo\.png" mode="aspectFit" \/>/)
  assert.match(loginWxss, /\.logo \{[^}]*width: 420rpx;[^}]*height: 56rpx/)
  assert.match(loginWxss, /\.hero-title \{[^}]*font-size: 52rpx;/)
  // 底色与文字色对齐 jl（#f7f9fc / #11131a / #8a94a6）
  assert.match(loginWxss, /background: #f7f9fc;/)
  assert.match(loginWxss, /color: #11131a;/)
  assert.match(loginWxss, /color: #8a94a6;/)
})

test('登录页不再套玻璃层：无 card / glass-nav-bar', () => {
  // 原来的观感问题就是三层玻璃叠加，登录页必须彻底脱离
  assert.doesNotMatch(loginWxml, /class="card"/)
  assert.doesNotMatch(loginWxml, /glass-nav-bar/)
  assert.doesNotMatch(loginJson, /glass-nav-bar/)
  // 也不再引用全局玻璃按钮/输入框
  assert.doesNotMatch(loginWxml, /class="primary action"/)
  assert.doesNotMatch(loginWxml, /class="field"/)
  // 改用平面类
  assert.match(loginWxml, /class="btn-flat"/)
  assert.match(loginWxml, /class="field-flat bind-field"/)
})

test('平面体系在 app.wxss 里成体系：令牌 + 卡片 + 按钮 + 输入 + 行', () => {
  assert.match(appWxss, /--flat-bg: #f7f9fc;/)
  assert.match(appWxss, /--flat-ink: #11131a;/)
  assert.match(appWxss, /--flat-line: rgba\(17, 19, 26, 0\.08\)/)
  assert.match(appWxss, /\.page-flat \{/)
  assert.match(appWxss, /\.flat-glow-a \{/)
  assert.match(appWxss, /\.card-flat \{/)
  assert.match(appWxss, /\.btn-flat \{/)
  assert.match(appWxss, /\.btn-flat-ghost \{/)
  assert.match(appWxss, /\.field-flat \{/)
  assert.match(appWxss, /\.row-flat \{/)
  // 平面按钮的关键参数来自 jl 的 .wxbtn
  assert.match(appWxss, /\.btn-flat \{[^}]*height: 104rpx;[^}]*border-radius: 52rpx;/)
})

test('业务两态保留：微信登录 / 绑定已有账号', () => {
  // 只复刻视觉，不改 jl 没有的绑定流程
  assert.match(loginWxml, /wx:if="\{\{!needBind\}\}"/)
  assert.match(loginWxml, /bindtap="login"/)
  assert.match(loginWxml, /wx:else/)
  assert.match(loginWxml, /bindtap="bind"/)
  assert.match(loginWxml, /bindtap="goRegister"/)
  assert.match(loginWxml, /bindtap="goForgot"/)
  // 绑定态字段多，需要收紧底部留白
  assert.match(loginWxml, /class="page-flat login-page \{\{needBind \? 'bind-mode' : ''\}\}"/)
})

test('图标资产已搬入：横版 logo 与功能图标目录', () => {
  assert.ok(fs.existsSync(path.join(root, 'assets/brand-logo.png')), 'brand-logo.png 缺失')
  assert.ok(fs.existsSync(path.join(root, 'assets/brand-mark.png')), 'brand-mark.png 缺失')
  const icons = fs.readdirSync(path.join(root, 'assets/icons'))
  assert.ok(icons.length >= 80, `icons 目录只有 ${icons.length} 个文件`)
  // 四色变体是这套图标的核心约定
  for (const variant of ['blue', 'dark', 'gray', 'white']) {
    assert.ok(icons.includes(`mic-${variant}.png`), `缺少 mic-${variant}.png`)
  }
  assert.ok(fs.existsSync(path.join(root, 'assets/icons/trace/trace-doc.png')), 'trace 图标缺失')
})

test('工作台 agent 页已去掉玻璃叠层：核心元素全部平面化', () => {
  const agentWxss = fs.readFileSync(path.join(root, 'pages/agent/index.wxss'), 'utf8')
  // 改造前：13 个选择器带 backdrop-filter、27 处 inset 内高光 —— 这正是"层层叠叠"
  const blurCount = (agentWxss.match(/backdrop-filter: blur/g) || []).length
  assert.equal(blurCount, 0, `仍有 ${blurCount} 处 backdrop-filter: blur`)

  for (const sel of ['nav-bar', 'input-card', 'input-bar', 'hero-pill', 'bubble']) {
    const rule = agentWxss.match(new RegExp(`^\\.${sel} \\{[^}]*\\}`, 'm'))
    assert.ok(rule, `${sel} 规则缺失`)
    assert.doesNotMatch(rule[0], /backdrop-filter: blur/, `${sel} 仍是玻璃`)
  }
  // 输入卡/气泡/hero 改为「一层白 + 一根线」
  assert.match(agentWxss, /^\.input-card \{[^}]*background: #ffffff;[^}]*border: 1rpx solid rgba\(17,19,26,0\.08\)/m)
  assert.match(agentWxss, /^\.input-bar \{[^}]*background: #f7f9fc;/m)
  // 页面底色对齐 jl 的 #f7f9fc（原来是纯白，平面卡片没有层次）
  assert.match(agentWxss, /^\.page \{[^}]*background: #f7f9fc;/m)
})