const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const ts = require('typescript')
const root = path.join(__dirname, '../miniprogram')
const app = JSON.parse(fs.readFileSync(path.join(root, 'app.json'), 'utf8'))
const agentPage = fs.readFileSync(path.join(root, 'pages/agent/index.ts'), 'utf8')
const agentTemplate = fs.readFileSync(path.join(root, 'pages/agent/index.wxml'), 'utf8')
const workTemplate = fs.readFileSync(path.join(root, 'pages/work/index.wxml'), 'utf8')

test('agent select controls store the selected label instead of the picker index', () => {
  assert.match(agentPage, /field\.control === 'select'[\s\S]{0,160}field\.options\[Number\(value\)\]/)
})

test('agent uses one chat-native form renderer and normalizes legacy input components', () => {
  assert.match(agentPage, /legacy_input_components\.v1/)
  assert.doesNotMatch(agentTemplate, /item\.inputComponents/)
})

test('work-form save confirms and executes an unambiguous review plan', () => {
  assert.match(agentPage, /form\.kind !== 'work_record'[\s\S]{0,220}confirmAndExecuteInlineReview/)
  assert.match(agentTemplate, /保存并生成记录/)
  assert.doesNotMatch(agentTemplate, /disabled="\{\{!item\.reviewCanConfirm/)
})

test('blocked review plans explain that no formal record was generated', () => {
  assert.match(agentTemplate, /先处理待确认项/)
  assert.match(agentPage, /尚未生成正式工作记录/)
})

test('history restores the latest unfinished review plan instead of orphaning it', () => {
  assert.match(agentPage, /restorePendingReviewPlan\(detail\.conversation_id\)/)
  assert.match(agentPage, /listReviewPlans\(conversationId\)/)
  assert.match(agentPage, /已恢复上次未完成的记录草稿/)
})

test('inspection details show the saved inspector, not the record creator', () => {
  assert.match(workTemplate, /巡视监理人员[\s\S]{0,180}detail\.inspector/)
})

test('agent cards identify and navigate to parallel inspection records', () => {
  assert.match(agentTemplate, /cd\.kind === 'parallel_inspection' \? '平行检验记录'/)
  assert.match(agentPage, /kind === 'parallel_inspection'[\s\S]{0,120}pages\/parallel\/index/)
})

for (const route of app.pages) {
  test(`${route}: page files, components and event handlers exist`, () => {
    const base = path.join(root, route)
    for (const ext of ['ts', 'wxml', 'json']) assert.ok(fs.existsSync(`${base}.${ext}`), `${route}.${ext}`)
    const config = JSON.parse(fs.readFileSync(`${base}.json`, 'utf8'))
    for (const [name, value] of Object.entries(config.usingComponents || {})) {
      if (value.startsWith('plugin://')) continue
      const target = value.startsWith('/') ? path.join(root, value) : path.resolve(path.dirname(base), value)
      assert.ok(fs.existsSync(`${target}.json`), `${name}: ${value}`)
    }
    const source = ts.createSourceFile(`${base}.ts`, fs.readFileSync(`${base}.ts`, 'utf8'), ts.ScriptTarget.Latest, true)
    const handlers = new Set()
    function visit(node) {
      if (ts.isMethodDeclaration(node) && node.name) handlers.add(node.name.getText(source).replace(/['"]/g, ''))
      ts.forEachChild(node, visit)
    }
    visit(source)
    const wxml = fs.readFileSync(`${base}.wxml`, 'utf8')
    for (const match of wxml.matchAll(/\b(?:bind|catch|capture-bind|capture-catch):?[\w-]+\s*=\s*"([A-Za-z_$][\w$]*)"/g)) {
      if (match[1] === 'true') continue
      assert.ok(handlers.has(match[1]), `${route} missing handler ${match[1]}`)
    }
  })
}
