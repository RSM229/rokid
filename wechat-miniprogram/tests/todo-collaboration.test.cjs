const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')

const root = path.join(__dirname, '../miniprogram')
const app = JSON.parse(fs.readFileSync(path.join(root, 'app.json'), 'utf8'))
const agentPage = fs.readFileSync(path.join(root, 'pages/agent/index.ts'), 'utf8')
const agentTemplate = fs.readFileSync(path.join(root, 'pages/agent/index.wxml'), 'utf8')
const todoPage = fs.readFileSync(path.join(root, 'pages/todo/index.ts'), 'utf8')
const todoTemplate = fs.readFileSync(path.join(root, 'pages/todo/index.wxml'), 'utf8')
const composePage = fs.readFileSync(path.join(root, 'pages/todo/compose.ts'), 'utf8')
const composeTemplate = fs.readFileSync(path.join(root, 'pages/todo/compose.wxml'), 'utf8')
const kbPage = fs.readFileSync(path.join(root, 'pages/kb/index.ts'), 'utf8')
const workPage = fs.readFileSync(path.join(root, 'pages/work/index.ts'), 'utf8')
const workTemplate = fs.readFileSync(path.join(root, 'pages/work/index.wxml'), 'utf8')

test('todo is a registered ordinary page and not a bottom navigation tab', () => {
  assert.ok(app.pages.includes('pages/todo/index'))
  assert.equal((app.tabBar?.list || []).some(item => item.pagePath === 'pages/todo/index'), false)
})

test('drawer replaces the work-document group with todo and shares the pending badge', () => {
  assert.match(agentTemplate, /data-url="\/pages\/todo\/index"[\s\S]{0,180}待办事项/)
  assert.doesNotMatch(agentTemplate, /工作文书/)
  assert.match(agentTemplate, /class="todo-badge"[\s\S]{0,120}todoPendingCount/)
  assert.match(agentTemplate, /class="todo-drawer-badge"[\s\S]{0,120}todoPendingCount/)
  assert.match(agentPage, /request<\{ pending_count: number \}>\('\/collaboration\/inbox\?view=action'\)/)
})

