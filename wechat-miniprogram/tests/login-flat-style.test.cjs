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
test('思考进度卡改平面：去掉左侧绿色竖条，改用脉冲状态点', () => {
  const agentWxss = fs.readFileSync(path.join(root, 'pages/agent/index.wxss'), 'utf8')
  const agentWxml = fs.readFileSync(path.join(root, 'pages/agent/index.wxml'), 'utf8')

  // 原来的 border-left: 6rpx solid 绿色竖条是"丑"的直接来源，必须消失
  const progressRule = agentWxss.match(/^\.agent-progress \{[^}]*\}/m)[0]
  assert.doesNotMatch(progressRule, /border-left:/)
  assert.match(progressRule, /border: 1rpx solid rgba\(17,19,26,0\.08\)/)
  assert.match(progressRule, /background: #ffffff/)
  // 旧配色体系（医疗绿）整体退出
  assert.doesNotMatch(agentWxss, /#13795b/)
  assert.doesNotMatch(agentWxss, /rgba\(19,121,91/)

  // 「正在思考」不再用动态指示（呼吸灯被视为干扰），状态只由文字表达
  assert.doesNotMatch(agentWxss, /progressPulse/)
  assert.doesNotMatch(agentWxss, /agent-progress-dot/)
  assert.doesNotMatch(agentWxml, /agent-progress-dot/)
  assert.match(agentWxml, /class="agent-progress-title">\{\{item\.progressTerminal \? '思考完成' : '思考中'\}\}/)
  // 右侧用状态文字 + 折叠箭头，不再有「收起/展开」两行文案
  assert.match(agentWxml, /class="agent-progress-chevron \{\{item\.progressExpanded \? 'up' : ''\}\}"/)
  assert.match(agentWxss, /\.agent-progress-chevron\.up \{ transform: rotate\(-90deg\); \}/)
  assert.doesNotMatch(agentWxml, /agent-progress-toggle/)
})
test('模型目录不重复：同一个模型不能在选择器里出现两次', () => {
  const catalog = fs.readFileSync(path.join(root, 'services/modelCatalog.ts'), 'utf8')
  const ids = [...catalog.matchAll(/^\s{2}\{\s*$[\s\S]*?id: '([^']+)'/gm)].map(x => x[1])
  // 更稳妥：直接抓所有 id 字段
  const allIds = [...catalog.matchAll(/id:\s*'([^']+)'/g)].map(x => x[1])
  assert.ok(allIds.length >= 3, `目录只有 ${allIds.length} 条`)
  const dupes = allIds.filter((id, i) => allIds.indexOf(id) !== i)
  assert.deepEqual(dupes, [], `目录存在重复 id：${dupes.join(', ')}`)

  // 选择器一律用去重后的目录（防止以后重复维护又漏进来）
  assert.match(catalog, /export const MODEL_CATALOG_UNIQUE/)
  const agent = fs.readFileSync(path.join(root, 'pages/agent/index.ts'), 'utf8')
  assert.match(agent, /import \{ MODEL_CATALOG_UNIQUE \}/)
  assert.match(agent, /const CHAT_MODELS = MODEL_CATALOG_UNIQUE/)
  assert.doesNotMatch(agent, /const CHAT_MODELS = MODEL_CATALOG\b/)
})
test('@ 面板搜索不会永久卡在「搜索中」：loading 由在飞计数结算 + 看门狗兜底', () => {
  const agent = fs.readFileSync(path.join(root, 'pages/agent/index.ts'), 'utf8')
  const wxml = fs.readFileSync(path.join(root, 'pages/agent/index.wxml'), 'utf8')

  // 旧实现：finally 里 if (seq === atSearchSeq) 才复位 —— 一旦有更新的请求插入、
  // 而最后一次回调丢失，loading 会永久停在 true，面板永远显示「搜索中…」
  assert.doesNotMatch(agent, /if \(seq === this\.atSearchSeq\) this\.setData\(\{ atLoading: false \}\)/)
  // 新实现：按在飞请求计数结算
  assert.match(agent, /this\.atInFlight \+= 1/)
  assert.match(agent, /this\.atInFlight = Math\.max\(0, this\.atInFlight - 1\)/)
  assert.match(agent, /if \(this\.atInFlight === 0\) \{/)
  // 看门狗：请求回调丢失也要把 loading 放下来
  assert.match(agent, /atLoadingTimer/)
  assert.match(agent, /搜索超时，请重试/)
  // 失败不再被空 catch 吞掉：要打日志 + 界面上给提示
  assert.doesNotMatch(agent, /\} catch \{\s*\n\s*\/\/ 搜索失败保持面板可用/)
  assert.match(agent, /console\.warn\('\[kb\/search\] 失败:',/)
  assert.match(wxml, /class="at-empty at-empty-error">\{\{atError\}\}/)
})
test('输入框内 @ 引用渲染成行内胶囊：editor 为主、textarea 兜底', () => {
  const agent = fs.readFileSync(path.join(root, 'pages/agent/index.ts'), 'utf8')
  const wxml = fs.readFileSync(path.join(root, 'pages/agent/index.wxml'), 'utf8')

  // textarea 只能放纯文本，行内样式必须靠 <editor>
  assert.match(wxml, /<editor wx:if="\{\{useEditor && !recording\}\}"[\s\S]{0,120}id="chat-editor"/)
  // 低版本基础库回退到 textarea（原有整条链路不动）
  assert.match(wxml, /<textarea wx:elif="\{\{!recording\}\}"/)
  assert.match(agent, /supportsEditor\(\): boolean \{/)
  assert.match(agent, /this\.setData\(\{ useEditor: this\.supportsEditor\(\) \}\)/)

  // editor 事件：ready 拿上下文、input 回传 html/text
  assert.match(agent, /onEditorReady\(\) \{/)
  assert.match(agent, /\.select\('#chat-editor'\)/)
  assert.match(agent, /onEditorInput\(event: \{ detail: \{ html\?: string; text\?: string \} \}\)/)

  // 胶囊用内联样式，不依赖页面 wxss 的类（editor 内部不一定吃到）
  assert.match(agent, /function pill|const pill = \(raw: string\) =>/)
  assert.match(agent, /background:rgba\(10,109,255,0\.12\);border-radius:999px/)
  // 胶囊里保留完整 token，mentions 才能从纯文本可靠恢复（属性可能被运行时清洗）
  assert.match(agent, /@\\\[资料库\(\?:检索\)\?\\s\[\^\\\]\]\*\\\]\/g/)
  // 追加引用后要把胶囊同步进 editor
  assert.match(agent, /void this\.syncEditorWithInput\(\)/)
})