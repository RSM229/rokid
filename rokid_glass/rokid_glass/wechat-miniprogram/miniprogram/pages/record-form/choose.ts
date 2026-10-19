import { themed } from '../../utils/theme'
import { session } from '../../services/session'

interface FormChoice {
  code: string
  name: string
  form_number: string
}

// 11 类现场记录表式 + 安全监理日志，均已完成字段级 schema 对齐
const FORMS: FormChoice[] = [
  { code: 'gd_b_212_pangzhan', name: '旁站记录', form_number: 'GD-B-212' },
  { code: 'gd_b_214_xunshi', name: '巡视记录', form_number: 'GD-B-214' },
  { code: 'gd_b_213_pingxing', name: '平行检查记录', form_number: 'GD-B-213' },
  { code: 'gd_b_215_tongzhi', name: '监理通知单', form_number: 'GD-B-215' },
  { code: 'gdaq_30308_yinhuan', name: '安全隐患整改通知', form_number: 'GDAQ30308' },
  { code: 'acceptance_record', name: '验收记录', form_number: 'YS' },
  { code: 'gd_b_220_rizhi', name: '监理日志', form_number: 'GD-B-220' },
  { code: 'gd_b_221_yuebao', name: '监理月报', form_number: 'GD-B-221' },
  { code: 'gd_b_219_cailiao', name: '材料/构配件/设备台账', form_number: 'GD-B-219' },
  { code: 'gdaq_30306_weida', name: '危大工程巡视记录', form_number: 'GDAQ30306' },
  { code: 'anquan_jianli_rizhi', name: '安全监理日志', form_number: '安全监理日志' },
]

Page(themed({
  data: {
    forms: FORMS,
  },

  onLoad() {
    if (!session.getToken()) {
      wx.reLaunch({ url: '/pages/login/index' })
      return
    }
  },

  openForm(event: WechatMiniprogram.TouchEvent) {
    const code = event.currentTarget.dataset.code
    if (!code) return
    wx.navigateTo({ url: `/pages/record-form/index?code=${code}` })
  },
}))
