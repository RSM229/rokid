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
  if (id === './agent-execution') return { isExecutionRecoverable: () => false, waitForAgentExecution: async () => null }
  if (id === '../config') return { absoluteMediaUrl: (value) => value }
  return require(id)
}
loaded._compile(compiled, file)
const model = loaded.exports

function chooseRecordTypes(plan, recordType = '巡视') {
  return plan.events.reduce((current, event) => model.editEvent(current, event.id, {
    recordType, recordTypeConfirmed: true, recordTypeConfidence: 1, recordTypeReason: '测试中由用户确认',
  }), plan)
}

test('nine photo dates remain nine distinct candidate events and use watermark dates', () => {
  let plan = model.createExample(1)
  assert.equal(plan.evidence.length, 9)
  assert.equal(plan.events.length, 9)
  assert.equal(plan.events.filter(event => event.dateConfirmed).length, 9)
  assert.equal(model.reviewIssues(plan).length, 9)
  assert.match(model.reviewIssues(plan)[0], /请选择工作记录类型/)
  plan = chooseRecordTypes(plan)
  assert.equal(model.reviewIssues(plan).length, 0)
})

test('watermark labels and values can be edited per evidence and invalidate confirmation', () => {
  let plan = model.createExample(2)
  const evidenceId = plan.events[0].evidenceIds[0]
  plan = chooseRecordTypes(plan)
  plan = { ...plan, evidence: plan.evidence.map(item => item.id === evidenceId ? { ...item, watermarkItems: [{ label: '备注', value: '' }] } : item) }
  plan = model.confirmPlan(plan)
  plan = model.editEvidenceWatermark(plan, evidenceId, 0, 'value', '补充：夜间施工')
  plan = model.editEvidenceWatermark(plan, evidenceId, 0, 'label', '检查备注')
  assert.deepEqual(plan.evidence.find(item => item.id === evidenceId).watermarkItems, [{ label: '检查备注', value: '补充：夜间施工' }])
  assert.equal(plan.confirmedRevision, null)
  plan = model.addEvidenceWatermark(plan, evidenceId)
  assert.equal(plan.evidence.find(item => item.id === evidenceId).watermarkItems.length, 2)
})

test('photos on the same observed date do not force one event per photo', () => {
  const plan = model.createExample(2)
  assert.equal(plan.evidence.length, 9)
  assert.equal(plan.events.length, 3)
  assert.equal(new Set(plan.events.map(event => event.date)).size, 1)
  assert.equal(plan.events.every(event => event.dateConfirmed), true)
})

test('evidence grouping uses date, project, location and section without treating missing values as conflicts', () => {
  const evidence = [
    { id: 'a', image: 'a.jpg', observedDate: '2026-10-18', project: '白泽项目', location: 'A区', section: '3号楼二层' },
    { id: 'b', image: 'b.jpg', observedDate: '2026-10-18', project: '白泽项目二期', location: 'A区东侧', section: '3号楼二层梁板', note: '' },
    { id: 'c', image: 'c.jpg', observedDate: '2026-10-19', project: '白泽项目', location: 'A区', section: '3号楼二层', note: '' },
    { id: 'd', image: 'd.jpg', observedDate: '2026-10-18', project: '另一项目', location: 'A区', section: '3号楼二层', note: '' },
    { id: 'e', image: 'e.jpg', observedDate: '2026-10-18', project: '白泽项目', location: 'B区', section: '3号楼二层', note: '' },
    { id: 'f', image: 'f.jpg', observedDate: '', project: '', location: '', section: '', note: '' },
  ]
  const groups = model.groupReviewEvidence(evidence)
  assert.deepEqual(groups.map(group => group.evidenceIds), [['a', 'b', 'f'], ['c'], ['d'], ['e']])
  assert.equal(model.groupReviewEvidence(evidence, { ignoreDate: true }).length, 3)
})

test('watermark date and location win over conflicting user retelling', () => {
  const selected = model.chooseReviewIdentity(
    { date: '2026-10-18', location: 'A区' },
    { date: '2026-10-19', location: 'B区', section: '3号楼二层' },
  )
  assert.equal(selected.date, '2026-10-19')
  assert.equal(selected.location, 'B区')
  assert.equal(selected.dateSource, 'watermark_or_exif')
  assert.equal(selected.locationSource, 'watermark')
  assert.equal(selected.userDateConflict, true)
  assert.equal(selected.userLocationConflict, true)
})

