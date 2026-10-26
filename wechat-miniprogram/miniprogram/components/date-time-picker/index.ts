import { buildDateTimePickerState, dateTimeFromIndexes, daysInMonth, type DateTimePickerMode } from '../../utils/date-time-picker'

Component({
  properties: {
    value: { type: String, value: '' },
    referenceDate: { type: String, value: '' },
    mode: { type: String, value: 'datetime' },
    referenceEnabled: { type: Boolean, value: true },
    disabled: { type: Boolean, value: false },
  },
  data: {
    ranges: [] as string[][],
    indexes: [] as number[],
    displayValue: '',
    hint: '',
    isReference: true,
  },
  observers: {
    'value, referenceDate, mode, referenceEnabled': function (value: string, referenceDate: string, mode: DateTimePickerMode, referenceEnabled: boolean) {
      this.setData(buildDateTimePickerState(value, referenceDate, mode, new Date(), referenceEnabled))
    },
  },
  methods: {
    onColumnChange(event: WechatMiniprogram.PickerColumnChange) {
      const mode = this.data.mode as DateTimePickerMode
      if (mode === 'time') return
      const indexes = [...(this.data.indexes as number[])]
      indexes[event.detail.column] = event.detail.value
      if (event.detail.column === 0 || event.detail.column === 1) {
        const year = 1990 + indexes[0]
        const month = indexes[1] + 1
        const maxDay = daysInMonth(year, month)
        const maxDayIndex = Math.min(indexes[2], maxDay - 1)
        indexes[2] = maxDayIndex
        this.setData({
          indexes,
          'ranges[2]': Array.from({ length: maxDay }, (_, index) => `${String(index + 1).padStart(2, '0')}日`),
        })
      } else {
        this.setData({ indexes })
      }
    },
    onChange(event: WechatMiniprogram.PickerChange) {
      const indexes = Array.isArray(event.detail.value) ? event.detail.value.map(Number) : []
      const mode = this.data.mode as DateTimePickerMode
      const value = dateTimeFromIndexes(indexes, mode)
      this.setData({ ...buildDateTimePickerState(value, this.data.referenceDate, mode, new Date(), this.data.referenceEnabled as boolean) })
      this.triggerEvent('change', { value })
    },
    adoptReference() {
      if (!this.data.isReference) return
      this.triggerEvent('change', { value: dateTimeFromIndexes(this.data.indexes as number[], this.data.mode as DateTimePickerMode) })
    },
  },
})
