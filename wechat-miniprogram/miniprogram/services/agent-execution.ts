import { ApiRequestError, request } from './request'

export interface AgentExecutionResponse {
  execution_id: string
  request_id?: string
  flow_type: string
  scope_id: string
  expected_revision?: number | null
  status: 'RUNNING' | 'COMMITTED' | 'FAILED' | string
  stage: string
  attempts: number
  result: Record<string, any>
  error: string
  started_at?: string | null
  updated_at?: string | null
  finished_at?: string | null
}

export function newOperationId(prefix: string): string {
  return `${prefix}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`
}

export function isExecutionRecoverable(error: unknown): boolean {
  if (error instanceof ApiRequestError) {
    return error.networkError || error.timedOut || Number(error.statusCode || 0) >= 500
  }
  return /timeout|网络|连不上|请求失败/i.test((error as Error)?.message || '')
}

export async function fetchAgentExecution(executionId: string): Promise<AgentExecutionResponse> {
  return request<AgentExecutionResponse>(
    `/agent/executions/${encodeURIComponent(executionId)}`,
    { timeout: 10000 },
  )
}

/** Poll a durable execution after a timeout or dropped response. */
export async function waitForAgentExecution(executionId: string): Promise<AgentExecutionResponse | null> {
  const delays = [0, 800, 1600, 3000]
  let latest: AgentExecutionResponse | null = null
  for (const delay of delays) {
    if (delay) await new Promise(resolve => setTimeout(resolve, delay))
    try {
      latest = await fetchAgentExecution(executionId)
      if (latest.status === 'COMMITTED' || latest.status === 'FAILED') return latest
    } catch (error) {
      if (error instanceof ApiRequestError && error.statusCode === 404) return null
    }
  }
  return latest
}
