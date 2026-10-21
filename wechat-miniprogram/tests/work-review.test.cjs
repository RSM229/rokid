const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const Module = require('node:module')
const ts = require('typescript')

const file = path.join(__dirname, '../miniprogram/services/work-review.ts')
const source = fs.readFileSync(file, 'utf8')
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText
const loaded = new Module(file, module)
loaded.filename = file
loaded.paths = Module._nodeModulePaths(path.dirname(file))
loaded.require = (id) => {
  if (id === './request') return { request: async () => { throw new Error('network call is unavailable in this unit test') } }
  if (id === '../config') return { absoluteMediaUrl: (value) => value }
  return require(id)
}
loaded._compile(compiled, file)
const model = loaded.exports

test('nine photo dates remain nine distinct candidate events and use watermark dates', () => {
  const plan = model.createExample(1)
  assert.equal(plan.evidence.length, 9)
  assert.equal(plan.events.length, 9)
  assert.equal(plan.events.filter(event => event.dateConfirmed).length, 9)
  assert.equal(model.reviewIssues(plan).length, 0)
})

test('photos on the same observed date do not force one event per photo', () => {
  const plan = model.createExample(2)
  assert.equal(plan.evidence.length, 9)
  assert.equal(plan.events.length, 3)
  assert.equal(new Set(plan.events.map(event => event.date)).size, 1)
  assert.equal(plan.events.every(event => event.dateConfirmed), true)
})

test('unknown intent does not infer events; evidence can remain explicitly unassigned', () => {
  const plan = model.createExample(3)
  assert.equal(plan.events.length, 0)
  assert.deepEqual(plan.unassignedIds, plan.evidence.map(photo => photo.id))
  assert.throws(() => model.confirmPlan(plan), /尚未确定/)
})

test('one event may produce multiple selected forms and separate records', () => {
  let plan = model.createExample(0)
  plan = model.editEvent(plan, 'event-1', { forms: ['巡视记录', '监理日志'], date: '2026-10-18', dateConfirmed: true })
  plan = model.editEvent(plan, 'event-2', { forms: ['旁站记录'], date: '2026-10-19', dateConfirmed: true })
  plan = model.editEvent(plan, 'event-3', { forms: ['验收记录'], date: '2026-10-20', dateConfirmed: true })
  assert.equal(plan.events.length, 3)
  assert.equal(plan.events[0].evidenceIds.length, 3)
  assert.equal(plan.events.flatMap(event => event.forms).length, 4)
})

test('form-specific values survive edits and compatible event merges', () => {
  let plan = model.createExample(0)
  plan = model.editFormField(plan, 'event-1', 'gd_b_213_pingxing', 'conclusion', '合格')
  plan = model.editFormField(plan, 'event-2', 'gd_b_213_pingxing', 'basis', 'GB 50204')
  plan = model.mergeEvents(plan, ['event-1', 'event-2'], 'merged-fields')
  assert.deepEqual(plan.events.find(event => event.id === 'merged-fields').fields, {
    gd_b_213_pingxing: { conclusion: '合格', basis: 'GB 50204' },
  })
})

test('merging events refuses to silently overwrite conflicting form values', () => {
  let plan = model.createExample(0)
  plan = model.editFormField(plan, 'event-1', 'gd_b_213_pingxing', 'conclusion', '合格')
  plan = model.editFormField(plan, 'event-2', 'gd_b_213_pingxing', 'conclusion', '不合格')
  assert.throws(() => model.mergeEvents(plan, ['event-1', 'event-2'], 'merged-fields'), /内容冲突/)
})

test('split, merge and unassigned/assignment preserve every piece of evidence', () => {
  let plan = model.createExample(0)
  plan = model.splitEvidence(plan, 'photo-1', 'event-split')
  assert.equal(plan.events.length, 4)
  assert.equal(plan.events.find(event => event.id === 'event-split').evidenceIds[0], 'photo-1')
  plan = model.mergeEvents(plan, ['event-1', 'event-2'], 'event-merged')
  assert.equal(plan.events.find(event => event.id === 'event-merged').date, '')
  plan = model.releaseEvidence(plan, 'photo-9')
  assert.ok(plan.unassignedIds.includes('photo-9'))
  plan = model.assignEvidence(plan, 'photo-9', 'event-merged')
  assert.ok(plan.events.find(event => event.id === 'event-merged').evidenceIds.includes('photo-9'))
  assert.equal(plan.evidence.length, 9)
  assert.equal(plan.events.flatMap(event => event.evidenceIds).length + plan.unassignedIds.length, 9)
})

test('confirm does not require a form or manual date; any later edit invalidates confirmation', () => {
  let plan = model.createExample(0)
  plan = model.confirmPlan(plan)
  assert.equal(plan.confirmedRevision, plan.revision)
  plan = model.editRequest(plan, '改过的工作要求')
  assert.equal(plan.confirmedRevision, null)
})

test('restored drafts reject duplicated or lost evidence assignments', () => {
  const plan = model.createExample(0)
  assert.equal(model.restorePlan(plan).events.length, 3)
  const invalid = { ...plan, events: plan.events.map((event, i) => i === 0 ? { ...event, evidenceIds: [...event.evidenceIds, 'photo-4'] } : event) }
  assert.equal(model.restorePlan(invalid), null)
})

test('text-only requests can be confirmed without requiring photo evidence', () => {
  let plan = model.createExample(4)
  assert.equal(plan.evidence.length, 0)
  for (const event of plan.events) plan = model.editEvent(plan, event.id, { date: '2026-10-20', dateConfirmed: true })
  assert.equal(model.reviewIssues(plan).length, 0)
  assert.equal(model.confirmPlan(plan).confirmedRevision, plan.revision)
})

test('removing an event releases its evidence without deleting photos', () => {
  const plan = model.removeEvent(model.createExample(), 'event-1')
  assert.equal(plan.evidence.length, 9)
  assert.deepEqual(plan.unassignedIds, ['photo-1', 'photo-2', 'photo-3'])
  assert.throws(() => model.assignEvidence(plan, 'photo-1', 'missing-event'), /不存在/)
  assert.equal(model.restorePlan(plan).events.length, 2)
})

test('merging dates preserves original clues and requires explicit new confirmation', () => {
  let plan = model.createExample(1)
  plan = model.editEvent(plan, 'event-1', { dateConfirmed: true })
  plan = model.editEvent(plan, 'event-2', { dateConfirmed: true })
  plan = model.mergeEvents(plan, ['event-1', 'event-2'], 'merged')
  assert.equal(plan.events[0].date, '')
  assert.equal(plan.events[0].dateConfirmed, false)
  assert.deepEqual(model.candidateDates(plan, plan.events[0]), ['2026-10-12', '2026-10-13'])
})

test('invalid dates and stale confirmation cannot be restored', () => {
  assert.equal(model.restorePlan(null), null)
  assert.equal(model.restorePlan({}), null)
  const plan = model.createExample()
  assert.throws(() => model.editEvent(plan, 'event-1', { date: '2026-02-30' }), /日期无效/)
  assert.equal(model.restorePlan({ ...plan, confirmedRevision: plan.revision - 1 }), null)
})
