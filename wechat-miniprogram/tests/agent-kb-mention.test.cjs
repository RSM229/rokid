const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')

const root = path.join(__dirname, '../miniprogram/pages/agent')
const page = fs.readFileSync(path.join(root, 'index.ts'), 'utf8')
const template = fs.readFileSync(path.join(root, 'index.wxml'), 'utf8')
const styles = fs.readFileSync(path.join(root, 'index.wxss'), 'utf8')

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
  assert.match(page, /data: \{ q: query, offset, limit: 20 \}/)
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
  assert.match(page, /token = item\.kind === 'folder'/)
  // 发送时带结构化引用
  assert.match(page, /buildChatMentions\(\): Array<Record<string, unknown>>/)
  assert.match(page, /mentions: this\.buildChatMentions\(\)/)
})

test('用户手工删掉 token 时同步撤销引用', () => {
  assert.match(page, /syncMentionsWithInput\(input: string\)/)
  assert.match(page, /const kept = this\.data\.mentions\.filter\(m => input\.includes\(m\.token\)\)/)
  // onInput 里必须调用，否则删除 token 后模型仍按旧范围检索
  assert.match(page, /this\.syncMentionsWithInput\(input\)/)
})

test('打 @ 唤起面板后，关键词进入搜索而不是本地过滤', () => {
  assert.match(page, /const m = \/\(\^\|\\s\)@\(\[\^\\s@\\\[\\\]\]\*\)\$\/\.exec\(input\)/)
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
