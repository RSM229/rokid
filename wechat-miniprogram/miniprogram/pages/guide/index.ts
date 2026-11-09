const STORAGE_KEY = 'gdjl_onboarded_v1'

interface GuideSlide {
  icon: string
  title: string
  desc: string
}

interface HelpSection {
  title: string
  items: string[]
}

const HELP_SECTIONS: HelpSection[] = [
  {
    title: '快速上手',
    items: [
      '登录后用底部「工作台」唤起监理智能体，语音或文字现场问答、口述生成记录。',
      '「工作记录」中选择记录类型（旁站 / 平行检查 / 巡视 / 隐患 / 验收 / 监理日志）开始填报。',
      '点底部「隐患识别」拍照，或从相册一次选多张批量识别；AI 给出风险状态、整改要求与检查依据，识别出的隐患可在隐患列表查看。',
      '拿不准的验收项，点下方「前往规范资料库查验收指引」，看规范要点与必查项。',
      '「我」里管理所在项目、账号与安全设置。',
    ],
  },
  {
    title: '六类记录怎么填',
    items: [
      '旁站记录：对关键部位、关键工序施工全过程跟踪记录。',
      '平行检查：对进场材料、设备、施工放样等进行平行检验实测。',
      '巡视记录：日常巡查发现的问题与处理情况。',
      '隐患通知单：发现安全隐患时下发，跟踪整改闭环。',
      '验收记录：分部分项工程验收，关键节点需对应岗位签字。',
      '监理日志：每日必填；天气与温度可自动获取，未配置坐标时人工补录。',
    ],
  },
  {
    title: '必签与导出',
    items: [
      '关键节点由对应岗位（总监 / 专监等）在 App 内签字确认，不可代签。',
      '记录生成后可导出 PDF / Excel 留档，便于归档与上报。',
    ],
  },
  {
    title: '天气补录',
    items: [
      '项目未填写经纬度时，天气与最高 / 最低温度显示「待补填」。',
      '此时需人工录入当日天气与温度，确保监理日志完整。',
    ],
  },
]

Page({
  data: {
    mode: 'intro' as 'intro' | 'help',
    current: 0,
    slides: [
      { icon: '👋', title: '欢迎使用监理通', desc: '工程监理智能体，把规范、记录、知识装进口袋，现场随问随答。' },
      { icon: '💬', title: '工作台 · 监理智能体', desc: '语音或文字呼叫 AI，现场问答、口述生成工作记录，不熟的操作随时问。' },
      { icon: '📝', title: '工作记录 · 六类填报', desc: '旁站 / 平行检查 / 巡视 / 隐患 / 验收 / 监理日志，按规范填报，关键节点必签。' },
      { icon: '🔍', title: '隐患识别 · 拍照即判', desc: '点底部「隐患识别」拍现场照片，或从相册一次选多张批量识别；AI 给出风险状态、整改要求与检查依据。' },
      { icon: '📚', title: '资料库 · 我', desc: '「资料库」里按项目分类管理资料；「我」里管账号、项目与设置。' },
    ] as GuideSlide[],
    helpSections: HELP_SECTIONS,
  },
  onLoad(query: Record<string, string | undefined> = {}) {
    if (query.mode === 'help') {
      this.setData({ mode: 'help' })
      wx.setNavigationBarTitle({ title: '使用帮助' })
    }
  },
  onSlideChange(e: any) {
    this.setData({ current: e.detail.current })
  },
  next() {
    const cur = this.data.current
    if (cur < this.data.slides.length - 1) {
      this.setData({ current: cur + 1 })
    }
  },
  finish() {
    try {
      wx.setStorageSync(STORAGE_KEY, '1')
    } catch (e) {
      void e
    }
    wx.reLaunch({ url: '/pages/agent/index' })
  },
  goKnowledge() {
    wx.navigateTo({ url: '/pages/knowledge/index' })
  },
  goSettings() {
    wx.navigateTo({ url: '/pages/settings/index' })
  },
})