test('review identity falls back per field when watermark evidence is missing', () => {
  const selected = model.chooseReviewIdentity(
    { date: '2026-10-18', location: 'A区' },
    { date: '', location: '', section: '3号楼二层' },
  )
  assert.equal(selected.date, '2026-10-18')
  assert.equal(selected.location, '3号楼二层')
  assert.equal(selected.dateSource, 'user_message')
  assert.equal(selected.locationSource, 'watermark_section')
  assert.equal(selected.userDateConflict, false)
  assert.equal(selected.userLocationConflict, true)

  const userOnly = model.chooseReviewIdentity(
    { date: '2026-10-18', location: 'A区' },
    { date: '', location: '', section: '' },
  )
  assert.equal(userOnly.date, '2026-10-18')
  assert.equal(userOnly.location, 'A区')
  assert.equal(userOnly.locationSource, 'user_message')
})

test('record attachment validation reports only known identity conflicts', () => {
  const evidence = [
    { id: 'a', image: 'a.jpg', observedDate: '2026-10-18', project: '白泽项目', location: 'A区', section: '3号楼二层', note: '' },
    { id: 'b', image: 'b.jpg', observedDate: '', project: '', location: 'B区', section: '', note: '' },
  ]
  assert.deepEqual(model.evidenceTargetConflicts(evidence, {
    date: '2026-10-18', project: '白泽项目一期', location: 'A区', section: '3号楼二层',
  }), ['地点'])
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
    gd_b_213_pingxing: {
      conclusion: { value: '合格', source: 'user_message', status: 'user_confirmed' },
      basis: { value: 'GB 50204', source: 'user_message', status: 'user_confirmed' },
    },
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
  plan = chooseRecordTypes(plan)
  plan = model.confirmPlan(plan)
  assert.equal(plan.confirmedRevision, plan.revision)
  plan = model.editRequest(plan, '改过的工作要求')
  assert.equal(plan.confirmedRevision, null)
})

test('Agent type recommendation cannot be confirmed until the user selects a record type', () => {
  let plan = model.createExample(4)
  plan = model.editEvent(plan, plan.events[0].id, { recordType: '巡视', recordTypeConfidence: 0.96 })
  assert.match(model.reviewIssues(plan)[0], /请由你确认或修改/)
  plan = chooseRecordTypes(plan)
  assert.equal(model.reviewIssues(plan).length, 0)
})

test('changing the selected record type resets stale form binding to that type default', () => {
  const plan = model.createExample()
  const event = plan.events[0]
  event.recordType = '巡视'
  event.recordTypeConfirmed = true
  event.forms = ['旁站记录']
  const selectedCode = model.FORM_CODE_BY_LABEL[event.forms[0]]
  const next = model.editEvent(plan, event.id, {
    recordType: '验收', recordTypeConfirmed: true,
    forms: ['验收记录'],
  })
  assert.equal(selectedCode, 'gd_b_212_pangzhan')
  assert.deepEqual(next.events[0].forms, ['验收记录'])
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
  plan = chooseRecordTypes(plan)
  assert.equal(model.reviewIssues(plan).length, 0)
  assert.equal(model.confirmPlan(plan).confirmedRevision, plan.revision)
})

test('server item plan is adapted without re-grouping and keeps every evidence item accounted for', () => {
  const evidence = [
    { id: 'p1', image: 'p1.jpg', observedDate: '2026-10-18', location: 'A区', section: '3号楼二层', note: '' },
    { id: 'p2', image: 'p2.jpg', observedDate: '2026-10-18', location: 'A区', section: '3号楼二层', note: '' },
    { id: 'p3', image: 'p3.jpg', observedDate: '', location: '', section: '', note: '' },
  ]
  const plan = model.reviewPlanFromItemPlan({
    items: [
      {
        id: 'item_1', record_type: '巡视',
        fields: {
          record_date: { value: '2026-10-18', source: 'watermark', status: 'evidence_extracted' },
          section: { value: '3号楼二层', source: 'watermark', status: 'evidence_extracted' },
        },
        evidence_indexes: [0, 1], missing_fields: ['location'], issues: ['地点待核对'],
        notes: ['钢筋绑扎'], confidence: 0.9, status: 'candidate',
      },
      {
        id: 'item_2', record_type: '验收',
        fields: { record_date: { value: '2026-10-19', source: 'user_message', status: 'user_stated_candidate' } },
        evidence_indexes: [], missing_fields: ['location', 'section'], issues: [], confidence: 0.75, status: 'candidate',
      },
    ],
    unassigned_evidence_indexes: [],
    issues: ['识别到 2 个可能事项；合并或拆分必须由用户确认'],
  }, evidence, '记录今天的工作')
  assert.deepEqual(plan.events.map(event => event.id), ['item_1', 'item_2'])
  assert.deepEqual(plan.events[0].evidenceIds, ['p1', 'p2'])
  assert.equal(plan.events[0].dateSource, 'watermark')
  assert.deepEqual(plan.events[0].missingFields, ['location'])
  assert.equal(plan.events[0].fields.gd_b_214_xunshi.section.value, '3号楼二层')
  assert.equal(plan.events[0].fields.gd_b_214_xunshi.section.status, 'evidence_extracted')
  assert.deepEqual(plan.unassignedIds, ['p3'])
  assert.ok(plan.notices.some(notice => notice.includes('合并或拆分')))
  assert.equal(plan.evidence.length, 3)
})

