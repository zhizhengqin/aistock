import client from './client'

export type LlmProvider = 'deepseek' | 'kimi' | 'qwen'
export type LlmLifecycle = 'draft' | 'active' | 'disabled' | 'retired'

export interface LlmErrorEnvelope {
  code: string
  message: string
  data: null
  field: string | null
  request_id: string
}

export interface LlmModel {
  id: string
  provider: LlmProvider
  display_name: string
  model_name: string
  base_url: string
  key_hint: string | null
  lifecycle_status: LlmLifecycle
  version: number
  runtime_fingerprint: string
  verified_test_id: string | null
  last_probe_status: string | null
  last_probe_at: string | null
  last_probe_latency_ms: number | null
  input_price_micro_yuan_per_million: number | null
  output_price_micro_yuan_per_million: number | null
  max_output_tokens: number | null
  created_new_version: boolean
  supersedes_id: string | null
  capabilities: {
    can_test: boolean
    can_enable: boolean
    can_disable: boolean
    can_activate: boolean
    can_delete: boolean
  }
}

export interface LlmModelList {
  items: LlmModel[]
  total: number
  page: number
  page_size: number
  default_model_config_id: string | null
  daily_token_limit: number
  budget_locked: boolean
  settings_version: number
}

export interface LlmSettings {
  id: number
  daily_token_limit: number
  budget_locked: boolean
  budget_date: string
  reserved_tokens: number
  settled_tokens: number
  default_model_config_id: string | null
  version: number
  switched_by: number | null
  switched_at: string | null
  audit_event_id?: string
}

export interface LlmUsageItem {
  module: string
  provider: LlmProvider
  model: string
  input_tokens: number
  output_tokens: number
  cost_micro_yuan: number | null
  calls: number
}

export interface LlmUsage {
  days: number
  items: LlmUsageItem[]
  total_calls: number
  total_cost_micro_yuan: number
}

export interface LlmModelCandidate {
  provider: LlmProvider
  display_name: string
  model_name: string
  base_url: string
  api_key: string
  max_output_tokens: number
  input_price_micro_yuan_per_million: number | null
  output_price_micro_yuan_per_million: number | null
}

export type LlmModelPatch = Partial<Omit<LlmModelCandidate, 'api_key'>> & {
  api_key?: string
  expected_version: number
}

export interface LlmProbeResult {
  status: string
  test_run_id?: string
  capabilities?: Record<string, boolean>
  [key: string]: unknown
}

export interface LlmSettingsMutationResult {
  id: number
  daily_token_limit: number
  budget_locked: boolean
  default_model_config_id: string | null
  version: number
  audit_event_id?: string
}

function unwrap<T>(response: { data: { data: T } }): T {
  return response.data.data
}

export async function listLlmModels(): Promise<LlmModelList> {
  return unwrap(await client.get<{ data: LlmModelList }>('/admin/llm-models', { params: { page: 1, page_size: 100 } }))
}

export async function getLlmSettings(): Promise<LlmSettings> {
  return unwrap(await client.get<{ data: LlmSettings }>('/admin/llm-settings'))
}

export async function getLlmUsage(days = 7): Promise<LlmUsage> {
  return unwrap(await client.get<{ data: LlmUsage }>('/admin/llm-usage', { params: { days } }))
}

export async function testUnsavedLlmModel(payload: LlmModelCandidate): Promise<LlmProbeResult> {
  return unwrap(await client.post<{ data: LlmProbeResult }>('/admin/llm-models/test', payload))
}

export async function createLlmModel(payload: LlmModelCandidate): Promise<LlmModel> {
  return unwrap(await client.post<{ data: LlmModel }>('/admin/llm-models', payload))
}

export async function patchLlmModel(configId: string, payload: LlmModelPatch): Promise<LlmModel> {
  return unwrap(await client.patch<{ data: LlmModel }>(`/admin/llm-models/${configId}`, payload))
}

export async function testSavedLlmModel(configId: string): Promise<LlmProbeResult> {
  return unwrap(await client.post<{ data: LlmProbeResult }>(`/admin/llm-models/${configId}/test`))
}

export async function enableLlmModel(configId: string, expectedVersion: number, testRunId?: string): Promise<LlmModel> {
  return unwrap(await client.post<{ data: LlmModel }>(`/admin/llm-models/${configId}/enable`, {
    expected_version: expectedVersion,
    test_run_id: testRunId,
  }))
}

export async function disableLlmModel(configId: string, expectedVersion: number): Promise<LlmModel> {
  return unwrap(await client.post<{ data: LlmModel }>(`/admin/llm-models/${configId}/disable`, {
    expected_version: expectedVersion,
  }))
}

export async function activateLlmModel(configId: string, expectedVersion: number): Promise<LlmModel> {
  const idempotencyKey = globalThis.crypto.randomUUID()
  return unwrap(await client.post<{ data: LlmModel }>(`/admin/llm-models/${configId}/activate`, {
    expected_version: expectedVersion,
    idempotency_key: idempotencyKey,
  }))
}

export async function deleteLlmModel(configId: string): Promise<void> {
  await client.delete(`/admin/llm-models/${configId}`)
}

export async function patchLlmSettings(expectedVersion: number, dailyTokenLimit: number): Promise<LlmSettingsMutationResult> {
  return unwrap(await client.patch<{ data: LlmSettingsMutationResult }>('/admin/llm-settings', {
    expected_version: expectedVersion,
    daily_token_limit: dailyTokenLimit,
  }))
}

export async function unlockLlmSettings(expectedVersion: number, reason: string): Promise<LlmSettingsMutationResult> {
  return unwrap(await client.post<{ data: LlmSettingsMutationResult }>('/admin/llm-settings/unlock', {
    expected_version: expectedVersion,
    reason,
  }))
}
