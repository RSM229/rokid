const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')

const page = fs.readFileSync(path.join(__dirname, '../miniprogram/pages/agent/index.ts'), 'utf8')
const template = fs.readFileSync(path.join(__dirname, '../miniprogram/pages/agent/index.wxml'), 'utf8')

test('hazard vision creates an in-conversation candidate before any formal record write', () => {
  assert.match(page, /buildHazardCandidates\(/)
  assert.match(template, /item\.hazardCandidates/)
  assert.match(template, /确认所选隐患并建档/)
  assert.match(template, /confirmHazardCandidates/)
})

test('hazard confirmation uses the server-side atomic batch endpoint, not per-image writes', () => {
  assert.match(page, /\/hazard\/commit-candidates/)
  assert.doesNotMatch(page, /\/hazard\/commit-detection/)
  assert.doesNotMatch(page, /wantsRecordIntent/)
})