test('entity conflicts survive the item-plan adapter and block confirmation until selected', () => {
  const evidence = [{ id: 'p1', image: 'p1.jpg', observedDate: '2026-10-09', section: '安全巡视', note: '' }]
  const plan = model.reviewPlanFromItemPlan({
    items: [{
      id: 'item_1', record_type: '巡视',
      fields: {
        record_date: { value: '2026-10-09', source: 'watermark', status: 'evidence_extracted' },
        section: { value: '安全巡视', source: 'watermark', status: 'evidence_extracted' },
        project: {
          value: '广东省中西医结合应急救治中心项目', source: 'watermark', status: 'conflict',
          candidates: ['广东省中西医结合应急救治中心项目', '111'],
        },
        construction_unit: {
          value: '广州一建建设集团有限公司', source: 'watermark', status: 'conflict',
          candidates: ['广州一建建设集团有限公司', '系统施工单位'],
        },
      },
      evidence_indexes: [0], missing_fields: [], issues: ['项目存在多个候选值'],
    }],
    unassigned_evidence_indexes: [], issues: [],
  }, evidence, '记录这个工作')
  assert.equal(plan.events[0].entityFields.project_name, '广东省中西医结合应急救治中心项目')
  assert.equal(plan.events[0].entityFields.construction_unit, '广州一建建设集团有限公司')
  assert.deepEqual(plan.events[0].fieldCandidates.project_name, ['广东省中西医结合应急救治中心项目', '111'])
  assert.deepEqual(plan.events[0].unresolvedConflicts.sort(), ['construction_unit', 'project_name'])
  let confirmed = chooseRecordTypes(plan)
  assert.ok(model.reviewIssues(confirmed).some(issue => issue.includes('项目存在多个候选值')))
  confirmed = model.editEvent(confirmed, 'item_1', {
    entityFields: { ...confirmed.events[0].entityFields, project_name: '111' },
    unresolvedConflicts: confirmed.events[0].unresolvedConflicts.filter(field => field !== 'project_name'),
  })
  assert.ok(!model.reviewIssues(confirmed).some(issue => issue.includes('项目存在多个候选值')))
})

test('removing an event releases its evidence without deleting photos', () => {
  const plan = model.removeEvent(model.createExample(), 'event-1')
  assert.equal(plan.evidence.length, 9)
  assert.deepEqual(plan.unassignedIds, ['photo-1', 'photo-2', 'photo-3'])
  assert.throws(() => model.assignEvidence(plan, 'photo-1', 'missing-event'), /不存在/)
  assert.equal(model.restorePlan(plan).events.length, 2)
})

test('merging events keeps conflicting parties selectable instead of concatenating them', () => {
  let plan = model.createExample()
  plan = model.editEvent(plan, plan.events[0].id, {
    entityFields: { project_name: '项目甲', construction_unit: '单位甲' },
    fieldCandidates: { project_name: ['项目甲', '项目乙'], construction_unit: ['单位甲', '单位乙'] },
    unresolvedConflicts: ['project_name', 'construction_unit'],
  })
  plan = model.editEvent(plan, plan.events[1].id, {
    entityFields: { project_name: '项目乙', construction_unit: '单位乙' },
    fieldCandidates: { project_name: ['项目甲', '项目乙'], construction_unit: ['单位甲', '单位乙'] },
    unresolvedConflicts: ['project_name', 'construction_unit'],
  })
  const merged = model.mergeEvents(plan, [plan.events[0].id, plan.events[1].id], 'merged-conflict')
  const event = merged.events.find(item => item.id === 'merged-conflict')
  assert.equal(event.entityFields.project_name, '项目甲')
  assert.deepEqual(event.fieldCandidates.project_name, ['项目甲', '项目乙'])
  assert.ok(event.unresolvedConflicts.includes('project_name'))
  assert.ok(event.unresolvedConflicts.includes('construction_unit'))
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
