const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const Module = require('node:module')
const ts = require('typescript')

const file = path.join(__dirname, '../miniprogram/utils/date-time-picker.ts')
const source = fs.readFileSync(file, 'utf8')
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText
const loaded = new Module(file, module)
loaded.filename = file
loaded.paths = Module._nodeModulePaths(path.dirname(file))
loaded._compile(compiled, file)
const { buildDateTimePickerState, dateTimeFromIndexes, daysInMonth, normalizeDateTimeInput, resolveDateTimeRange } = loaded.exports

test('datetime values from evidence are shown directly in normalized picker format', () => {
  const state = buildDateTimePickerState('2026-10-09T15:48:22', '', 'datetime', new Date(2026, 9, 26, 10, 5))
  assert.equal(state.displayValue, '2026-10-09 15:48')
  assert.equal(state.isReference, false)
  assert.equal(state.hint, '')
  assert.equal(dateTimeFromIndexes(state.indexes), '2026-10-09 15:48')
})

test('missing datetimes show a reference without turning it into a saved value', () => {
  const state = buildDateTimePickerState('', '2026-10-09', 'datetime', new Date(2026, 9, 26, 10, 5))
  assert.equal(state.displayValue, '2026-10-09 10:05')
  assert.equal(state.isReference, true)
  assert.match(state.hint, /选择后写入/)
})

test('time-only evidence keeps its value and uses the event date as a reference', () => {
  const state = buildDateTimePickerState('15:48', '2026-10-09', 'datetime', new Date(2026, 9, 26, 10, 5))
  assert.equal(state.displayValue, '2026-10-09 15:48')
  assert.equal(state.isReference, true)
  assert.match(state.hint, /时间已识别/)
})

test('month changes clamp the day wheel and leap years are respected', () => {
  assert.equal(daysInMonth(2024, 2), 29)
  assert.equal(daysInMonth(2025, 2), 28)
  const indexes = buildDateTimePickerState('2024-02-29 23:59').indexes
  assert.equal(dateTimeFromIndexes(indexes), '2024-02-29 23:59')
  assert.equal(dateTimeFromIndexes([35, 1, 30, 8, 0]), '2025-02-28 08:00')
})

test('time-only picker accepts only hours and minutes', () => {
  const state = buildDateTimePickerState('09:07', '', 'time', new Date(2026, 9, 26, 10, 5))
  assert.equal(state.displayValue, '09:07')
  assert.deepEqual(state.indexes, [9, 7])
  assert.equal(dateTimeFromIndexes([17, 32], 'time'), '17:32')
})

test('candidate date-time formats normalize before a conflict choice is saved', () => {
  assert.equal(normalizeDateTimeInput('2026年10月9日15时48分'), '2026-10-09 15:48')
  assert.equal(normalizeDateTimeInput('2026/10/09 15:48:22'), '2026-10-09 15:48')
  assert.equal(normalizeDateTimeInput('15：08'), '15:08')
  assert.equal(normalizeDateTimeInput('15:08:59', 'time'), '15:08')
  assert.equal(normalizeDateTimeInput('2026-02-30 12:00'), null)
  assert.equal(normalizeDateTimeInput('25:00', 'time'), null)
})

test('time-only work form values use the business date and infer overnight end dates', () => {
  const range = resolveDateTimeRange('21:30', '01:15', '2026-10-09', new Date(2026, 9, 26, 10, 5))
  assert.deepEqual(range, {
    start: '2026-10-09T21:30',
    end: '2026-10-10T01:15',
    invalidField: null,
  })
})

test('explicit end dates are kept and malformed values are rejected', () => {
  const explicit = resolveDateTimeRange('2026-10-09 21:30', '2026-10-10 01:15', '2026-10-09')
  assert.equal(explicit.end, '2026-10-10T01:15')
  assert.equal(explicit.invalidField, null)
  const inferredFromStart = resolveDateTimeRange('2026-10-09 21:30', '01:15', '2026-10-08')
  assert.equal(inferredFromStart.end, '2026-10-10T01:15')
  assert.equal(resolveDateTimeRange('bad time', '', '2026-10-09').invalidField, 'start')
  assert.equal(resolveDateTimeRange('21:30', 'bad time', '2026-10-09').invalidField, 'end')
})
