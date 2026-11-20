/**
 * 统一模型目录（前端"选模型"共用）。
 *
 * 字段说明：
 *  - id          选择键（唯一）
 *  - provider    后端 provider 键（''=默认文本端点，仅此一种；华工无文本端点，不列入）
 *  - providerName 展示用供应商名（截图左侧那列，如 DeepSeek / 通义千问）
 *  - model       传给后端的 model id
 *  - name        友好名称
 *  - shortName   chip 上显示的简短名（避免完整名过长）
 *  - desc        一句话简介
 *  - temperature 该模型的推荐温度
 *  - badges      角标（截图里的 New / 限时夜间5折 之类）
 *  - capabilities 能力标签（截图右侧：文本生成、推理模型、视觉理解…）
 *
 * ⚠️ 只放后端实际支持的模型。所有模型走默认端点（provider=''）= 阿里云 token-plan
 * 的 chat/completions，前端用 model 字段覆盖具体模型名——只要是同一 API 的模型
 * （如通义千问、DeepSeek），填对 model id 即可，无需额外后端配置。
 *
 * 注：安全工识别（隐患识别）不在这份目录里——它由后端 detect_hazard 固定走华工
 * VLM 专用通道，与文本选择器无关（见 model_gateway._hazard_vlm_config）。
 */
export interface ModelItem {
  id: string
  provider: string
  providerName: string
  model: string
  name: string
  shortName: string
  desc: string
  temperature: number
  badges: string[]
  capabilities: string[]
}

export const MODEL_CATALOG: ModelItem[] = [
  {
    id: 'deepseek-v4.1-flash-1017',
    provider: '',
    providerName: 'DeepSeek',
    model: 'deepseek-v4.1-flash-1017',
    name: 'DeepSeek V4.1 Flash',
    shortName: 'DeepSeek V4.1',
    desc: '高速推理，文本/视觉全能（默认推荐）',
    temperature: 0.7,
    badges: ['⚡ 限时夜间 5 折'],
    capabilities: ['文本生成', '推理模型', '视觉理解'],
  },
  {
    id: 'qwen3.7-plus',
    provider: '',
    providerName: '通义千问',
    model: 'qwen3.7-plus',
    name: '通义千问 3.7 Plus',
    shortName: 'Qwen3.7 Plus',
    desc: '智能理解、抽取字段更准',
    temperature: 0.7,
    badges: [],
    capabilities: ['文本生成', '推理模型', '视觉理解'],
  },
  {
    id: 'qwen3.8-flash',
    provider: '',
    providerName: '通义千问',
    model: 'qwen3.8-flash',
    name: '通义千问 3.8 Flash',
    shortName: 'Qwen3.8 Flash',
    desc: '高速全能，省钱首选',
    temperature: 0.7,
    badges: [],
    capabilities: ['文本生成', '推理模型', '视觉理解'],
  },
]

/**
 * 按 id 去重后的目录（选择器一律用这份）。
 *
 * 背景：目录里曾出现两条 id 完全相同的 DeepSeek 记录（只有 desc 差一个后缀），
 * 选择器把两条都渲染出来，用户看到"同一个模型出现两次、都打勾"，像是选错了。
 * 数据已修正；这里再加一层运行时兜底，避免以后重复维护时又漏进来。
 */
export const MODEL_CATALOG_UNIQUE: ModelItem[] = MODEL_CATALOG.filter(
  (item, index, list) => list.findIndex(m => m.id === item.id) === index,
)

export function findModel(id: string): ModelItem {
  return MODEL_CATALOG.find(m => m.id === id) || MODEL_CATALOG[0]
}