test('todo count refreshes on page return and when the drawer is opened', () => {
  const onShow = agentPage.match(/onShow\(\)[\s\S]*?(?=\n  onHide\()/)?.[0] || ''
  const openHistory = agentPage.match(/async openHistory\(\)[\s\S]*?(?=\n  \/\*\*)/)?.[0] || ''
  assert.match(onShow, /loadTodoPendingCount\(\)/)
  assert.match(openHistory, /loadTodoPendingCount\(\)/)
})

test('todo inbox has actionable, tracking, sent, and legacy task views with empty states', () => {
  for (const view of ['action', 'cc', 'sent', 'done', 'tasks']) assert.ok(todoPage.includes(`key: '${view}'`))
  assert.match(todoTemplate, /当前没有待处理事项/)
  assert.match(todoTemplate, /当前没有抄送事项/)
  assert.match(todoTemplate, /当前没有发出的事项/)
  assert.match(todoTemplate, /还没有处理记录/)
  assert.match(todoTemplate, /当前没有符合条件的派单/)
})

test('opening a collaboration detail does not complete or remove its pending item', () => {
  const openDetail = todoPage.match(/async openDetail\([\s\S]*?(?=\n  closeDetail\()/)?.[0] || ''
  assert.match(openDetail, /\/collaboration\/\$\{id\}/)
  assert.doesNotMatch(openDetail, /resolve|complete|status\s*=/)
  assert.match(todoPage, /onShow\(\)[\s\S]{0,520}this\.load\(\)/)
})

test('todo detail uses server business labels and keeps deleted-object history readable', () => {
  assert.match(todoTemplate, /item\.status_label/)
  assert.match(todoTemplate, /item\.mode_label/)
  assert.match(todoTemplate, /item\.action_label/)
  assert.match(todoTemplate, /detail\.object_deleted/)
  assert.match(todoTemplate, /关联业务对象已删除，仅保留协作记录/)
  assert.match(todoTemplate, /item\.action_label \|\| item\.action/)
  assert.match(todoTemplate, /item\.actor_name/)
  assert.match(todoPage, /if \(detail\.object_deleted\)/)
})

test('completion gives a result summary and keeps the item in a real completed inbox', () => {
  assert.match(todoTemplate, /completion-mask/)
  assert.match(todoTemplate, /completion\.eyebrow/)
  assert.match(todoPage, /eyebrow: '处理结果'/)
  assert.match(todoPage, /label: '处理记录'/)
  assert.match(todoTemplate, /返回待办/)
  assert.match(todoTemplate, /查看工作记录/)
  assert.match(todoTemplate, /查看工作文书/)
  assert.match(todoTemplate, /完成于/)
  assert.match(todoPage, /buildCompletion\(/)
  assert.match(todoPage, /checkCompletedAfterBusiness\(/)
  assert.match(todoPage, /pendingCheckId/)
  assert.match(todoPage, /completion, collabActionId: 0/)
  assert.match(todoPage, /note \|\| '已完成'/)
  assert.match(todoPage, /请填写驳回原因/)
})

test('compose sends a stable idempotency key for network retries', () => {
  assert.match(composePage, /clientRequestId/)
  assert.match(composePage, /client_request_id: this\.data\.clientRequestId/)
  const submit = composePage.match(/async submit\(\)[\s\S]*?(?=\n  \},\n\}\)\))/)?.[0] || ''
  assert.match(submit, /client_request_id/)
})

test('compose hydrates the real business object instead of trusting a route title', () => {
  assert.match(composePage, /request<WorkRecordContext>\(`\/work-record\/\$\{objectId\}`\)/)
  assert.match(composePage, /request<DocumentContext>\(`\/document\/\$\{objectId\}`\)/)
  assert.match(composePage, /request<Member\[\]>\('\/collaboration\/members'\)/)
  assert.doesNotMatch(composePage, /options\.title|objectTitle/)
  assert.doesNotMatch(workPage, /compose\?type=work_record&id=\$\{detail\.id\}&title=/)
  assert.doesNotMatch(fs.readFileSync(path.join(root, 'pages/documents/detail.ts'), 'utf8'), /compose\?type=document&id=\$\{doc\.id\}&title=/)
})

test('compose separates responsibility, signoff and non-blocking CC semantics', () => {
  assert.match(composeTemplate, /负责人/)
  assert.match(composeTemplate, /主要跟踪人/)
  assert.match(composeTemplate, /同时抄送给/)
  assert.match(composeTemplate, /不承担处理责任/)
  assert.match(composeTemplate, /不计入待办/)
  assert.match(composePage, /待我处理/)
  assert.match(composeTemplate, /仅显示当前文书尚未完成的必签项/)
  assert.match(composeTemplate, /objectLoading/)
  assert.match(composeTemplate, /objectError/)
  assert.match(composeTemplate, /membersError/)
})

test('project archive entry uses the dedicated project-scoped API, not a library-name scan', () => {
  assert.match(kbPage, /request<\{ id: number; name: string \}>\('\/kb\/project-archive'\)/)
  assert.doesNotMatch(kbPage, /find\(\(item\) => item\.kb_type === 'shared' && item\.project_id === projectId\)/)
})

test('work-record page retains a visible document-management entry after the drawer change', () => {
  assert.match(workTemplate, /bindtap="openDocuments"[\s\S]{0,40}文书管理/)
  assert.match(workPage, /openDocuments\(\)[\s\S]{0,120}\/pages\/documents\/index/)
})

test('todo badge refreshes after project switch and clears on lookup failure', () => {
  const pickProject = agentPage.match(/async pickProject\([\s\S]*?(?=\n  \},)/)?.[0] || ''
  const loader = agentPage.match(/async loadTodoPendingCount\([\s\S]*?(?=\n  \},)/)?.[0] || ''
  assert.match(pickProject, /loadTodoPendingCount\(\)/)
  assert.match(loader, /catch \{[\s\S]{0,120}todoPendingCount: 0/)
})
