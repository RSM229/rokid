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
  assert.match(loginWxss, /\.hero-title \{[^}]*font-size: 48rpx;/)
  // 底色与文字色对齐 jl（#f7f9fc / #11131a / #8a94a6）
  assert.match(loginWxss, /background: #f7f9fc;|background: #f2f4f7;/)
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
  assert.match(appWxss, /--flat-bg: #f2f4f7;/)
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
  // 例外：导航栏材质是 iOS 路线有意保留的（导航栏是"材料"不是色块）。
  // 材质只铺在 .nav-bar-fill（标题那一条）；.nav-bar 外层必须透明，
  // 否则状态栏会被铺成一条没有内容的白带。
  const navRule = (agentWxss.match(/^\.nav-bar \{[^}]*\}/m) || [''])[0]
  const fillRule = (agentWxss.match(/^\.nav-bar-fill \{[^}]*\}/m) || [''])[0]
  // 除导航栏材质外的模糊数应为 0
  const totalBlur = (agentWxss.match(/(?<!-webkit-)backdrop-filter: blur/g) || []).length
  const navBlur = (fillRule.match(/(?<!-webkit-)backdrop-filter: blur/g) || []).length + (navRule.match(/(?<!-webkit-)backdrop-filter: blur/g) || []).length
  assert.equal(totalBlur - navBlur, 0, `除导航栏外仍有 ${totalBlur - navBlur} 处 backdrop-filter: blur`)
  // 材质在 fill 上：不透明纯白 + 一根细线（用户要求顶部统一白色，不要灰白条）
  assert.match(fillRule, /background: #ffffff/)
  assert.match(fillRule, /border-bottom: 1rpx solid rgba\(17, 19, 26, 0\.08\)/)
  assert.doesNotMatch(fillRule, /--deep-rgb/)
  // 不透明之后 blur 已无意义，不应再出现
  assert.doesNotMatch(fillRule, /backdrop-filter/)
  // 外层容器不能再有背景，否则状态栏又被铺成白带
  assert.doesNotMatch(navRule, /background:/)

  // nav-bar 不在此列：iOS 路线下它是「材质」（半透明 + 模糊 + 细线），不是平面卡片
  for (const sel of ['input-card', 'input-bar', 'hero-pill', 'bubble']) {
    const rule = agentWxss.match(new RegExp(`^\\.${sel} \\{[^}]*\\}`, 'm'))
    assert.ok(rule, `${sel} 规则缺失`)
    assert.doesNotMatch(rule[0], /backdrop-filter: blur/, `${sel} 仍是玻璃`)
  }
  // 输入卡：内层白卡悬浮（胶囊），外层容器不再铺底色
  assert.match(agentWxss, /^\.input-card \{[^}]*background: #ffffff;/m)
  // 外层 input-bar 必须透明：原来是「浅灰条 + 白卡」两层卡片叠在一起（用户反馈像卡片套卡片）
  assert.match(agentWxss, /^\.input-bar \{[^}]*position: relative;/m)
  const barRule = (agentWxss.match(/^\.input-bar \{[^}]*\}/m) || [''])[0]
  assert.doesNotMatch(barRule, /background:/, 'input-bar 不应再有底色')
  assert.doesNotMatch(barRule, /border-top:/, 'input-bar 不应再有上边框')
  // 悬浮感靠投影表达
  assert.match(agentWxss, /^\.input-card \{[^}]*box-shadow: 0 8rpx 28rpx/m)
  // 页面底色统一为 #f2f4f7（原来是纯白，平面卡片没有层次）
  assert.match(agentWxss, /^\.page \{[^}]*background: #f2f4f7;/m)
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
test('输入框固定用 textarea（<editor> 的编辑 API 在真机不可控）', () => {
  const agent = fs.readFileSync(path.join(root, 'pages/agent/index.ts'), 'utf8')
  const wxml = fs.readFileSync(path.join(root, 'pages/agent/index.wxml'), 'utf8')

  // 结构保留：基础库够新时 editor 分支仍在（组件能力稳定后可再评估），
  // 但页面不再启用它 —— 2026-11-23 起固定走 textarea。
  assert.match(wxml, /<editor wx:if="\{\{useEditor && !recording\}\}"/)
  assert.match(wxml, /<textarea wx:elif="\{\{!recording\}\}"/)
  assert.match(agent, /supportsEditor\(\): boolean \{/)
  assert.match(agent, /this\.setData\(\{ useEditor: false \}\)/)

  // 停用理由必须写在代码里，避免以后有人"顺手"改回去再踩一遍：
  // format 后光标跳到开头并重新染色、setContents 后失去输入能力、deleteText index 不稳定
  assert.match(agent, /format\(\) 之后光标会跳到开头/)
  assert.match(agent, /setContents\(\) 重建内容后编辑器失去输入能力/)

  // 选完引用要把焦点交回输入框，用户才能接着打字
  assert.match(agent, /this\.focusDraftInput\(\)/)
})
test('config.ts 不用可选链调用，避免关掉 ES5 转译后真机解析失败', () => {
  const cfg = fs.readFileSync(path.join(root, 'config.ts'), 'utf8')
  // 只在「非注释行」上断言：注释里会提到旧写法，不能误判
  const code = cfg.split('\n').filter(l => !/^\s*\/\//.test(l)).join('\n')
  // 之前是 wx.getExtConfigSync?.()：可选链是 ES2020，
  // 项目 es6/enhance 均为 false 时不会转译，真机预览直接 SyntaxError
  assert.doesNotMatch(code, /wx\.getExtConfigSync\?\.\(/)
  assert.match(cfg, /typeof wx\.getExtConfigSync !== 'function'/)
})

test('引用整块删除：token 被删残时残片整段清掉并撤销引用', () => {
  const agent = fs.readFileSync(path.join(root, 'pages/agent/index.ts'), 'utf8')
  // <editor> 没有按键事件，只能在 bindinput 之后修补
  assert.match(agent, /stripBrokenMentionFragments\(text: string\)/)
  // 判据：mention 的 token 不再完整出现 → 视为被打残
  assert.match(agent, /const full = new Set\(text\.match\(MENTION_TOKEN_RE\) \|\| \[\]\)/)
  assert.match(agent, /this\.data\.mentions\.filter\(m => !full\.has\(m\.token\)\)/)
  // 只清「没有闭合 ]」的残片，完整 token 不会被误删
  assert.match(agent, /@\\\[资料库\(\?:检索\)\?\[\^\\\]\]\*\?\(\?=\\s\|\$\)/)
  // 清理后要撤销对应引用，否则界面没引用但后端仍在限定范围检索
  assert.match(agent, /this\.refreshMentions\(this\.data\.mentions\.filter\(m => !repaired\.removed\.includes\(m\)\)\)/)
})

test('大标题滚动收起：wxs 渲染层计算 + 只在整数像素变化时 setData', () => {
  const wxs = fs.readFileSync(path.join(root, 'utils/large-title.wxs'), 'utf8')
  const compWxml = fs.readFileSync(path.join(root, 'components/glass-nav-bar/index.wxml'), 'utf8')
  const compTs = fs.readFileSync(path.join(root, 'components/glass-nav-bar/index.ts'), 'utf8')
  const mineTs = fs.readFileSync(path.join(root, 'pages/mine/index.ts'), 'utf8')

  // 位移/透明度必须在渲染层算（wxs），不能走 setData —— 否则滚动掉帧
  assert.match(wxs, /function largeTitleStyle/)
  assert.match(wxs, /function inlineTitleStyle/)
  assert.match(wxs, /function navDividerStyle/)
  assert.match(compWxml, /<wxs src="..\/..\/utils\/large-title.wxs" module="lt" \/>/)
  // 组件要暴露 largeTitle / scrollTop
  assert.match(compTs, /largeTitle: \{ type: Boolean, value: false \}/)
  assert.match(compTs, /scrollTop: \{ type: Number, value: 0 \}/)
  // 页面只在整数像素变化时 setData
  assert.match(mineTs, /onPageScroll\(e: \{ scrollTop: number \}\)/)
  assert.match(mineTs, /if \(top === this\.data\.scrollTop\) return/)
  // spacer 只占导航栏高度；大标题在正常流里自己占位（之前写 +96 导致中间一大块空白）
  assert.match(compWxml, /style="height: \{\{totalHeight\}\}px;"/)
  assert.doesNotMatch(compWxml, /totalHeight \+ 96/)
  // spacer 必须排在导航栏之后、大标题之前，否则大标题会顶到页面顶部
  const spacerIdx = compWxml.indexOf('height: {{totalHeight}}px;"></view>')
  const largeIdx = compWxml.indexOf('class="gnb-large-wrap"')
  assert.ok(spacerIdx > 0 && largeIdx > spacerIdx, 'spacer 应排在大标题之前')
  // 材质条只铺标题那一条（HIG：Reduce the use of toolbar backgrounds）
  assert.match(compWxml, /class="gnb-fill"/)
})