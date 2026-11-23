const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')

const root = path.join(__dirname, '../miniprogram/pages/agent')
const page = fs.readFileSync(path.join(root, 'index.ts'), 'utf8')
const template = fs.readFileSync(path.join(root, 'index.wxml'), 'utf8')
const styles = fs.readFileSync(path.join(root, 'index.wxss'), 'utf8')
const globalStyles = fs.readFileSync(path.join(__dirname, '../miniprogram/app.wxss'), 'utf8')

test('@ 面板只保留资料库一个入口，三类旧候选已移除', () => {
  // 旧实现有三个 tab（快捷指令/成员/业务引用），重构后单一入口
  assert.doesNotMatch(template, /at-tab\b/)
  assert.doesNotMatch(template, /switchAtTab/)
  assert.doesNotMatch(template, /快捷指令/)
  assert.doesNotMatch(template, /业务引用/)
  assert.doesNotMatch(page, /AT_QUICK_ITEMS/)
  assert.doesNotMatch(page, /pickAtQuick|pickAtMember|pickAtBiz/)
  assert.doesNotMatch(page, /loadAtData/)
  // 新入口：标题 + 搜索框
  assert.match(template, /class="at-panel-title">引用资料库/)
  assert.match(template, /bindinput="onAtSearch"/)
})

