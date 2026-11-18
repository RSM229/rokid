import { request } from '../../services/request'
import { session } from '../../services/session'
import { getCached, CACHE_KEYS } from '../../services/cache'
import { getTheme, previewColor, themed } from '../../utils/theme'

interface RecordStat {
  unviewed: number
  pending: number
}

interface TodoStats {
  pending_receive?: number
  in_progress?: number
  completed?: number
  total_received?: number
}

interface TrendPoint {
  date: string
  inspection_count: number
  hazard_count: number
}

interface CategoryItem {
  category: string
  name: string
  count: number
}

interface Kpi {
  label: string
  value: number
  unit: string
  color: string
  pct: number
  deltaText: string
  showDelta: boolean
}

interface Ring {
  label: string
  pct: number
  desc: string
  color: string
  style: string
}

interface TrendBar {
  label: string
  inspection: number
  hazard: number
  hpx: number
}

interface TrendPointXY {
  k: string
  x: number
  y: number
}

interface TrendSeg {
  k: string
  x: number
  y: number
  w: number
  a: number
}

interface AxisTick {
  label: string
  pct: number
}

/** 语义色：隐患=危险红、待办=警示橙（不随主题变） */
const HAZARD_COLOR = '#b93838'
const WARN_COLOR = '#d97706'
/** 折线绘图区顶部留白（px），避免峰值贴顶 */
const PLOT_PAD = 20

/** 图表调色板：首位跟随用户主题色，其余为区分/语义色 */
function barColors(): string[] {
  return [previewColor(getTheme()), '#d97706', '#0284c7', '#7c3aed', '#b93838']
}

/** 环形进度样式：已完成部分上色，剩余部分留浅底 */
function ringStyle(color: string, pct: number): string {
  const deg = Math.max(0, Math.min(100, pct)) * 3.6
  return `conic-gradient(${color} 0deg ${deg.toFixed(1)}deg, rgba(226,236,248,1) 0deg 360deg)`
}

/** 由分子分母算百分比（分母 <= 0 时返回 0） */
function ratioPct(part: number, whole: number): number {
  if (!whole || whole <= 0) return 0
  return Math.round(Math.max(0, Math.min(1, part / whole)) * 100)
}

/** 环比文案：较前一日 ↑/↓ x% */
function deltaText(delta: number | null): string {
  if (delta === null) return '—'
  if (delta === 0) return '较前日 持平'
  return `较前日 ${delta > 0 ? '↑' : '↓'} ${Math.abs(delta)}%`
}

