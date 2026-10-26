export type DateTimePickerMode = 'datetime' | 'time'

export interface DateTimePickerState {
  ranges: string[][]
  indexes: number[]
  displayValue: string
  hint: string
  isReference: boolean
}

export interface ResolvedDateTimeRange {
  start: string
  end: string
  invalidField: 'start' | 'end' | null
}

const MIN_YEAR = 1990
const MAX_YEAR = 2100
const pad = (value: number) => String(value).padStart(2, '0')

function dateParts(value: string): [number, number, number] | null {
  const match = value.match(/^(\d{4})[-/.年](\d{1,2})[-/.月](\d{1,2})日?$/)
  if (!match) return null
  const year = Number(match[1]), month = Number(match[2]), day = Number(match[3])
  const parsed = new Date(year, month - 1, day)
  return parsed.getFullYear() === year && parsed.getMonth() === month - 1 && parsed.getDate() === day
    ? [year, month, day]
    : null
}

function timeParts(value: string): [number, number] | null {
  const match = value.match(/^(\d{1,2})[:：时点](\d{1,2})分?(?::\d{1,2})?$/)
  if (!match) return null
  const hour = Number(match[1]), minute = Number(match[2])
  return hour < 24 && minute < 60 ? [hour, minute] : null
}

export function normalizeDateTimeInput(rawValue: string, mode: DateTimePickerMode = 'datetime'): string | null {
  const raw = String(rawValue || '').trim()
  const timeOnly = timeParts(raw)
  if (mode === 'time') return timeOnly ? `${pad(timeOnly[0])}:${pad(timeOnly[1])}` : null
  if (timeOnly) return `${pad(timeOnly[0])}:${pad(timeOnly[1])}`
  const fullMatch = raw.match(/^(\d{4}[-/.年]\d{1,2}[-/.月]\d{1,2}日?)[T\s]*(\d{1,2}[:：时点]\d{1,2}分?(?::\d{1,2})?)$/)
  if (!fullMatch) return null
  const parsedDate = dateParts(fullMatch[1].replace(/年/g, '-').replace(/月/g, '-').replace(/日/g, '').trim())
  const parsedTime = timeParts(fullMatch[2].replace(/:(\d{2}):\d{2}$/, ':$1'))
  if (!parsedDate || !parsedTime) return null
  return `${parsedDate[0]}-${pad(parsedDate[1])}-${pad(parsedDate[2])} ${pad(parsedTime[0])}:${pad(parsedTime[1])}`
}

export function resolveDateTimeRange(
  startValue: string,
  endValue: string,
  referenceDate = '',
  now = new Date(),
): ResolvedDateTimeRange {
  const referenceParts = dateParts(referenceDate) || [now.getFullYear(), now.getMonth() + 1, now.getDate()]
  const reference = `${referenceParts[0]}-${pad(referenceParts[1])}-${pad(referenceParts[2])}`
  const resolve = (value: string, baseDate = reference): { value: string; timeOnly: boolean } | null | false => {
    if (!String(value || '').trim()) return null
    const normalized = normalizeDateTimeInput(value, 'datetime')
    if (!normalized) return false
    if (/^\d{2}:\d{2}$/.test(normalized)) return { value: `${baseDate}T${normalized}`, timeOnly: true }
    return { value: normalized.replace(' ', 'T'), timeOnly: false }
  }

  const start = resolve(startValue)
  if (start === false) return { start: '', end: '', invalidField: 'start' }
  const end = resolve(endValue, start?.value.slice(0, 10) || reference)
  if (end === false) return { start: '', end: '', invalidField: 'end' }

  if (start && end?.timeOnly && end.value.slice(11, 16) < start.value.slice(11, 16)) {
    const [year, month, day] = start.value.slice(0, 10).split('-').map(Number)
    const nextDate = new Date(year, month - 1, day + 1)
    end.value = `${nextDate.getFullYear()}-${pad(nextDate.getMonth() + 1)}-${pad(nextDate.getDate())}T${end.value.slice(11, 16)}`
  }

  return { start: start?.value || '', end: end?.value || '', invalidField: null }
}