test('搜索走服务端 /kb/search，带分页与竞态丢弃', () => {
  assert.match(page, /request<KbSearchResponse>\('\/kb\/search'/)
  assert.match(page, /data: \{ q: query, offset, limit: 20, project_id: this\.data\.currentProjectId \|\| undefined \}/)
  // 竞态：过期响应必须丢弃，否则慢请求会覆盖新结果
  assert.match(page, /const seq = \+\+this\.atSearchSeq/)
  assert.match(page, /if \(seq !== this\.atSearchSeq\) return/)
  assert.match(page, /loadMoreAt\(\)/)
  assert.match(template, /catchtap="loadMoreAt"/)
})

test('多选：已选引用成 chips，点选可反选，撤销同步抹掉 token', () => {
  assert.match(page, /mentions: \[\] as AtMention\[\]/)
  assert.match(page, /interface AtMention \{/)
  assert.match(template, /class="at-selected-item"[\s\S]{0,120}catchtap="removeMention"/)
  assert.match(page, /removeMentionByKey\(key: string\)/)
  // 反选：再次点击同一项即取消
  assert.match(page, /const already = this\.data\.mentions\.some\(m => m\.key === item\.key\)/)
  // 撤销时把输入框里的 token 一并删掉，避免「已撤销但仍显示」
  assert.match(page, /this\.data\.input\.split\(target\.token\)\.join\(''\)/)
})

test('文件夹=整棵子树、文档=单份：token 与 mentions 结构对齐后端契约', () => {
  assert.match(page, /\{ key: item\.key, type: 'kb_folder', kb_id: item\.kbId, folder_id: item\.id/)
  assert.match(page, /\{ key: item\.key, type: 'kb_doc', kb_id: item\.kbId, doc_id: item\.id/)
  // token 只放名字（完整路径由已选区的第二行承担；语义靠 folder_id/doc_id，不靠文本）
  assert.match(page, /const token = `@\[资料库 \$\{item\.name\}\]`/)
  assert.doesNotMatch(page, /token = item\.kind === 'folder'/)
  // 发送时带结构化引用
  assert.match(page, /buildChatMentions\(\): Array<Record<string, unknown>>/)
  assert.match(page, /mentions: this\.buildChatMentions\(\)/)
})

test('用户手工删掉 token 时同步撤销引用', () => {
  assert.match(page, /syncMentionsWithInput\(input: string\)/)
  // 输入框改为只显示名字后，必须同时按 name 判断（兼容老草稿里的 token 文本）
  assert.match(page, /const kept = this\.data\.mentions\.filter\(m => input\.includes\(m\.token\) \|\| input\.includes\(m\.name\)\)/)
  // onInput 里必须调用，否则删除 token 后模型仍按旧范围检索
  assert.match(page, /this\.syncMentionsWithInput\(input\)/)
})

test('打 @ 唤起面板：句中输入也要触发（不要求 @ 前有空格）', () => {
  // 旧实现是 /(^|\s)@…$/，只有行首或空格后的 @ 才触发；
  // 用户在句子里打「你好@」时完全没反应（点按钮能开，因为不走这条路径）。
  assert.match(page, /const m = \/@\(\[\^\\s@\\\[\\\]\]\*\)\$\/\.exec\(input\)/)
  assert.doesNotMatch(page, /\(\^\|\\s\)@\(\[\^\\s@/)
  // 仍要排除已插入的「@[资料库 …]」，否则会被当成触发词
  assert.match(page, /\[\^\\s@\\\[\\\]\]/)
  assert.match(page, /void this\.searchAtKb\(keyword\)/)
})

test('选中态有对应样式（已选 chip、打勾、加载更多）', () => {
  assert.match(styles, /\.at-selected-item \{/)
  assert.match(styles, /\.at-item\.picked \{/)
  assert.match(styles, /\.at-more \{/)
})

test('模糊词：搜索框关键字本身可作为引用，随消息以 kb_query 发送', () => {
  // 列表首位插入「按这个关键词检索」的合成项，只在首页插入（加载更多不重复）
  assert.match(page, /kind: 'query', key: `kb-query-\$\{keyword\}`/)
  assert.match(page, /if \(offset === 0 && keyword\)/)
  // 选中它 → kb_query mention + 可读 token（token 里必须带「资料库检索」标识，避免与文件夹 token 混淆）
  assert.match(page, /type: 'kb_query'[\s\S]{0,80}query: item\.name, token/)
  assert.match(page, /const token = `@\[资料库检索 \$\{item\.name\}\]`/)
  // 发给后端时 kb_query 只带 query 字段（没有 kb_id/folder_id，后端据此不限范围）
  assert.match(page, /if \(m\.type === 'kb_query'\) return \{ type: 'kb_query', query: m\.query \|\| m\.name \}/)
  // 渲染：🔍 而不是 📁/📄
  assert.match(template, /item\.kind === 'query' \? '按「' \+ item\.name \+ '」检索资料库'/)
  assert.match(template, /item\.type === 'kb_query' \? '检索：' \+ item\.name/)
})

test('气泡里引用渲染成胶囊，并支持 AI 回答的 [[n]] 来源角标', () => {
  // 分段在发送时刻算好并随消息存下（不靠渲染时正则解析文本）
  assert.match(page, /segments\?: Array<\{ kind: 'text' \| 'mention' \| 'cite'; value: string; token\?: string \}>/)
  assert.match(page, /function splitMentionSegments\(/)
  assert.match(page, /const MENTION_TOKEN_RE = \/@\\\[资料库\(\?:检索\)\?\\s\[\^\\\]\]\*\\\]\/g/)
  assert.match(page, /function withMentionSegments\(msg: Message\): Message/)
  // 两条消息入口都要带上分段：addMessage（多数路径）+ 主发送路径的 userMsg
  assert.match(page, /msg = withMentionSegments\(msg\)/)
  // 发送时把 mentions 快照挂到消息上：输入框只写名字，气泡要靠它按名字切片段
  assert.match(page, /const userMsg: Message = withMentionSegments\(\{/)
  assert.match(page, /mentions: \[\.\.\.this\.data\.mentions\]/)
  // 后端提示词要求用 [[编号]] 标注来源，前端必须切出来，否则会原样露出 [[1]]
  assert.match(page, /const CITE_MARK_RE = \/\\\[\\\[\(\\d\{1,3\}\)\\\]\\\]\/g/)
  assert.match(page, /kind: 'cite'/)
  // 渲染成「蓝色文字链接 + 来源角标」；引用只显示名字，shell @[ ] 已去掉
  assert.match(template, /class="bubble-mention" data-token="\{\{seg\.token\}\}"/)
  assert.match(template, /wx:elif="\{\{seg\.kind === 'cite'\}\}" class="bubble-cite"/)
  assert.match(template, /catchtap="onBubbleMentionTap"/)
  assert.match(styles, /\.bubble-mention \{/)
  assert.match(styles, /\.bubble-cite \{/)
  // 链接色与点击处理器
  assert.match(page, /onBubbleMentionTap\(event: WechatMiniprogram\.TouchEvent\)/)
  assert.match(styles, /color: var\(--link, #0a84ff\)/)
  // 老消息没有 segments 时必须回退纯文本，不能出现幽灵高亮
  assert.match(template, /<block wx:else>\s*<text wx:if="\{\{item\.text\}\}" class="bubble-text"/)
})

test('引用档案条：双行信息 + 超量折叠 + 范围语义提示', () => {
  assert.match(page, /mentionChips: \[\] as AtMention\[\]/)
  assert.match(page, /mentionOverflow: 0/)
  assert.match(page, /mentionsSummary: ''/)
  assert.match(page, /const chips = mentions\.slice\(0, 3\)/)
  assert.match(page, /const overflow = Math\.max\(0, mentions\.length - 3\)/)
  // 文件夹必须说清「整棵子树 + N 份」，否则用户以为只引用了一个目录名
  assert.match(page, /parts\.push\(`\$\{folders\.length\} 个文件夹（整棵子树）`\)/)
  assert.match(page, /共 \$\{total\} 份资料/)
  // 渲染
  assert.match(template, /class="at-selected-name"/)
  assert.match(template, /class="at-selected-sub">\{\{item\.type === 'kb_folder' \? \(item\.count \? '整棵子树/)
  assert.match(template, /class="at-selected-more"[^>]*>\+\{\{mentionOverflow\}\}/)
  assert.match(template, /class="at-scope-hint"[^>]*>\{\{mentionsSummary\}\}/)
  // 三处变更都要重算档案条，否则 UI 与实际引用不一致
  assert.match(page, /this\.refreshMentions\(mentions\)/)
  assert.match(page, /this\.refreshMentions\(kept\)/)
  assert.match(styles, /\.at-selected-sub \{/)
  assert.match(styles, /\.at-scope-hint \{/)
})
test('空查询有内容可看：browse 分三段渲染，不再给空面板', () => {
  assert.match(page, /if \(result\.mode === 'browse' && offset === 0\)/)
  // 「最近」单独存成横向快捷条，「常用」一段已移除（数据量小时与最近重叠，会造成重复勾）
  assert.match(page, /atRecent: \[\] as AtKbItem\[\]/)
  assert.match(page, /this\.setData\(\{ atRecent: recent, atAllItems: folders, atHasMore: false \}\)/)
  assert.doesNotMatch(page, /result\.popular/)
  assert.match(template, /<scroll-view class="at-recent" scroll-x/)
  assert.match(styles, /\.at-recent-item \{/)
})

test('本地联想：打字先本地出结果，服务端结果到达后覆盖', () => {
  assert.match(page, /atLocalIndex: new Map<string, AtKbItem>\(\)/)
  assert.match(page, /rememberLocalItems\(items: AtKbItem\[\]\)/)
  assert.match(page, /filterLocalItems\(query: string\): AtKbItem\[\]/)
  // onAtSearch 必须先本地立刻渲染（0ms 手感），再走防抖网络
  assert.match(page, /const local = this\.filterLocalItems\(raw\)/)
  assert.match(page, /if \(local\.length\) \{[\s\S]{0,160}atAllItems: local,[\s\S]{0,60}this\.applyCandidateFilter\(\)/)
  // 缓存要落 storage，进页面就能恢复
  assert.match(page, /wx\.setStorageSync\('gdjl_kb_local_index'/)
  assert.match(page, /const cached = wx\.getStorageSync\('gdjl_kb_local_index'\)/)
  // 上限保护：无限增长会拖慢匹配并吃内存
  assert.match(page, /if \(index\.size > 300\)/)
})

test('个性化埋点：发送时上报「真的用进对话」的引用', () => {
  assert.match(page, /reportMentionUsage\(\)/)
  assert.match(page, /request\('\/kb\/mention-stats', \{/)
  assert.match(page, /data: \{ items \},/)
  // 只在发送路径上报，不在点选时上报（点选可能只是浏览）
  assert.match(page, /this\.reportMentionUsage\(\)\s*\n\s*\/\/ 发送时收掉/)
  // kb_query 不进埋点（关键词不是稳定目标，后端 target_type 只收 folder/doc）
  assert.match(page, /filter\(m => m\.type === 'kb_folder' \|\| m\.type === 'kb_doc'\)/)
})
test('已选项从候选列表移除：同一对象不再同时出现在两处、带两个勾', () => {
  // 核心不变量：已选的条目只在顶部「已选」区出现，候选列表把它过滤掉
  assert.match(page, /withoutSelected\(items: AtKbItem\[\]\): AtKbItem\[\]/)
  assert.match(page, /const chosen = new Set\(this\.data\.mentions\.map\(m => m\.key\)\)/)
  assert.match(page, /return items\.filter\(item => item\.kind !== 'header' && !chosen\.has\(item\.key\)\)/)
  // 候选列表 = atAllItems 过滤后的结果，撤销后能回到列表
  assert.match(page, /applyCandidateFilter\(\) \{/)
  assert.match(page, /atItems: this\.withoutSelected\(this\.data\.atAllItems\)/)
  // 任何一次引用变更都要重算，否则会出现"选了却还在候选里"
  assert.match(page, /this\.refreshMentions\(mentions\)/)
  assert.match(page, /this\.applyCandidateFilter\(\)\s*\n\s*\},/)
  // 候选行不再显示勾（勾只保留在「最近」快捷条上表示状态）
  assert.doesNotMatch(template, /class="at-item-check"/)
})
test('选择模式有明确出口：完成按钮收起面板并把焦点交回输入框', () => {
  // 用户的操作是「先选资料，再写需求」，选完必须能直接打字，
  // 而不是「关面板 → 点输入框 → 才开始打字」三步。
  assert.match(page, /finishAtPanel\(\) \{/)
  assert.match(page, /this\.setData\(\{ showAtPanel: false, atQuery: '', atAllItems: \[\], atRecent: \[\], atError: '' \}\)/)
  // 复用已有的「先失焦再聚焦」写法：textarea 本来就是 true 时直接 setData true 不会重新聚焦
  assert.match(page, /this\.focusDraftInput\(\)/)
  assert.match(page, /focusDraftInput\(\) \{\s*\n\s*this\.setData\(\{ draftFocus: false \}\)/)
  // 顶部与底部各一个出口
  assert.match(template, /class="at-panel-done" catchtap="finishAtPanel">完成/)
  assert.match(template, /class="at-done-bar \{\{mentions\.length \? 'active' : ''\}\}" catchtap="finishAtPanel"/)
  // 零选择时按钮仍在（灰态），避免用户找不到怎么结束
  assert.match(template, /\{\{mentions\.length \? '完成（已选 ' \+ mentions\.length \+ ' 项）' : '完成'\}\}/)
  assert.match(styles, /\.at-done-bar\.active \{/)
})

test('面板不再常驻：高度收缩并与输入框留出间距，降低误触', () => {
  // 64vh 会贴到输入框顶边，手指点底部行时容易误触
  // 面板必须是「确定高度 + 裁切」：只给 max-height 时 scroll-view 会按内容撑开，
  // 溢出上沿盖住底部完成条和输入框（实测遮挡过）
  assert.match(styles, /\.at-panel \{[^}]*height: 52vh/)
  assert.match(styles, /\.at-panel \{[^}]*overflow: hidden/)
  assert.match(styles, /\.at-panel \{[^}]*bottom: calc\(360rpx \+ var\(--kb, 0px\) \+ env\(safe-area-inset-bottom\)\)/)
  // scroll-view 靠 height:0 + flex:1 取到「剩余空间的具体高度」才会滚动
  assert.match(styles, /\.at-list \{ height: 0; flex: 1; min-height: 0; \}/)
  // 固定区不参与压缩，空间全让给列表
  assert.match(styles, /\.at-panel-head \{[^}]*flex-shrink: 0/)
  assert.match(styles, /\.at-done-bar \{[^}]*flex-shrink: 0/)
  // 标题栏直接报数，用户随时知道选了几项
  assert.match(template, /引用资料库\{\{mentions\.length \? '（已选 ' \+ mentions\.length \+ ' 项）' : ''\}\}/)
  // 旧的 × 关闭入口让位给「完成」，避免两个语义相近的出口
  assert.doesNotMatch(template, /class="at-panel-close"/)
})
test('设计令牌层：骨架数值集中定义，页面按语义引用', () => {
  // 背景：agent 页 528 条规则里出现过 7 种圆角、8 种字号，视觉上"没有规律"。
  // 令牌层把圆角/间距/字号/阴影收敛成语义档位，页面按角色引用。
  assert.match(globalStyles, /--r-card: 24rpx;/)
  assert.match(globalStyles, /--r-bubble: 22rpx;/)
  assert.match(globalStyles, /--r-item: 16rpx;/)
  assert.match(globalStyles, /--r-pill: 999rpx;/)
  assert.match(globalStyles, /--gap-1: 8rpx;/)
  // 字号阶梯（2026-11-23 由 30 种手调值收敛为 9 档）
  assert.match(globalStyles, /--fs-mini: 20rpx;/)
  assert.match(globalStyles, /--fs-caption: 22rpx;/)
  assert.match(globalStyles, /--fs-body-sm: 24rpx;/)
  assert.match(globalStyles, /--fs-body: 26rpx;/)
  assert.match(globalStyles, /--fs-body-lg: 28rpx;/)
  assert.match(globalStyles, /--fs-title: 32rpx;/)
  assert.match(globalStyles, /--fs-title-lg: 40rpx;/)
  assert.match(globalStyles, /--fs-display: 48rpx;/)
  // 字重 / 行高 / 控件尺寸
  assert.match(globalStyles, /--fw-semibold: 600;/)
  assert.match(globalStyles, /--lh-body: 1\.5;/)
  assert.match(globalStyles, /--h-control-lg: 104rpx;/)
  // 跨平台字体栈：不能只写 iOS，否则安卓掉到默认 sans-serif
  assert.match(globalStyles, /HarmonyOS Sans SC/)
  assert.match(globalStyles, /MiSans/)
  // 页面侧必须真的在用，而不是只定义
  assert.match(styles, /border-radius: var\(--r-bubble, 22rpx\)/)
  assert.match(styles, /font-size: var\(--fs-body, 28rpx\)/)
  assert.match(styles, /\.at-done-bar \{[^}]*border-radius: var\(--r-card, 24rpx\)/)
})

test('气泡改为平面：纯白底 + 一根线，不再用玻璃渐变', () => {
  // 演进：0.54→0.30（轮廓看不见）→ 0.78→0.46（有边界但仍玻璃）→ 纯白平面（对齐 jl）
  const bubbleRule = styles.match(/^\.bubble \{[^}]*\}/m)[0]
  assert.match(bubbleRule, /background: #ffffff;/)
  assert.match(bubbleRule, /border: 1rpx solid rgba\(17,19,26,0\.08\)/)
  assert.match(bubbleRule, /box-shadow: 0 2rpx 10rpx rgba\(22,38,74,0\.05\)/)
  // 不得再出现 blur / inset 内高光：这正是"层层叠叠"的来源
  assert.doesNotMatch(bubbleRule, /backdrop-filter: blur/)
  assert.doesNotMatch(bubbleRule, /inset 0/)
})

test('完成按钮是胶囊而非整条填满，且保留大点击区', () => {
  assert.match(styles, /\.at-panel-done \{[^}]*border-radius: var\(--r-pill, 999rpx\)/)
  assert.match(styles, /\.at-done-bar \{[^}]*height: 96rpx/)
  assert.match(styles, /\.at-done-bar\.active \{[^}]*box-shadow/)
})
test('键盘弹起不遮挡输入区：监听高度 + 布局跟着抬 + 防二次顶起', () => {
  // 页面是 height:100vh 的固定布局，微信的 adjust-position 只顶输入框，
  // @ 面板是 fixed 相对视口定位，不受影响 → 键盘一弹就盖住，必须自己算。
  assert.match(page, /keyboardHeight: 0,/)
  assert.match(page, /wx\.onKeyboardHeightChange\(this\.keyboardHandler\)/)
  // 页面销毁要注销，否则回调打到已销毁实例
  assert.match(page, /wx\.offKeyboardHeightChange\(this\.keyboardHandler\)/)
  // 键盘高度以 CSS 变量注入页面根，供 .page 与 .at-panel 共用
  assert.match(template, /--kb: \{\{keyboardHeight\}\}px;/)
  // 整页不能吃 --kb：整页加 padding 会把消息区压缩、输入卡被顶到导航栏下方（真机踩过）。
  // 只让输入条自己 margin-bottom 抬起 —— 对齐 jl 的做法。
  assert.match(styles, /\.page \{[^}]*padding: 0 0 calc\(126rpx \+ env\(safe-area-inset-bottom\)\)/)
  assert.doesNotMatch(styles, /\.page \{[^}]*var\(--kb/)
  assert.doesNotMatch(styles, /\.input-bar \{[^}]*margin-bottom: var\(--kb/)
  assert.match(styles, /\.at-panel \{[^}]*bottom: calc\(360rpx \+ var\(--kb, 0px\) \+ env\(safe-area-inset-bottom\)\)/)
  // 键盘很高时面板不能顶出屏幕
  assert.match(styles, /\.at-panel \{[^}]*max-height: calc\(100vh - 360rpx - var\(--kb, 0px\) - 100rpx\)/)
  // 关掉微信自动顶起，避免和我们算的 --kb 叠加；cursor-spacing 保证光标不被压住
  assert.match(template, /adjust-position="\{\{true\}\}"/)
  assert.match(template, /cursor-spacing="\{\{20\}\}"/)
})
test('打 @ 唤起：editor 末尾换行与全角 ＠ 也要能触发', () => {
  // 真机坑 1：<editor> 的 detail.text 末尾常带换行，正则用 $ 锚定会失配
  assert.match(page, /replace\(\/\[\\s\\u00a0\\u200b\]\+\$\/, ''\)/)
  // 真机坑 2：中文输入法可能打出全角 ＠
  assert.match(page, /replace\(\/＠\/g, '@'\)/)
  // 选中后替换触发词时，口径要与检测一致（否则关键词会残留在输入框里）
  // 2026-11-23：替换成「显示名」而不是整个 token，输入框里不再出现 @[ ]
  assert.match(page, /replace\(\/\[@＠\]\(\[\^\\s@＠\]\*\)\\s\*\$\/, `\$\{display\} `\)/)
})
test('打字选完引用后必须还能继续输入：用 deleteText 而不是 setContents 重建', () => {
  // 踩过的坑：用 setContents 重建内容后，<editor> 失去输入能力，
  // 用户没法在名字前后继续打字、也不能再 @ 新的东西。
  assert.match(page, /replaceTriggerWithMention\(index: number, triggerLen: number, display: string\)/)
  assert.match(page, /ctx\.deleteText\(\{/)
  assert.match(page, /index,/)
  assert.match(page, /length: triggerLen/)
  // 触发词起点按「删除前文本长度 - 触发词长度」计算
  assert.match(page, /replaceTriggerWithMention\(current\.length - triggerLen, triggerLen, display\)/)
  // 这条路径不能再出现 setContents 重建
  assert.doesNotMatch(page, /replaceEditorWithMention/)
})
test('引用插入只保留一个数据源，插入期间屏蔽中间态', () => {
  // 闪一下又复原的根因：先乐观改 data.input，再动编辑器，两个真相互相覆盖。
  // 现在 editor 模式下先改编辑器，编辑完成后再对齐 data.input。
  assert.match(page, /mentionEditing: false/)
  assert.match(page, /this\.mentionEditing = true/)
  assert.match(page, /this\.mentionEditing = false/)
  // onInput 在插入期间必须直接返回，不参与同步与 @ 唤起
  assert.match(page, /if \(this\.mentionEditing\) return/)
  // 编辑完成后再显式对齐文本（防止部分基础库程序化改动不派发 bindinput）
  assert.match(page, /this\.setData\(\{ input: expected, canSend: true \}\)/)
})
test('消息写入统一在 setData 入口补分段，避免 [[n]] 裸露', () => {
  // 直接 setData({messages}) 的写入点有十几处，逐个加必然漏；
  // 漏掉一处，用户就会看到 [[2]] 原样显示。
  assert.match(page, /const rawSetData = this\.setData\.bind\(this\)/)
  assert.match(page, /Array\.isArray\(p\.messages\)/)
  assert.match(page, /p\.messages\.map\(m => withMentionSegments\(m\)\)/)
})
test('气泡分段标签之间不能有模板空白（pre-wrap 会把缩进渲染成空行）', () => {
  // 症状：引用角标单独占一行、下面还空一行。
  // 原因：.bubble-segments 是 white-space: pre-wrap，模板里 <text> 之间的换行缩进
  // 会被当成真实文本节点渲染出来。三个 <text> 必须紧挨着写。
  assert.match(template, /<\/text><text wx:elif="\{\{seg\.kind === 'cite'\}\}"/)
  assert.match(template, /<\/text><text wx:else class="bubble-plain"/)
  assert.doesNotMatch(template, /<\/text>\s*\r?\n\s*<text wx:elif/)
})
test('分段不得改动原文空白：不能用修剪换行的方式修排版', () => {
  // 踩过的坑：为了让角标贴住句末去修剪前后的换行，
  // 结果把模型原本的列表排版挤乱（角标与下一行的「5. …」并到同一行）。
  // 用改文本的方式修显示是本末倒置，前端只做「标记 → 角标」的替换。
  assert.doesNotMatch(page, /prev\.value = prev\.value\.replace\(\/\\s\+\$\/, ' '\)/)
  assert.doesNotMatch(page, /seg\.value = seg\.value\.replace\(\/\^\[ \\t\]\*\\n/)
})

test('角标是内联上标，不能是占位方块', () => {
  // inline-block 有自己的高度/行高，会参与行盒计算，角标一多就把整行撑开；
  // 改成 display: inline + vertical-align: super 后与纯文本排版一致。
  const rule = (styles.match(/^\.bubble-cite \{[^}]*\}/m) || [''])[0]
  assert.ok(rule, '.bubble-cite 规则缺失')
  assert.match(rule, /display: inline;/)
  assert.match(rule, /vertical-align: super;/)
  assert.doesNotMatch(rule, /inline-block/)
  assert.doesNotMatch(rule, /height:/)
  assert.doesNotMatch(rule, /line-height:/)
})
test('角标/标点拆行统一兜底：行首不许有角标、行内不许只剩标点', () => {
  // AI 输出不可靠，形态五花八门（角标跑行首、句号独占行、孤立小圆圈…），
  // 逐例打补丁修不完，所以用两条硬规则统一处理：
  //   A. 行首角标一律挪到上一行末尾
  //   B. 只剩角标 / 只剩标点的行一律并回上一行
  assert.match(page, /function normalizeCiteArtifacts\(text: string\): string \{/)
  assert.match(page, /const CITE_ONLY = \/\^\(\?:\\\[\\\[\\d\{1,3\}\\\]\\\]\[ \\t\]\*\)\+\$\//)
  assert.match(page, /const PUNCT_ONLY = /)
  assert.match(page, /const PUNCT_START = /)
  // 规则 A：行首角标
  assert.match(page, /const CITE_LEADING = /)
  assert.match(page, /const lead = CITE_LEADING\.exec\(line\)/)
  // 孤立符号噪音
  assert.match(page, /const ORPHAN_SYMBOL = /)
  // 角标搬走后不能留下空行（否则列表项之间会冒空行）
  assert.match(page, /if \(wasNonEmpty && line\.trim\(\) === ''\) continue/)
  // 往前找最近的非空行（跳过空行但不删空行）
  assert.match(page, /while \(p >= 0 && out\[p\]\.trim\(\) === ''\) p--/)
  // 明确不再出现「压缩单个换行」那类会挤乱列表的改动（三个以上换行压成空行是允许的）
  assert.doesNotMatch(page, /replace\(\/\^\[ \\t\]\*\\n\[ \\t\]\*\\n\+\//)
})

test('复制的是渲染后的形态：合并拆行并把 [[n]] 变成 [n]', () => {
  // 用户复制出来是「…我不编造 / [[1]] / 。」三行，与界面不一致。
  assert.match(page, /normalizeCiteArtifacts\(raw\)\.replace\(\/\\\[\\\[\(\\d\{1,3\}\)\\\]\\\]\/g, '\[\$1\]'\)/)
  assert.match(page, /const raw = msg && msg\.text \? String\(msg\.text\) : ''/)
})