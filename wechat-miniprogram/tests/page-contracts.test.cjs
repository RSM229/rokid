const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const ts = require('typescript')
const root = path.join(__dirname, '../miniprogram')
const app = JSON.parse(fs.readFileSync(path.join(root, 'app.json'), 'utf8'))
const agentPage = fs.readFileSync(path.join(root, 'pages/agent/index.ts'), 'utf8')
const agentTemplate = fs.readFileSync(path.join(root, 'pages/agent/index.wxml'), 'utf8')

test('agent select controls store the selected label instead of the picker index', () => {
  assert.match(agentPage, /field\.control === 'select'[\s\S]{0,160}field\.options\[Number\(value\)\]/)
})

test('agent uses one chat-native form renderer and normalizes legacy input components', () => {
  assert.match(agentPage, /legacy_input_components\.v1/)
  assert.doesNotMatch(agentTemplate, /item\.inputComponents/)
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
