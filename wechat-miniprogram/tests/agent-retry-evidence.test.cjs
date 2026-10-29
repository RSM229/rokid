const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')

const page = fs.readFileSync(path.join(__dirname, '../miniprogram/pages/agent/index.ts'), 'utf8')

test('failed review-plan save puts the images back into the composer', () => {
  assert.match(page, /const restored = this\.restoreAttachments\(images\)/)
  assert.match(page, /restoreAttachments\(items: Attachment\[\]\): number/)
  assert.match(page, /直接回复「继续记录工作」即可重试，不用重新选图/)
  assert.match(page, /服务端没落库、客户端又已清空 attachments 时/)
})