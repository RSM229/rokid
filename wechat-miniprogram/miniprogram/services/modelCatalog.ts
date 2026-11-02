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
 * ⚠️ 只放后端实际支持的模型。默认端点（provider=''）走阿里云 token-plan 的
 * chat/completions，前端用 model 字段覆盖具体模型名——只要是同一 API 的模型
 * （如通义千问、DeepSeek），填对 model id 即可，无需额外后端配置；
 * 华南理工（provider='huagong'）走独立供应商。
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
    id: 'qwen3.8-flash',
    provider: '',
    providerName: '通义千问',
    model: 'qwen3.8-flash',
    name: '通义千问 3.8 Flash',
    desc: '高速全能，文本/推理/视觉',
    temperature: 0.7,
    badges: ['New'],
    capabilities: ['文本生成', '推理模型', '视觉理解'],
  },
  {
    id: 'deepseek-v4.1-flash-1017',
    provider: '',
    providerName: 'DeepSeek',
    model: 'deepseek-v4.1-flash-1017',
    name: 'DeepSeek V4.1 Flash',
    desc: '高速推理，文本/视觉全能',
    temperature: 0.7,
    badges: ['New', '⚡ 限时夜间 5 折'],
    capabilities: ['文本生成', '推理模型', '视觉理解'],
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
