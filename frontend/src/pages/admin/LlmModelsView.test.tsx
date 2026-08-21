import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import LlmModelsView from './LlmModelsView'

const api = vi.hoisted(() => ({
  listLlmModels: vi.fn(),
  getLlmSettings: vi.fn(),
  getLlmUsage: vi.fn(),
  testUnsavedLlmModel: vi.fn(),
  createLlmModel: vi.fn(),
  patchLlmModel: vi.fn(),
  testSavedLlmModel: vi.fn(),
  enableLlmModel: vi.fn(),
  disableLlmModel: vi.fn(),
  activateLlmModel: vi.fn(),
  deleteLlmModel: vi.fn(),
  unlockLlmSettings: vi.fn(),
  patchLlmSettings: vi.fn(),
}))

const http = vi.hoisted(() => ({
  post: vi.fn(),
}))

vi.mock('../../api/llmModels', () => api)
vi.mock('../../api/client', () => ({ default: http }))

const baseModel = {
  id: 'cfg-deepseek',
  provider: 'deepseek',
  display_name: 'DeepSeek 主模型',
  model_name: 'deepseek-chat',
  base_url: 'https://api.deepseek.com/v1',
  key_hint: 'sk-****3557',
  lifecycle_status: 'draft',
  version: 1,
  runtime_fingerprint: 'fingerprint-1',
  verified_test_id: null,
  last_probe_status: null,
  last_probe_at: null,
  last_probe_latency_ms: null,
  input_price_micro_yuan_per_million: null,
  output_price_micro_yuan_per_million: null,
  max_output_tokens: 4096,
  created_new_version: false,
  supersedes_id: null,
  capabilities: {
    can_test: true,
    can_enable: true,
    can_disable: false,
    can_activate: true,
    can_delete: true,
  },
}

const baseList = {
  items: [baseModel],
  total: 1,
  page: 1,
  page_size: 20,
  default_model_config_id: null,
  daily_token_limit: 2_000_000,
  budget_locked: false,
  settings_version: 1,
}

const baseSettings = {
  id: 1,
  daily_token_limit: 2_000_000,
  budget_locked: false,
  budget_date: '2026-08-21',
  reserved_tokens: 0,
  settled_tokens: 0,
  default_model_config_id: null,
  version: 1,
  switched_by: null,
  switched_at: null,
}

const baseUsage = {
  days: 7,
  items: [],
  total_calls: 0,
  total_cost_micro_yuan: 0,
}

function mockLoaded(overrides: Partial<typeof baseList> = {}, settings = baseSettings) {
  api.listLlmModels.mockResolvedValue({ ...baseList, ...overrides })
  api.getLlmSettings.mockResolvedValue(settings)
  api.getLlmUsage.mockResolvedValue(baseUsage)
}

function apiError(status: number, message: string, code = 'llm_config_conflict') {
  return { response: { status, data: { code, message, data: null, field: null } } }
}

beforeEach(() => {
  vi.clearAllMocks()
  mockLoaded()
  api.testUnsavedLlmModel.mockResolvedValue({ status: 'success', test_run_id: 'probe-1', capabilities: { json: true } })
  api.createLlmModel.mockResolvedValue(baseModel)
  api.patchLlmModel.mockResolvedValue({ ...baseModel, version: 2 })
  api.testSavedLlmModel.mockResolvedValue({ status: 'success', test_run_id: 'probe-2' })
  api.enableLlmModel.mockResolvedValue({ ...baseModel, lifecycle_status: 'active', version: 2, verified_test_id: 'probe-2' })
  api.disableLlmModel.mockResolvedValue({ ...baseModel, lifecycle_status: 'disabled', version: 2 })
  api.activateLlmModel.mockResolvedValue({ ...baseModel, lifecycle_status: 'active', version: 2, default_model_config_id: baseModel.id })
  api.deleteLlmModel.mockResolvedValue(undefined)
  api.unlockLlmSettings.mockResolvedValue({ ...baseSettings, budget_locked: false, version: 2 })
  api.patchLlmSettings.mockResolvedValue({ ...baseSettings, version: 2 })
})

