/**
 * 统一模型目录（前端"选模型"共用）。
 *
 * 字段说明：
 *  - id          选择键（唯一）
 *  - provider    后端 provider 键（''=默认文本端点；'huagong'=华南理工独立供应商）
 *  - providerName 展示用供应商名（截图左侧那列，如 DeepSeek / 通义千问）
 *  - model       传给后端的 model id（''=沿用系统默认模型）
 *  - name        友好名称
 *  - desc        一句话简介
 *  - temperature 该模型的推荐温度
 *  - badges      角标（截图里的 New / 限时夜间5折 之类）
 *  - capabilities 能力标签（截图右侧：文本生成、推理模型、视觉理解…）
 *
 * ⚠️ 只放后端实际支持的模型。默认端点是阿里云 token-plan，只认通义千问系列；
 * 华南理工走独立 provider。想加 DeepSeek 等，须先确认后端 model_gateway 已配置对应
 * provider / 端点，否则选了会生成失败。
 */
export interface ModelItem {
  id: string
  provider: string
  providerName: string
  model: string
  name: string
  desc: string
  temperature: number
  badges: string[]
  capabilities: string[]
}

export const MODEL_CATALOG: ModelItem[] = [
  {
    id: 'qwen3.7-max',
    provider: '',
    providerName: '通义千问',
    model: 'qwen3.7-max',
    name: '通义千问 3.7 Max',
    desc: '最强推理，复杂规范分析',
    temperature: 0.7,
    badges: ['New'],
    capabilities: ['文本生成', '推理模型', '视觉理解'],
  },
  {
    id: 'qwen3.7-plus',
    provider: '',
    providerName: '通义千问',
    model: 'qwen3.7-plus',
    name: '通义千问 3.7 Plus',
    desc: '均衡通用，日常问答',
    temperature: 0.7,
    badges: [],
    capabilities: ['文本生成', '视觉理解'],
  },
  {
    id: 'huagong',
    provider: 'huagong',
    providerName: '华南理工',
    model: '',
    name: '华工大模型',
    desc: '华南理工，视觉/深度',
    temperature: 0.6,
    badges: [],
    capabilities: ['文本生成', '视觉理解', '深度分析'],
  },
]

export function findModel(id: string): ModelItem {
  return MODEL_CATALOG.find(m => m.id === id) || MODEL_CATALOG[0]
}
