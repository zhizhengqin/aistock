import client from './client'

export type Freshness = 'fresh' | 'stale'

export interface CredentialField {
  key: string
  label: string
  secret: boolean
  required: boolean
  help: string
}

export interface DataSource {
  id: string | null
  provider: string
  display_name: string
  description: string
  capabilities: string[]
  auth_type: string
  credential_fields: CredentialField[]
  fee_type: string
  update_frequency: string
  risk_note: string
  available?: boolean
  unavailable_reason?: string | null
  enabled: boolean
  version: number
  key_hint: string | null
  fingerprint: string | null
  last_probe_status: string | null
  last_probe_at: string | null
  last_probe_latency_ms: number | null
}

export interface DataSourceList { items: DataSource[] }
export interface DataSourceRoute {
  capability: string
  mode: 'auto' | 'fixed'
  providers: string[]
  contract_version: string
  version: number
  provider_options?: { provider: string; display_name: string; available: boolean; unavailable_reason?: string | null; enabled: boolean; selectable?: boolean }[]
}

export const listDataSources = async (): Promise<DataSourceList> =>
  (await client.get('/admin/data-sources')).data.data

export const saveDataSource = async (payload: {
  provider: string
  public_config?: Record<string, unknown>
  credentials?: Record<string, string>
  expected_version?: number | null
}): Promise<DataSource> => (await client.post('/admin/data-sources', payload)).data.data

export const patchDataSource = async (provider: string, payload: {
  public_config?: Record<string, unknown>
  credentials?: Record<string, string>
  expected_version: number
}): Promise<DataSource> => (await client.patch(`/admin/data-sources/${provider}`, payload)).data.data

export const testDataSource = async (payload: {
  provider: string
  public_config?: Record<string, unknown>
  credentials?: Record<string, string>
}): Promise<Record<string, unknown>> => (await client.post('/admin/data-sources/test', payload)).data.data

export const testSavedDataSource = async (provider: string, capability?: string): Promise<Record<string, unknown>> =>
  (await client.post(`/admin/data-sources/${provider}/test`, null, { params: capability ? { capability } : undefined })).data.data

export const setDataSourceEnabled = async (provider: string, enabled: boolean, version: number): Promise<DataSource> =>
  (await client.post(`/admin/data-sources/${provider}/${enabled ? 'enable' : 'disable'}`, null, { params: { expected_version: version } })).data.data

export const listDataSourceRoutes = async (): Promise<{ items: DataSourceRoute[] }> =>
  (await client.get('/admin/data-source-routes')).data.data

export const saveDataSourceRoute = async (capability: string, payload: {
  mode: 'auto' | 'fixed'
  providers: string[]
  expected_version?: number | null
}): Promise<DataSourceRoute> => (await client.put(`/admin/data-source-routes/${capability}`, payload)).data.data