describe('LlmModelsView', () => {
  it('shows loading then a truthful empty state', async () => {
    let resolveList: ((value: typeof baseList) => void) | undefined
    api.listLlmModels.mockReturnValue(new Promise((resolve) => { resolveList = resolve }))
    render(<LlmModelsView />)

    expect(screen.getByText('加载中…')).toBeInTheDocument()
    resolveList?.({ ...baseList, items: [], total: 0 })
    expect(await screen.findByText('暂无模型配置')).toBeInTheDocument()
  })

  it('offers all supported providers and never renders a Mock control', async () => {
    mockLoaded({ items: [], total: 0 })
    render(<LlmModelsView />)
    await screen.findByText('暂无模型配置')
    await userEvent.click(screen.getByRole('button', { name: '添加模型' }))

    const provider = screen.getByLabelText('供应商')
    expect(within(provider).getByRole('option', { name: 'DeepSeek' })).toBeInTheDocument()
    expect(within(provider).getByRole('option', { name: 'Kimi' })).toBeInTheDocument()
    expect(within(provider).getByRole('option', { name: 'Qwen' })).toBeInTheDocument()
    expect(screen.queryByText(/Mock/i)).not.toBeInTheDocument()
    expect(screen.getByLabelText('API Key')).toHaveAttribute('type', 'password')
    expect(screen.getByLabelText('API Key')).toHaveValue('')
  })

  it('supports free model ids and base urls for all three provider choices', async () => {
    mockLoaded({ items: [], total: 0 })
    render(<LlmModelsView />)
    await screen.findByText('暂无模型配置')
    await userEvent.click(screen.getByRole('button', { name: '添加模型' }))

    await userEvent.selectOptions(screen.getByLabelText('供应商'), 'kimi')
    await userEvent.type(screen.getByLabelText('模型 ID'), 'moonshot-v1-8k')
    await userEvent.clear(screen.getByLabelText('Base URL'))
    await userEvent.type(screen.getByLabelText('Base URL'), 'https://api.moonshot.cn/v1')
    expect(screen.getByLabelText('模型 ID')).toHaveValue('moonshot-v1-8k')
    expect(screen.getByLabelText('Base URL')).toHaveValue('https://api.moonshot.cn/v1')

    await userEvent.selectOptions(screen.getByLabelText('供应商'), 'qwen')
    await userEvent.clear(screen.getByLabelText('模型 ID'))
    await userEvent.type(screen.getByLabelText('模型 ID'), 'qwen-max-long-context-model-id')
    expect(screen.getByLabelText('模型 ID')).toHaveValue('qwen-max-long-context-model-id')
  })

  it('validates required fields before allowing an unsaved test', async () => {
    mockLoaded({ items: [], total: 0 })
    render(<LlmModelsView />)
    await screen.findByText('暂无模型配置')
    await userEvent.click(screen.getByRole('button', { name: '添加模型' }))
    await userEvent.click(screen.getByRole('button', { name: '测试配置' }))

    expect(await screen.findByText('请填写配置名称')).toBeInTheDocument()
    expect(api.testUnsavedLlmModel).not.toHaveBeenCalled()
  })

  it('tests and saves an unsaved model, then clears the key input', async () => {
    mockLoaded({ items: [], total: 0 })
    render(<LlmModelsView />)
    await screen.findByText('暂无模型配置')
    await userEvent.click(screen.getByRole('button', { name: '添加模型' }))
    await userEvent.type(screen.getByLabelText('配置名称'), 'DeepSeek 新配置')
    await userEvent.type(screen.getByLabelText('模型 ID'), 'deepseek-chat')
    await userEvent.type(screen.getByLabelText('Base URL'), 'https://api.deepseek.com/v1')
    await userEvent.type(screen.getByLabelText('API Key'), 'sk-super-secret')

    await userEvent.click(screen.getByRole('button', { name: '测试配置' }))
    await waitFor(() => expect(api.testUnsavedLlmModel).toHaveBeenCalledWith(expect.objectContaining({ api_key: 'sk-super-secret' })))
    expect(screen.getByLabelText('API Key')).toHaveValue('')
    expect(screen.getByText('测试完成，请重新输入 API Key 后保存')).toBeInTheDocument()
    expect(screen.queryByText('sk-super-secret')).not.toBeInTheDocument()

    await userEvent.type(screen.getByLabelText('API Key'), 'sk-resubmitted')
    await userEvent.click(screen.getByRole('button', { name: '保存配置' }))
    await waitFor(() => expect(api.createLlmModel).toHaveBeenCalledWith(expect.objectContaining({ display_name: 'DeepSeek 新配置', api_key: 'sk-resubmitted' })))
  })

  it('keeps edit key blank and renders only the redacted hint', async () => {
    mockLoaded()
    render(<LlmModelsView />)
    await screen.findByText('DeepSeek 主模型')
    expect(screen.getByText(/sk-\*\*\*\*3557/)).toBeInTheDocument()
    expect(screen.queryByText('sk-super-secret')).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: '编辑' }))
    expect(screen.getByLabelText('API Key')).toHaveValue('')
    expect(document.body.textContent).not.toContain('sk-super-secret')
    expect(screen.getByText(/当前密钥/)).toHaveTextContent('sk-****3557')
    await userEvent.click(screen.getByRole('button', { name: '保存配置' }))
    await waitFor(() => expect(api.patchLlmModel).toHaveBeenCalledWith(baseModel.id, expect.objectContaining({ api_key: '' })))
  })

  it('does not submit duplicate actions while a save is pending', async () => {
    let resolveCreate: ((value: typeof baseModel) => void) | undefined
    api.createLlmModel.mockReturnValue(new Promise((resolve) => { resolveCreate = resolve }))
    mockLoaded({ items: [], total: 0 })
    render(<LlmModelsView />)
    await screen.findByText('暂无模型配置')
    await userEvent.click(screen.getByRole('button', { name: '添加模型' }))
    await userEvent.type(screen.getByLabelText('配置名称'), '待保存配置')
    await userEvent.type(screen.getByLabelText('模型 ID'), 'custom-model')
    await userEvent.type(screen.getByLabelText('Base URL'), 'https://example.com/v1')
    await userEvent.type(screen.getByLabelText('API Key'), 'sk-once')
    const save = screen.getByRole('button', { name: '保存配置' })
    await userEvent.click(save)
    await userEvent.click(save)
    expect(api.createLlmModel).toHaveBeenCalledTimes(1)
    expect(save).toBeDisabled()
    resolveCreate?.(baseModel)
  })

  it('conflict errors stay Chinese and trigger a list refresh', async () => {
    api.patchLlmModel.mockRejectedValueOnce(apiError(409, '配置已被其他管理员修改，请刷新后重试'))
    mockLoaded()
    render(<LlmModelsView />)
    await screen.findByText('DeepSeek 主模型')
    await userEvent.click(screen.getByRole('button', { name: '编辑' }))
    await userEvent.clear(screen.getByLabelText('配置名称'))
    await userEvent.type(screen.getByLabelText('配置名称'), '冲突版本')
    await userEvent.click(screen.getByRole('button', { name: '保存配置' }))

    expect(await screen.findByText('配置已被其他管理员修改，请刷新后重试')).toBeInTheDocument()
    expect(api.listLlmModels).toHaveBeenCalledTimes(2)
    expect(api.getLlmSettings).toHaveBeenCalledTimes(2)
  })

  it('shows unknown prices instead of inventing zero', async () => {
    mockLoaded()
    api.getLlmUsage.mockResolvedValue({
      ...baseUsage,
      total_cost_micro_yuan: null,
      items: [{ date: '2026-08-20', module: 'stock.analysis', provider: 'deepseek', model: 'deepseek-chat', model_config_id: 'cfg-deepseek', input_tokens: 12, output_tokens: 8, cost_micro_yuan: null, calls: 1 }],
    })
    render(<LlmModelsView />)
    await screen.findByText('DeepSeek 主模型')
    expect((await screen.findAllByText(/价格未知/)).length).toBeGreaterThanOrEqual(2)
    expect(screen.getByText(/部分费用未配置/)).toBeInTheDocument()
    expect(screen.getByText('2026-08-20')).toBeInTheDocument()
    expect(screen.queryByText(/¥0\.0000/)).not.toBeInTheDocument()
  })

  it('runs named model actions with confirmation and refreshes after each', async () => {
    const user = userEvent.setup()
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true)
    mockLoaded({
      items: [
        { ...baseModel, id: 'cfg-draft', capabilities: { ...baseModel.capabilities } },
        { ...baseModel, id: 'cfg-active', display_name: 'Kimi 活跃模型', lifecycle_status: 'active', capabilities: { ...baseModel.capabilities, can_enable: false, can_disable: true, can_delete: false } },
      ],
    })
    render(<LlmModelsView />)
    await screen.findByText('DeepSeek 主模型')

    const draft = within(screen.getByTestId('llm-model-cfg-draft'))
    const active = within(screen.getByTestId('llm-model-cfg-active'))
    await user.click(draft.getByRole('button', { name: '测试' }))
    await waitFor(() => expect(api.testSavedLlmModel).toHaveBeenCalledWith('cfg-draft'))
    await user.click(draft.getByRole('button', { name: '启用' }))
    await user.click(active.getByRole('button', { name: '停用' }))
    await user.click(draft.getByRole('button', { name: '设为默认' }))
    await user.click(draft.getByRole('button', { name: '删除' }))
    await waitFor(() => expect(api.enableLlmModel).toHaveBeenCalledWith('cfg-draft', 1, 'probe-2'))
    await waitFor(() => expect(api.disableLlmModel).toHaveBeenCalledWith('cfg-active', 1))
    await waitFor(() => expect(api.activateLlmModel).toHaveBeenCalledWith('cfg-draft', 1))
    await waitFor(() => expect(api.deleteLlmModel).toHaveBeenCalledWith('cfg-draft'))
    expect(confirmSpy).toHaveBeenCalled()
    expect(api.listLlmModels.mock.calls.length).toBeGreaterThan(4)
    confirmSpy.mockRestore()
  })

  it('protects the current default from disable and delete actions', async () => {
    mockLoaded({
      default_model_config_id: baseModel.id,
      items: [{ ...baseModel, lifecycle_status: 'active', capabilities: { ...baseModel.capabilities, can_enable: false, can_disable: false, can_activate: false, can_delete: false } }],
    } as any)
    render(<LlmModelsView />)
    await screen.findByText('DeepSeek 主模型')
    expect(screen.getByRole('button', { name: '停用' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '删除' })).toBeDisabled()
  })

  it('shows locked budget totals and requires a Chinese reason before unlock', async () => {
    const locked = { ...baseSettings, budget_locked: true, reserved_tokens: 170, settled_tokens: 230, version: 7 }
    mockLoaded({}, locked)
    api.unlockLlmSettings.mockRejectedValueOnce(apiError(409, '额度状态已变化，请刷新后重试', 'llm_settings_conflict'))
    render(<LlmModelsView />)
    expect(await screen.findByText(/额度已锁停/)).toBeInTheDocument()
    expect(screen.getByText(/预留 170/)).toBeInTheDocument()
    expect(screen.getByText(/已结算 230/)).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: '解除锁停' }))
    expect(screen.getByRole('button', { name: '确认解锁' })).toBeDisabled()
    await userEvent.type(screen.getByLabelText('解锁原因'), '已核对供应商账单')
    await userEvent.click(screen.getByRole('button', { name: '确认解锁' }))
    await waitFor(() => expect(api.unlockLlmSettings).toHaveBeenCalledWith(7, '已核对供应商账单'))
    expect(await screen.findByText('配置已被其他管理员修改，请刷新后重试')).toBeInTheDocument()
    expect(screen.getByText(/额度已锁停/)).toBeInTheDocument()
  })

  it('renders the successful unlock audit result only after the API confirms it', async () => {
    const locked = { ...baseSettings, budget_locked: true, reserved_tokens: 1, settled_tokens: 2, version: 4 }
    mockLoaded({}, locked)
    api.unlockLlmSettings.mockResolvedValueOnce({ ...locked, budget_locked: false, version: 5 })
    render(<LlmModelsView />)
    await screen.findByText(/额度已锁停/)
    api.getLlmSettings.mockResolvedValueOnce({ ...locked, budget_locked: false, version: 5 })
    await userEvent.click(screen.getByRole('button', { name: '解除锁停' }))
    await userEvent.type(screen.getByLabelText('解锁原因'), '完成账单复核后恢复')
    await userEvent.click(screen.getByRole('button', { name: '确认解锁' }))
    expect(await screen.findByText(/解锁审计已记录/)).toBeInTheDocument()
    expect(screen.queryByText(/额度已锁停/)).not.toBeInTheDocument()
  })

  it('refetches and synchronizes the daily limit input after a settings conflict', async () => {
    const latest = { ...baseSettings, daily_token_limit: 7654321, version: 2 }
    api.listLlmModels.mockResolvedValue(baseList)
    api.getLlmSettings
      .mockResolvedValueOnce(baseSettings)
      .mockResolvedValueOnce(latest)
    api.getLlmUsage.mockResolvedValue(baseUsage)
    api.patchLlmSettings.mockRejectedValueOnce(apiError(409, '额度设置版本已变化', 'llm_settings_conflict'))
    render(<LlmModelsView />)
    const input = await screen.findByTestId('daily-token-limit')
    expect(input).toHaveValue(2000000)
    await userEvent.clear(input)
    await userEvent.type(input, '123')
    await userEvent.click(screen.getByRole('button', { name: '保存限额' }))
    await waitFor(() => expect(api.patchLlmSettings).toHaveBeenCalledWith(1, 123))
    await waitFor(() => expect(input).toHaveValue(7654321))
    expect(await screen.findByText('配置已被其他管理员修改，请刷新后重试')).toBeInTheDocument()
  })
})

describe('llm api helpers', () => {
  it('uses a fresh UUID idempotency key for each activation request', async () => {
    const key1 = '00000000-0000-4000-8000-000000000001'
    const key2 = '00000000-0000-4000-8000-000000000002'
    const actual = await vi.importActual<typeof import('../../api/llmModels')>('../../api/llmModels')
    http.post.mockResolvedValue({ data: { data: baseModel } })
    const uuid = vi.spyOn(globalThis.crypto, 'randomUUID')
      .mockReturnValueOnce(key1)
      .mockReturnValueOnce(key2)
    const activate = actual.activateLlmModel
    await activate('cfg-1', 3)
    await activate('cfg-1', 3)
    expect(http.post).toHaveBeenNthCalledWith(1, '/admin/llm-models/cfg-1/activate', {
      expected_version: 3,
      idempotency_key: key1,
    })
    expect(http.post).toHaveBeenNthCalledWith(2, '/admin/llm-models/cfg-1/activate', {
      expected_version: 3,
      idempotency_key: key2,
    })
    uuid.mockRestore()
  })
})
