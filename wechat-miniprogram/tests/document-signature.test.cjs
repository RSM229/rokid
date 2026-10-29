const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const ts = require('typescript')

const root = path.join(__dirname, '../miniprogram')
const detail = fs.readFileSync(path.join(root, 'pages/documents/detail.ts'), 'utf8')
const detailTemplate = fs.readFileSync(path.join(root, 'pages/documents/detail.wxml'), 'utf8')
const signature = fs.readFileSync(path.join(root, 'pages/documents/signature.ts'), 'utf8')
const signatureTemplate = fs.readFileSync(path.join(root, 'pages/documents/signature.wxml'), 'utf8')
const signatureExports = {}
vm.runInNewContext(ts.transpileModule(signature, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
}).outputText, { exports: signatureExports, Page() {} })

test('document hand-signing uses a landscape page and returns a confirmed canvas image', () => {
  const app = JSON.parse(fs.readFileSync(path.join(root, 'app.json'), 'utf8'))
  const page = JSON.parse(fs.readFileSync(path.join(root, 'pages/documents/signature.json'), 'utf8'))
  assert.ok(app.pages.includes('pages/documents/signature'))
  assert.equal(page.pageOrientation, 'landscape')
  assert.match(signatureTemplate, /id="signature-surface"/)
  assert.match(signatureTemplate, /type="2d"/)
  assert.match(signature, /if \(!this\.data\.hasInk\)/)
  assert.match(signature, /canvasToTempFilePath/)
  assert.match(signature, /emit\('signatureComplete', \{ tempFilePath: result.tempFilePath \}\)/)
  assert.match(detail, /signatureTempPath: payload.tempFilePath/)
})

test('signature pointer maps canvas-local coordinates independently of page offsets', () => {
  const rect = { left: 160, top: 90, width: 800, height: 360 }
  const localPoint = signatureExports.mapSignaturePoint({ x: 400, y: 180 }, rect)
  // 落笔点必须与手指重合：旧实现把它放大到 1400x620 的假想画布（700/310），
  // 横屏真机上纵向还会被放大 3 倍以上，笔画明显偏离手指。
  assert.equal(localPoint.x, 400)
  assert.equal(localPoint.y, 180)

  const pagePoint = signatureExports.mapSignaturePoint({ clientX: 560, clientY: 270 }, rect)
  assert.equal(pagePoint.x, localPoint.x)
  assert.equal(pagePoint.y, localPoint.y)

  const clamped = signatureExports.mapSignaturePoint({ x: 900, y: -20 }, rect)
  assert.equal(clamped.x, 800)
  assert.equal(clamped.y, 0)

  // 画布坐标不再依赖固定位图尺寸，也不能残留 1400x620 的换算。
  assert.doesNotMatch(signature, /CANVAS_WIDTH|CANVAS_HEIGHT/)

  const styles = fs.readFileSync(path.join(root, 'pages/documents/signature.wxss'), 'utf8')
  assert.match(styles, /position: fixed; top: 0; right: 0; bottom: 0; left: 0/)
})

test('album upload selects the new signature without resetting the authorization form', () => {
  const upload = detail.match(/uploadPersonalSignature\(\) \{[\s\S]*?(?=\n  submit\()/)?.[0] || ''
  assert.match(upload, /formData: \{ name: '个人签名', password: this.data.signPassword \}/)
  assert.match(upload, /signatures.push\(\{ id: value.id/)
  assert.match(upload, /signSignatureIndex: signatures.findIndex/)
  assert.doesNotMatch(upload, /this.openSign\(/)
  assert.match(detailTemplate, /class="sign-primary"[\s\S]*确认本人授权签认/)
  assert.match(detailTemplate, /class="sign-secondary"[\s\S]*从相册上传签名图片/)
  assert.match(detailTemplate, /class="sign-cancel"[\s\S]*取消/)
})