Page(themed({
  data: {
    updateText: '',
    kpis: [] as Kpi[],
    rings: [] as Ring[],
    showRings: false,
    trendBars: [] as TrendBar[],
    showTrend: false,
    yTicksL: [] as AxisTick[],
    yTicksR: [] as AxisTick[],
    trendSegs: [] as TrendSeg[],
    trendPoints: [] as TrendPointXY[],
    workBars: [] as { label: string; count: number; pct: number; color: string }[],
    workTotal: 0,
    donutStyle: '',
    donutTotal: 0,
    showDonut: false,
    categories: [] as { name: string; count: number; pct: number; color: string }[],
    loading: false,
    // ⑥ 项目成员（2026-11-18）：名单全员可见，邀请仅 member_manage 持有者
    members: [] as Array<{ user_id: number; real_name: string; role_label: string; extra_text: string; is_present: boolean }>,
    canManageMembers: false,
    // 邀请弹层
    showInvite: false,
    inviteRoles: ['监理员', '专业监理工程师', '安全监理员', '资料员', '见证员', '总监代表'],
    inviteDayOptions: [7, 30, 90],
    inviteRole: '监理员',
    inviteDays: 7,
    inviteCode: '',
    inviteCreating: false,
  },
  onShow() {
    if (!session.getToken()) {
      wx.reLaunch({ url: '/pages/login/index' })
      return
    }
    this.load()
    this.loadMembers()
  },
  onPullDownRefresh() {
    this.load().finally(() => wx.stopPullDownRefresh())
  },

  /** ⑥ 项目成员名单 + 本人是否可发邀请码（member_manage） */
  async loadMembers() {
    const user = session.getUser() as { project_id?: number; permissions?: string[] } | null
    const pid = user?.project_id
    if (!pid) {
      this.setData({ members: [], canManageMembers: false })
      return
    }
    this.setData({ canManageMembers: (user?.permissions || []).includes('member_manage') })
    try {
      const res = await request<{ items: Array<{
        user_id: number; real_name: string; role_label: string
        extra_roles: string[]; is_present: boolean
      }> }>(`/project/${pid}/members`)
      const items = res.items || []
      // 岗位 label 由后端出（role_label），附加岗拼进副行
      this.setData({
        members: items.map(m => ({
          user_id: m.user_id,
          real_name: m.real_name || '未命名',
          role_label: m.role_label || '成员',
          extra_text: (m.extra_roles && m.extra_roles.length) ? ` · 兼 ${m.extra_roles.length} 岗` : '',
          is_present: m.is_present !== false,
        })),
      })
    } catch {
      // 名单拉不到不影响看板其它卡片
      this.setData({ members: [] })
    }
  },

  openInvite() {
    this.setData({ showInvite: true, inviteCode: '' })
  },

  closeInvite() {
    this.setData({ showInvite: false })
  },

  pickInviteRole(event: WechatMiniprogram.TouchEvent) {
    this.setData({ inviteRole: String(event.currentTarget.dataset.role || '') })
  },

  pickInviteDays(event: WechatMiniprogram.TouchEvent) {
    this.setData({ inviteDays: Number(event.currentTarget.dataset.days || 7) })
  },

  /** 中文岗位名 → 后端英文 key（与 UserRole 枚举对应） */
  async generateInviteCode() {
    if (this.data.inviteCreating) return
    const roleMap: Record<string, string> = {
      '监理员': 'inspector',
      '专业监理工程师': 'special_supervisor',
      '安全监理员': 'safety_supervisor',
      '资料员': 'documenter',
      '见证员': 'witness',
      '总监代表': 'deputy_chief',
    }
    const roleKey = roleMap[this.data.inviteRole] || 'inspector'
    const user = session.getUser() as { project_id?: number } | null
    if (!user?.project_id) {
      wx.showToast({ title: '未加入项目，无法邀请', icon: 'none' })
      return
    }
    this.setData({ inviteCreating: true })
    try {
      const res = await request<{ code: string; expires_at?: string | null }>(
        `/auth/projects/${user.project_id}/invite-codes`, {
          method: 'POST',
          data: { role: roleKey, level: 'personal', max_uses: 1, expires_in_days: this.data.inviteDays },
        },
      )
      this.setData({ inviteCode: res.code })
    } catch (error) {
      wx.showToast({ title: (error as Error).message, icon: 'none' })
    } finally {
      this.setData({ inviteCreating: false })
    }
  },

  copyInviteCode() {
    if (!this.data.inviteCode) return
    wx.setClipboardData({
      data: this.data.inviteCode,
      success: () => wx.showToast({ title: '已复制，发给同事即可', icon: 'none' }),
    })
  },
  safe<T>(promise: Promise<T>): Promise<T | null> {
    return promise.catch(() => null)
  },
  async load() {
    this.setData({ loading: true })
    try {
      const [tasks, records, inspStats, trend, cats, progLatest] = await Promise.all([
        this.safe(getCached<TodoStats>(
          CACHE_KEYS.TASK_TODO_STATS,
          () => request<TodoStats>('/task/todo-stats'),
        )),
        this.safe(getCached<{
          work_records: Record<string, RecordStat>
          hazards: { pending: number }
        }>(
          CACHE_KEYS.WORK_RECORD_STATS,
          () => request<{
            work_records: Record<string, RecordStat>
            hazards: { pending: number }
          }>('/work-record/stats'),
        )),
        this.safe(request<{
          today_inspection_count: number
          today_hazard_count: number
          total_inspection_count: number
          total_hazard_count: number
          active_session_count: number
        }>('/inspection/stats')),
        this.safe(request<TrendPoint[]>('/inspection/trend?days=7')),
        this.safe(request<CategoryItem[]>('/inspection/category-distribution')),
        this.safe(request<{
          actual_percent: number | null
          plan_percent: number | null
          record_date: string
          part: string
        }>('/progress/latest')),
      ])

      const brand = previewColor(getTheme())
      // 防御：接口可能返回非数组（空对象 / 包裹结构），一旦直接 .map/.reduce 会抛错，
      // 导致整个 setData 被跳过、页面只剩静态卡片（曾出现过整页空白）。只接受数组。
      const trendList = Array.isArray(trend) ? trend : []
      const catList = Array.isArray(cats) ? cats : []

      // ---------- ① 核心指标（今日巡检 / 今日隐患 / 待接任务 / 待处理隐患） ----------
      const lastTrend = trendList.length ? trendList[trendList.length - 1] : null
      const prevTrend = trendList.length > 1 ? trendList[trendList.length - 2] : null
      const todayInsp = Number(inspStats?.today_inspection_count || lastTrend?.inspection_count || 0)
      const todayHazard = Number(inspStats?.today_hazard_count || 0)
      const pendingTask = Number(tasks?.pending_receive || 0)
      const pendingHazard = Number(records?.hazards?.pending || 0)

      const pctChange = (cur: number, prev: number): number | null => {
        if (!prev || prev <= 0) return null
        return Math.round(((cur - prev) / prev) * 100)
      }
      const inspDelta = prevTrend ? pctChange(todayInsp, Number(prevTrend.inspection_count || 0)) : null
      const hazardDelta = prevTrend
        ? pctChange(Number(lastTrend?.hazard_count || 0), Number(prevTrend.hazard_count || 0))
        : null

      const kpiBase = [
        { label: '今日巡检', value: todayInsp, unit: '次', color: brand, delta: inspDelta },
        { label: '今日隐患', value: todayHazard, unit: '条', color: HAZARD_COLOR, delta: hazardDelta },
        { label: '待接任务', value: pendingTask, unit: '个', color: WARN_COLOR, delta: null },
        { label: '待处理隐患', value: pendingHazard, unit: '条', color: HAZARD_COLOR, delta: null },
      ]
      const kpiMax = Math.max(1, ...kpiBase.map((k) => k.value))
      const kpis: Kpi[] = kpiBase.map((k) => ({
        label: k.label,
        value: k.value,
        unit: k.unit,
        color: k.color,
        pct: Math.max(4, Math.round((k.value / kpiMax) * 100)),
        showDelta: k.delta !== null,
        deltaText: deltaText(k.delta),
      }))

      // ---------- ② 处置进度环（任务完成率 / 隐患整改率） ----------
      const taskTotal = Number(tasks?.total_received || 0)
      const taskDone = Number(tasks?.completed || 0)
      const hazardTotal = Number(inspStats?.total_hazard_count || 0)
      const hazardDone = Math.max(0, hazardTotal - pendingHazard)
      const taskPct = ratioPct(taskDone, taskTotal)
      const hazardDonePct = ratioPct(hazardDone, hazardTotal)
      const rings: Ring[] = [
        { label: '任务完成率', pct: taskPct, desc: `${taskDone} / ${taskTotal}`, color: brand, style: ringStyle(brand, taskPct) },
        { label: '隐患整改率', pct: hazardDonePct, desc: `${hazardDone} / ${hazardTotal}`, color: HAZARD_COLOR, style: ringStyle(HAZARD_COLOR, hazardDonePct) },
      ]
      // P3.4 工程进度环：数据源 = 进度记录模块最新一条（无记录时不占坑）
      const progActual = progLatest?.actual_percent
      const hasProgress = progActual !== null && progActual !== undefined
      if (hasProgress) {
        const progColor = '#0284c7'
        const planTxt = progLatest?.plan_percent !== null && progLatest?.plan_percent !== undefined
          ? `计划 ${progLatest?.plan_percent}%`
          : ''
        rings.push({
          label: '工程进度',
          pct: Math.max(0, Math.min(100, Number(progActual))),
          desc: planTxt || (progLatest?.record_date || ''),
          color: progColor,
          style: ringStyle(progColor, Number(progActual)),
        })
      }

      // ---------- ③ 近 7 日趋势（巡检柱 + 隐患折线，双轴） ----------
      const trendData = trendList.map((t) => {
        const [, m, d] = String(t.date || '').split('-')
        return {
          label: `${m || ''}/${d || ''}`,
          inspection: Number(t.inspection_count || 0),
          hazard: Number(t.hazard_count || 0),
          hpx: 0,
        }
      })
      const inspMax = Math.max(1, ...trendData.map((t) => t.inspection))
      const hazardMax = Math.max(1, ...trendData.map((t) => t.hazard))

      // ---------- ④ 隐患类别分布（环形） ----------
      const total = catList.reduce((sum, item) => sum + Number(item.count || 0), 0)
      const catData = catList.map((item, i) => {
        const count = Number(item.count || 0)
        return {
          name: item.name || item.category || '未分类',
          count,
          pct: total > 0 ? Math.round((count / total) * 100) : 0,
          color: barColors()[i % barColors().length],
        }
      })
      let deg = 0
      const segments = catData.map((item) => {
        const span = total > 0 ? (item.count / total) * 360 : 0
        const segment = `${item.color} ${deg.toFixed(1)}deg ${(deg + span).toFixed(1)}deg`
        deg += span
        return segment
      })
      const donutStyle = segments.length ? `conic-gradient(${segments.join(', ')})` : ''

      // ---------- ⑤ 工作记录待办构成 ----------
      const typeLabels = ['巡视', '旁站', '验收', '见证', '其他']
      const typeLevels = typeLabels.map((name, i) => {
        const count = Number(records?.work_records?.[name]?.pending || 0)
        return { label: name, count, color: barColors()[i % barColors().length] }
      })
      const workTotal = typeLevels.reduce((sum, item) => sum + item.count, 0)
      const workMax = Math.max(1, ...typeLevels.map((item) => item.count))
      const workBars = typeLevels.map((item) => ({
        ...item,
        pct: Math.max(1, Math.round((item.count / workMax) * 100)),
      }))

      const now = new Date()
      const hh = `0${now.getHours()}`.slice(-2)
      const mm = `0${now.getMinutes()}`.slice(-2)

      this.setData({
        updateText: `数据更新于 ${hh}:${mm}`,
        kpis,
        rings,
        showRings: taskTotal > 0 || hazardTotal > 0 || hasProgress,
        trendBars: trendData,
        showTrend: trendData.length > 0,
        yTicksL: [
          { label: String(inspMax), pct: 100 },
          { label: String(Math.round(inspMax / 2)), pct: 50 },
          { label: '0', pct: 0 },
        ],
        yTicksR: [
          { label: String(hazardMax), pct: 100 },
          { label: String(Math.round(hazardMax / 2)), pct: 50 },
          { label: '0', pct: 0 },
        ],
        workBars,
        workTotal,
        donutStyle,
        donutTotal: total,
        showDonut: catData.length > 0,
        categories: catData,
      })
    } catch (error) {
      wx.showToast({ title: (error as Error).message, icon: 'none' })
    } finally {
      this.setData({ loading: false })
      wx.nextTick(() => this.buildTrendChart())
    }
  },
  /**
   * 折线需要像素级坐标，因此在渲染完成后量取绘图区尺寸再计算。
   * 拿不到尺寸时退化为纯柱状（hpx 保持 0，折线层为空），不影响数据展示。
   */
  buildTrendChart() {
    const list = this.data.trendBars
    if (!list.length) {
      this.setData({ trendSegs: [], trendPoints: [] })
      return
    }
    const inspMax = Math.max(1, ...list.map((i) => i.inspection))
    const hazardMax = Math.max(1, ...list.map((i) => i.hazard))
    wx.createSelectorQuery()
      .select('#trendPlot')
      .boundingClientRect((rect) => {
        const box = rect as WechatMiniprogram.BoundingClientRectCallbackResult | null
        const width = Number(box?.width || 0)
        const height = Number(box?.height || 0)
        if (!width || !height) return
        const usable = Math.max(12, height - PLOT_PAD)
        const count = list.length
        const bars: TrendBar[] = list.map((item) => ({
          ...item,
          hpx: Math.max(3, (item.inspection / inspMax) * usable),
        }))
        const points: TrendPointXY[] = list.map((item, index) => ({
          k: `p${index}`,
          x: ((index + 0.5) / count) * width,
          y: height - (item.hazard / hazardMax) * usable,
        }))
        const segs: TrendSeg[] = []
        for (let i = 0; i < points.length - 1; i += 1) {
          const dx = points[i + 1].x - points[i].x
          const dy = points[i + 1].y - points[i].y
          segs.push({
            k: `s${i}`,
            x: points[i].x,
            y: points[i].y,
            w: Math.sqrt(dx * dx + dy * dy),
            a: (Math.atan2(dy, dx) * 180) / Math.PI,
          })
        }
        this.setData({ trendBars: bars, trendPoints: points, trendSegs: segs })
      })
      .exec()
  },
  open(event: WechatMiniprogram.TouchEvent) {
    wx.navigateTo({ url: event.currentTarget.dataset.url as string })
  },
}))
