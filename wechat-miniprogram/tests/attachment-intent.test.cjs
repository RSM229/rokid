const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const ts = require('typescript')

function load() {
  const filename = path.join(__dirname, '../miniprogram/services/attachment-intent.ts')
  const code = ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
  }).outputText
  const exports = {}
  vm.runInNewContext(code, { exports }, { filename })
  return exports.needsHazardDetection
}

const needsHazardDetection = load()

test('work-record requests and watermark reading do not trigger hazard vision', () => {
  for (const note of [
    '记录今天的工作',
    '记录一下现场情况',
    '识别一下图片水印',
    '读取照片上的日期和地点',
    '',
  ]) {
    assert.equal(needsHazardDetection(note), false, note)
  }
})

test('explicit hazard and safety questions trigger hazard vision', () => {
  for (const note of [
    '看看这张图有没有安全隐患',
    '识别现场安全风险',
    '图片里有没有问题',
    '帮我检查一下这里是否安全',
  ]) {
    assert.equal(needsHazardDetection(note), true, note)
  }
})
