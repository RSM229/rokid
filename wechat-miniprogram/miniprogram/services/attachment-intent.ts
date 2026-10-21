/** True only when the user explicitly asks for site-safety/hazard vision analysis. */
export function needsHazardDetection(note: string): boolean {
  const text = String(note || '').trim()
  if (!text) return false

  const hazardSubject = /(安全隐患|隐患|安全风险|风险点|安全问题|是否安全|安不安全|不安全吗|不安全|危险源|违规|违章)/
  const hazardQuestion = /(有没有问题|有无问题|存在问题|哪里有问题|有什么问题)/
  const asksHazardAction = /(识别|检测|检查|查看|看看|分析|判断|排查|创建|生成|记录|建档|发现)/

  return (
    (hazardSubject.test(text) && asksHazardAction.test(text)) ||
    (hazardQuestion.test(text) && /(图片|照片|现场|施工|作业|这里|这个)/.test(text))
  )
}
