const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')

const root = path.join(__dirname, '../miniprogram/pages/agent')
const page = fs.readFileSync(path.join(root, 'index.ts'), 'utf8')
const template = fs.readFileSync(path.join(root, 'index.wxml'), 'utf8')
const styles = fs.readFileSync(path.join(root, 'index.wxss'), 'utf8')

test('agent replies follow the filing-assistant single-column layout', () => {
  assert.match(styles, /\.message \{ display: flex; flex-direction: column;/)
  assert.match(styles, /\.message\.user \{[^}]*align-items:\s*flex-end/)
  assert.match(styles, /\.message\.assistant \{[^}]*align-items:\s*stretch/)
  assert.match(styles, /\.message\.assistant \{[^}]*border-left/)
  assert.match(template, /class="message-head"/)
  // 7c03e7a 起角色标签随 role 动态切换（助手=监理智能体 / 用户=我），不再是写死的纯文本
  assert.match(template, /class="message-role">\{\{[^}]*item\.role[^}]*'监理智能体'[^}]*\}\}/)
  assert.match(template, /class="agent-task-toggle"[^>]*bindtap="toggleAgentForm"/)
  assert.match(template, /wx:if="\{\{item\.agentForm\.expanded\}\}" class="agent-task-body"/)
  assert.match(page, /toggleAgentForm\(event: WechatMiniprogram\.TouchEvent\)/)
  // 38bc5c8 起新追问默认收起，展开态只保留用户主动点开的；旧断言「沿用 sourceForm.expanded」已反转
  assert.match(page, /sourceForm \? \{[\s\S]{0,400}?expanded: false,/)
  assert.doesNotMatch(page, /expanded: sourceForm\.expanded === true/)
  // f645e17 起「复核详情」整块被「多卡收起」取代：绑定从 toggleInlineReviewDetails
  // 换成 toggleAgentCards（旧方法与 reviewDetailsExpanded 已删除，别再断言它们）
  assert.match(template, /class="inline-review-preview agent-cards-toggle"[^>]*bindtap="toggleAgentCards"/)
  assert.match(page, /toggleAgentCards\(event: WechatMiniprogram\.TouchEvent\)/)
  assert.doesNotMatch(template, /bindtap="toggleInlineReviewDetails"/)
  assert.doesNotMatch(page, /toggleInlineReviewDetails\(/)
  assert.match(page, /function mergeAgentProgress\(target: Message, progress: Message\): Message/)
  assert.match(page, /messages\.splice\(progressIndex, 0, \{ role: 'assistant', text: '', timeText: formatMessageTime\(\) \}\)/)
  assert.match(page, /messages\[index\] = \{\s*\.\.\.messages\[index\], progressRequestId: requestId/)
  assert.match(page, /next\[progressIndex\] = mergeAgentProgress\(reply, progress\)/)
  assert.doesNotMatch(template, /备案订单预览/)
})