export function daysInMonth(year: number, month: number): number {
  return new Date(year, month, 0).getDate()
}

export function buildDateTimePickerState(
  rawValue: string,
  referenceDate = '',
  mode: DateTimePickerMode = 'datetime',
  now = new Date(),
  showReference = true,
): DateTimePickerState {
  const raw = String(rawValue || '').trim()
  const referenceParts = dateParts(referenceDate)
  const fullMatch = raw.match(/^(.+?)[T\s](\d{1,2}[:：时点]\d{1,2}分?(?::\d{1,2})?)$/)
  const parsedDate = fullMatch ? dateParts(fullMatch[1].replace(/年/g, '-').replace(/月/g, '-').replace(/日/g, '').trim()) : dateParts(raw)
  const parsedTime = fullMatch
    ? timeParts(fullMatch[2].replace(/:(\d{2}):\d{2}$/, ':$1'))
    : timeParts(raw)

  const year = parsedDate?.[0] || referenceParts?.[0] || now.getFullYear()
  const month = parsedDate?.[1] || referenceParts?.[1] || now.getMonth() + 1
  const day = parsedDate?.[2] || referenceParts?.[2] || now.getDate()
  const hour = parsedTime?.[0] ?? now.getHours()
  const minute = parsedTime?.[1] ?? now.getMinutes()
  const yearClamped = Math.min(MAX_YEAR, Math.max(MIN_YEAR, year))
  const dayClamped = Math.min(day, daysInMonth(yearClamped, month))
  const ranges = mode === 'time'
    ? [Array.from({ length: 24 }, (_, index) => `${pad(index)}时`), Array.from({ length: 60 }, (_, index) => `${pad(index)}分`)]
    : [
        Array.from({ length: MAX_YEAR - MIN_YEAR + 1 }, (_, index) => `${MIN_YEAR + index}年`),
        Array.from({ length: 12 }, (_, index) => `${pad(index + 1)}月`),
        Array.from({ length: daysInMonth(yearClamped, month) }, (_, index) => `${pad(index + 1)}日`),
        Array.from({ length: 24 }, (_, index) => `${pad(index)}时`),
        Array.from({ length: 60 }, (_, index) => `${pad(index)}分`),
      ]
  const indexes = mode === 'time'
    ? [hour, minute]
    : [yearClamped - MIN_YEAR, month - 1, dayClamped - 1, hour, minute]
  const isReference = mode === 'time'
    ? !parsedTime
    : !fullMatch || !parsedDate || !parsedTime
  const displayValue = !raw && !showReference
    ? ''
    : mode === 'time'
    ? `${pad(hour)}:${pad(minute)}`
    : `${yearClamped}-${pad(month)}-${pad(dayClamped)} ${pad(hour)}:${pad(minute)}`
  const hint = !raw && !showReference
    ? ''
    : mode === 'time'
    ? (parsedTime ? '' : '参考时间，选择后写入')
    : fullMatch && parsedDate && parsedTime
      ? ''
      : parsedTime && !parsedDate
        ? '时间已识别，日期为参考值'
        : parsedDate && !parsedTime
          ? '日期已识别，时间为参考值'
          : '参考时间，选择后写入'
  return { ranges, indexes, displayValue, hint, isReference }
}

export function dateTimeFromIndexes(indexes: number[], mode: DateTimePickerMode = 'datetime'): string {
  if (mode === 'time') return `${pad(Number(indexes[0] || 0))}:${pad(Number(indexes[1] || 0))}`
  const year = MIN_YEAR + Number(indexes[0] || 0)
  const month = Number(indexes[1] || 0) + 1
  const day = Math.min(Number(indexes[2] || 0) + 1, daysInMonth(year, month))
  return `${year}-${pad(month)}-${pad(day)} ${pad(Number(indexes[3] || 0))}:${pad(Number(indexes[4] || 0))}`
}
