import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  activateLlmModel,
  createLlmModel,
  deleteLlmModel,
  disableLlmModel,
  enableLlmModel,
  getLlmSettings,
  getLlmUsage,
  listLlmModels,
  patchLlmModel,
  patchLlmSettings,
  testSavedLlmModel,
  testUnsavedLlmModel,
  unlockLlmSettings,
  type LlmLifecycle,
  type LlmModel,
  type LlmModelCandidate,
  type LlmModelList,
  type LlmProbeResult,
  type LlmSettings,
  type LlmUsage,
  type LlmProvider,
} from '../../api/llmModels'

type ModelForm = LlmModelCandidate
type Action = 'load' | 'save' | 'probe-new' | `probe:${string}` | `enable:${string}` | `disable:${string}` | `activate:${string}` | `delete:${string}` | 'settings' | 'unlock' | null

const PROVIDERS: Array<{ value: LlmProvider; label: string }> = [
  { value: 'deepseek', label: 'DeepSeek' },
  { value: 'kimi', label: 'Kimi' },
  { value: 'qwen', label: 'Qwen' },
]

const PROVIDER_LABELS: Record<LlmProvider, string> = {
  deepseek: 'DeepSeek',
  kimi: 'Kimi',
  qwen: 'Qwen',
}

const LIFECYCLE_LABELS: Record<LlmLifecycle, string> = {
  draft: '草稿',
  active: '已启用',
  disabled: '已停用',
  retired: '已退役',
}

const EMPTY_FORM: ModelForm = {
  provider: 'deepseek',
  display_name: '',
  model_name: '',
  base_url: 'https://api.deepseek.com/v1',
  api_key: '',
  max_output_tokens: 4096,
  input_price_micro_yuan_per_million: null,
  output_price_micro_yuan_per_million: null,
}

function errorMessage(error: any, fallback: string): string {
  const status = error?.response?.status
  if (status === 409) return '配置已被其他管理员修改，请刷新后重试'
  const message = error?.response?.data?.message
  if (typeof message === 'string' && message) return message
  const detail = error?.response?.data?.detail
  if (typeof detail === 'string' && detail) return detail
  if (detail && typeof detail.message === 'string') return detail.message
  return fallback
}

function isChineseReason(value: string): boolean {
  return /[\u4e00-\u9fff]/.test(value.trim())
}

function formatPrice(value: number | null): string {
  return value === null || value === undefined ? '价格未知' : `¥${(value / 1_000_000).toFixed(2)} / 百万 Token`
}

function formatNumber(value: number): string {
  return Number(value || 0).toLocaleString('zh-CN')
}

function defaultForm(model: LlmModel): ModelForm {
  return {
    provider: model.provider,
    display_name: model.display_name,
    model_name: model.model_name,
    base_url: model.base_url,
    api_key: '',
    max_output_tokens: model.max_output_tokens ?? 4096,
    input_price_micro_yuan_per_million: model.input_price_micro_yuan_per_million,
    output_price_micro_yuan_per_million: model.output_price_micro_yuan_per_million,
  }
}

function modelPayload(form: ModelForm): ModelForm {
  const inputPrice = form.input_price_micro_yuan_per_million
  const outputPrice = form.output_price_micro_yuan_per_million
  return {
    ...form,
    display_name: form.display_name.trim(),
    model_name: form.model_name.trim(),
    base_url: form.base_url.trim(),
    api_key: form.api_key,
    max_output_tokens: Number(form.max_output_tokens),
    input_price_micro_yuan_per_million: inputPrice === null || inputPrice === undefined ? null : Number(inputPrice),
    output_price_micro_yuan_per_million: outputPrice === null || outputPrice === undefined ? null : Number(outputPrice),
  }
}

function validateForm(form: ModelForm, requireKey: boolean): string | null {
  if (!form.display_name.trim()) return '请填写配置名称'
  if (!form.model_name.trim()) return '请填写模型 ID'
  if (!form.base_url.trim()) return '请填写 Base URL'
  if (requireKey && !form.api_key.trim()) return '新配置必须填写 API Key'
  if (!Number.isInteger(Number(form.max_output_tokens)) || Number(form.max_output_tokens) <= 0) return '最大输出 Token 必须为正整数'
  return null
}

export default function LlmModelsView() {
  const [modelList, setModelList] = useState<LlmModelList | null>(null)
  const [settings, setSettings] = useState<LlmSettings | null>(null)
  const [usage, setUsage] = useState<LlmUsage | null>(null)
  const [dailyTokenLimit, setDailyTokenLimit] = useState<number | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [formError, setFormError] = useState('')
  const [editingId, setEditingId] = useState<string | null>(null)
  const [form, setForm] = useState<ModelForm>(EMPTY_FORM)
  const [showForm, setShowForm] = useState(false)
  const [pending, setPending] = useState<Action>(null)
  const [probeRuns, setProbeRuns] = useState<Record<string, string>>({})
  const [probeResult, setProbeResult] = useState<LlmProbeResult | null>(null)
  const [unlockOpen, setUnlockOpen] = useState(false)
  const [unlockReason, setUnlockReason] = useState('')

  const refresh = useCallback(async () => {
    setLoading(true)
    const [modelsResult, settingsResult, usageResult] = await Promise.allSettled([
      listLlmModels(),
      getLlmSettings(),
      getLlmUsage(7),
    ])
    const nextErrors: string[] = []
    if (modelsResult.status === 'fulfilled') setModelList(modelsResult.value)
    else nextErrors.push(errorMessage(modelsResult.reason, '加载模型配置失败'))
    if (settingsResult.status === 'fulfilled') {
      setSettings(settingsResult.value)
      setDailyTokenLimit(settingsResult.value.daily_token_limit)
    }
    else nextErrors.push(errorMessage(settingsResult.reason, '加载额度设置失败'))
    if (usageResult.status === 'fulfilled') setUsage(usageResult.value)
    setError(nextErrors[0] || '')
    setLoading(false)
  }, [])

  useEffect(() => { void refresh() }, [refresh])

  const startAdd = () => {
    setEditingId(null)
    setForm({ ...EMPTY_FORM })
    setProbeResult(null)
    setFormError('')
    setNotice('')
    setShowForm(true)
  }

  const startEdit = (model: LlmModel) => {
    setEditingId(model.id)
    setForm(defaultForm(model))
    setProbeResult(null)
    setFormError('')
    setNotice('')
    setShowForm(true)
  }

  const closeForm = () => {
    if (pending === 'save' || pending === 'probe-new') return
    setShowForm(false)
    setEditingId(null)
    setForm({ ...EMPTY_FORM })
    setProbeResult(null)
    setFormError('')
  }

  const runUnsavedProbe = async () => {
    const validation = validateForm(form, true)
    if (validation) {
      setFormError(validation)
      return
    }
    setPending('probe-new')
    setFormError('')
    setNotice('')
    const payload = modelPayload(form)
    try {
      const result = await testUnsavedLlmModel(payload)
      setProbeResult(result)
      setNotice('测试完成，请重新输入 API Key 后保存')
    } catch (cause) {
      setFormError(errorMessage(cause, '模型测试失败，请检查配置后重试'))
    } finally {
      setForm((current) => ({ ...current, api_key: '' }))
      setPending(null)
    }
  }

  const saveModel = async () => {
    const validation = validateForm(form, editingId === null)
    if (validation) {
      setFormError(validation)
      return
    }
    setPending('save')
    setFormError('')
    setNotice('')
    const payload = modelPayload(form)
    try {
      if (editingId) {
        const model = modelList?.items.find((item) => item.id === editingId)
        if (!model) throw new Error('模型配置不存在，请刷新后重试')
        await patchLlmModel(editingId, { ...payload, expected_version: model.version })
      } else {
        await createLlmModel(payload)
      }
      setNotice('模型配置已保存')
      setShowForm(false)
      setEditingId(null)
      setForm({ ...EMPTY_FORM })
      await refresh()
    } catch (cause) {
      const message = errorMessage(cause, '保存配置失败')
      if ((cause as any)?.response?.status === 409) await refresh()
      setFormError(message)
    } finally {
      setForm((current) => ({ ...current, api_key: '' }))
      setPending(null)
    }
  }

  const runModelTest = async (model: LlmModel) => {
    if (!model.capabilities.can_test || pending) return
    setPending(`probe:${model.id}`)
    setError('')
    setNotice('')
    try {
      const result = await testSavedLlmModel(model.id)
      if (result.test_run_id) setProbeRuns((current) => ({ ...current, [model.id]: result.test_run_id! }))
      setNotice(`${model.display_name} 测试完成`)
      await refresh()
    } catch (cause) {
      setError(errorMessage(cause, '模型测试失败'))
    } finally {
      setPending(null)
    }
  }

  const runAction = async (
    action: Exclude<Action, null | 'load' | 'save' | 'probe-new' | 'settings' | 'unlock'>,
    operation: () => Promise<unknown>,
    confirmation: string,
    success: string,
  ) => {
    if (pending || !window.confirm(confirmation)) return
    setPending(action)
    setError('')
    setNotice('')
    try {
      await operation()
      setNotice(success)
      await refresh()
    } catch (cause) {
      const message = errorMessage(cause, '操作失败')
      if ((cause as any)?.response?.status === 409) await refresh()
      setError(message)
    } finally {
      setPending(null)
    }
  }

  const updateSettings = async () => {
    if (!settings || pending) return
    setPending('settings')
    setError('')
    setNotice('')
    const limit = Number(dailyTokenLimit ?? settings.daily_token_limit)
    try {
      await patchLlmSettings(settings.version, limit)
      await refresh()
      setNotice('每日 Token 限额已保存')
    } catch (cause) {
      const message = errorMessage(cause, '保存额度设置失败')
      if ((cause as any)?.response?.status === 409) await refresh()
      setError(message)
    } finally {
      setPending(null)
    }
  }

  const confirmUnlock = async () => {
    if (!settings || pending || !isChineseReason(unlockReason)) return
    setPending('unlock')
    setError('')
    try {
      await unlockLlmSettings(settings.version, unlockReason.trim())
      await refresh()
      setUnlockOpen(false)
      setUnlockReason('')
      setNotice('解锁审计已记录')
    } catch (cause) {
      const message = errorMessage(cause, '解锁额度失败')
      if ((cause as any)?.response?.status === 409) await refresh()
      setError(message)
    } finally {
      setPending(null)
    }
  }

  const items = modelList?.items ?? []
  const totalCost = usage?.total_cost_micro_yuan
  const totalCostLabel = totalCost === null || totalCost === undefined
    ? '价格未知（部分费用未配置）'
    : `¥${(totalCost / 1_000_000).toFixed(2)}`
  const dailyLimit = settings?.daily_token_limit ?? modelList?.daily_token_limit ?? 0
  const selectedModel = useMemo(() => editingId ? items.find((model) => model.id === editingId) : null, [editingId, items])

  if (loading && !modelList) return <div className="empty">加载中…</div>

  return (
    <div className="llm-center panel-stack">
      <div className="between wrap llm-center-heading">
        <div>
          <h2 className="card-title">大模型中心</h2>
          <p className="caption">管理供应商模型、真实连通性测试与每日 Token 额度。页面不会保存或回显 API Key。</p>
        </div>
        <button className="btn btn-primary" onClick={startAdd}>添加模型</button>
      </div>

      {error && <div className="banner down" role="alert">{error}</div>}
      {notice && <div className="banner up" role="status">{notice}</div>}

      {settings?.budget_locked && (
        <section className="llm-budget-banner banner" role="alert">
          <div>
            <strong>额度已锁停</strong>
            <p>今日（{settings.budget_date}）大模型调用已暂停。预留 {formatNumber(settings.reserved_tokens)} Token，已结算 {formatNumber(settings.settled_tokens)} Token。</p>
          </div>
          <button className="btn btn-dark" onClick={() => setUnlockOpen(true)} disabled={pending === 'unlock'}>解除锁停</button>
        </section>
      )}

      {showForm && (
        <section className="card llm-model-form" aria-label={editingId ? '编辑模型配置' : '添加模型配置'}>
          <div className="between wrap">
            <h3 className="card-title-sm">{editingId ? '编辑模型配置' : '添加模型配置'}</h3>
            <button className="btn btn-ghost" onClick={closeForm} disabled={pending === 'save' || pending === 'probe-new'}>取消</button>
          </div>
          <div className="form-row">
            <div className="field">
              <label htmlFor="llm-provider">供应商</label>
              <select id="llm-provider" className="select" value={form.provider} onChange={(event) => setForm({ ...form, provider: event.target.value as LlmProvider })}>
                {PROVIDERS.map((provider) => <option key={provider.value} value={provider.value}>{provider.label}</option>)}
              </select>
            </div>
            <div className="field">
              <label htmlFor="llm-display-name">配置名称</label>
              <input id="llm-display-name" className="input" value={form.display_name} onChange={(event) => setForm({ ...form, display_name: event.target.value })} />
            </div>
          </div>
          <div className="form-row mt16">
            <div className="field">
              <label htmlFor="llm-model-name">模型 ID</label>
              <input id="llm-model-name" className="input mono llm-wrap-input" value={form.model_name} onChange={(event) => setForm({ ...form, model_name: event.target.value })} />
            </div>
            <div className="field">
              <label htmlFor="llm-base-url">Base URL</label>
              <input id="llm-base-url" className="input mono llm-wrap-input" value={form.base_url} onChange={(event) => setForm({ ...form, base_url: event.target.value })} />
            </div>
          </div>
          <div className="form-row mt16">
            <div className="field">
              <label htmlFor="llm-api-key">API Key</label>
              <input id="llm-api-key" className="input mono" type="password" autoComplete="new-password" value={form.api_key} placeholder={editingId ? '留空表示保留原密钥' : '仅用于本次保存或测试'} onChange={(event) => setForm({ ...form, api_key: event.target.value })} />
              {selectedModel?.key_hint && <span className="caption">当前密钥：{selectedModel.key_hint}</span>}
            </div>
            <div className="field">
              <label htmlFor="llm-max-output">最大输出 Token</label>
              <input id="llm-max-output" className="input mono" type="number" min="1" value={form.max_output_tokens} onChange={(event) => setForm({ ...form, max_output_tokens: Number(event.target.value) })} />
            </div>
          </div>
          <div className="form-row mt16">
            <div className="field">
              <label htmlFor="llm-input-price">输入价格（微元/百万 Token）</label>
              <input id="llm-input-price" className="input mono" type="number" min="0" placeholder="可留空，显示价格未知" value={form.input_price_micro_yuan_per_million ?? ''} onChange={(event) => setForm({ ...form, input_price_micro_yuan_per_million: event.target.value === '' ? null : Number(event.target.value) })} />
            </div>
            <div className="field">
              <label htmlFor="llm-output-price">输出价格（微元/百万 Token）</label>
              <input id="llm-output-price" className="input mono" type="number" min="0" placeholder="可留空，显示价格未知" value={form.output_price_micro_yuan_per_million ?? ''} onChange={(event) => setForm({ ...form, output_price_micro_yuan_per_million: event.target.value === '' ? null : Number(event.target.value) })} />
            </div>
          </div>
          {formError && <p className="small down" role="alert">{formError}</p>}
          {probeResult && <p className="small up" role="status">能力测试已记录{probeResult.test_run_id ? `（测试编号 ${probeResult.test_run_id}）` : ''}。</p>}
          <div className="flex wrap mt16">
            <button className="btn btn-ghost" onClick={runUnsavedProbe} disabled={pending === 'probe-new' || pending === 'save'}>{pending === 'probe-new' ? '测试中…' : '测试配置'}</button>
            <button className="btn btn-primary" onClick={saveModel} disabled={pending === 'probe-new' || pending === 'save'}>{pending === 'save' ? '保存中…' : '保存配置'}</button>
          </div>
        </section>
      )}

      <section className="card">
        <div className="between wrap">
          <div>
            <h3 className="card-title-sm">模型配置</h3>
            <p className="caption">共 {modelList?.total ?? 0} 个配置；模型 ID 与 Base URL 支持自定义。</p>
          </div>
          {loading && <span className="caption">刷新中…</span>}
        </div>
        {items.length === 0 ? <div className="empty">暂无模型配置</div> : (
          <div className="llm-model-list">
            {items.map((model) => {
              const isDefault = settings?.default_model_config_id === model.id || modelList?.default_model_config_id === model.id
              const actionDisabled = Boolean(pending)
              return (
                <article className="llm-model-card" data-testid={`llm-model-${model.id}`} key={model.id}>
                  <div className="llm-model-main">
                    <div className="between wrap llm-model-title-row">
                      <div>
                        <h4>{model.display_name}</h4>
                        <span className="badge info">{PROVIDER_LABELS[model.provider]}</span>{' '}
                        <span className={`badge ${model.lifecycle_status === 'active' ? 'up' : ''}`}>{isDefault ? '默认模型' : LIFECYCLE_LABELS[model.lifecycle_status]}</span>
                      </div>
                      <div className="llm-model-actions">
                        <button className="btn-text" onClick={() => startEdit(model)} disabled={actionDisabled}>编辑</button>
                        <button className="btn-text" onClick={() => void runModelTest(model)} disabled={actionDisabled || !model.capabilities.can_test}>{pending === `probe:${model.id}` ? '测试中…' : '测试'}</button>
                        <button className="btn-text" onClick={() => void runAction(`enable:${model.id}`, () => {
                          const testRunId = probeRuns[model.id] || model.verified_test_id || undefined
                          if (!testRunId) return Promise.reject(new Error('启用前请先测试模型'))
                          return enableLlmModel(model.id, model.version, testRunId)
                        }, `确认启用“${model.display_name}”？`, '模型已启用')} disabled={actionDisabled || !model.capabilities.can_enable}>{pending === `enable:${model.id}` ? '启用中…' : '启用'}</button>
                        <button className="btn-text" onClick={() => void runAction(`disable:${model.id}`, () => disableLlmModel(model.id, model.version), `确认停用“${model.display_name}”？`, '模型已停用')} disabled={actionDisabled || !model.capabilities.can_disable}>{pending === `disable:${model.id}` ? '停用中…' : '停用'}</button>
                        <button className="btn-text" onClick={() => void runAction(`activate:${model.id}`, () => activateLlmModel(model.id, model.version), `确认将“${model.display_name}”设为默认模型？`, '默认模型已切换')} disabled={actionDisabled || !model.capabilities.can_activate}>{pending === `activate:${model.id}` ? '切换中…' : '设为默认'}</button>
                        <button className="btn-text down" onClick={() => void runAction(`delete:${model.id}`, () => deleteLlmModel(model.id), `确认删除“${model.display_name}”？删除后不可恢复。`, '模型配置已删除')} disabled={actionDisabled || !model.capabilities.can_delete}>{pending === `delete:${model.id}` ? '删除中…' : '删除'}</button>
                      </div>
                    </div>
                    <div className="llm-model-meta">
                      <span className="mono llm-breakable">模型 ID：{model.model_name}</span>
                      <span className="mono llm-breakable">Base URL：{model.base_url}</span>
                      <span>API Key：{model.key_hint || '未配置'}</span>
                    </div>
                    <div className="llm-model-prices">
                      <span>输入：{formatPrice(model.input_price_micro_yuan_per_million)}</span>
                      <span>输出：{formatPrice(model.output_price_micro_yuan_per_million)}</span>
                      {model.last_probe_status && <span>最近测试：{model.last_probe_status}</span>}
                    </div>
                  </div>
                </article>
              )
            })}
          </div>
        )}
      </section>

      <section className="card">
        <div className="between wrap">
          <div>
            <h3 className="card-title-sm">额度与近 7 日用量</h3>
            <p className="caption">预算锁停由服务端状态决定，页面不会乐观隐藏锁停提示。</p>
          </div>
          <div className="llm-settings-form">
            <label htmlFor="daily-token-limit">每日 Token 限额</label>
            <input id="daily-token-limit" data-testid="daily-token-limit" className="input mono" type="number" min="1" value={dailyTokenLimit ?? dailyLimit} onChange={(event) => setDailyTokenLimit(Number(event.target.value))} />
            <button className="btn btn-ghost" onClick={updateSettings} disabled={pending === 'settings'}>{pending === 'settings' ? '保存中…' : '保存限额'}</button>
          </div>
        </div>
        <p className="small">累计调用 {formatNumber(usage?.total_calls ?? 0)} 次，成本 {totalCostLabel}。</p>
        {usage?.items.length ? (
          <div className="table-wrap">
            <table className="table">
              <thead><tr><th>日期</th><th>模块</th><th>供应商 / 模型</th><th className="num">调用</th><th className="num">输入 Token</th><th className="num">输出 Token</th><th className="num">成本</th></tr></thead>
                      <tbody>{usage.items.map((item) => <tr key={`${item.date}-${item.module}-${item.provider}-${item.model}-${item.model_config_id}`}><td className="mono">{item.date}</td><td>{item.module}</td><td className="mono llm-breakable">{item.provider ? PROVIDER_LABELS[item.provider] : '未知供应商'} / {item.model}</td><td className="num mono">{formatNumber(item.calls)}</td><td className="num mono">{formatNumber(item.input_tokens)}</td><td className="num mono">{formatNumber(item.output_tokens)}</td><td className="num mono">{item.cost_micro_yuan === null ? '价格未知' : `¥${(item.cost_micro_yuan / 1_000_000).toFixed(4)}`}</td></tr>)}</tbody>
            </table>
          </div>
        ) : <div className="empty">暂无用量记录</div>}
      </section>

      {unlockOpen && settings?.budget_locked && (
        <div className="modal-mask" role="presentation">
          <section className="modal" role="dialog" aria-modal="true" aria-labelledby="unlock-title">
            <h3 id="unlock-title">申请解锁额度保险丝</h3>
            <p className="caption">请输入中文原因，提交时会记录管理员审计事件。</p>
            <label className="field" htmlFor="unlock-reason">解锁原因</label>
            <textarea id="unlock-reason" className="textarea mt8" value={unlockReason} onChange={(event) => setUnlockReason(event.target.value)} />
            {unlockReason && !isChineseReason(unlockReason) && <p className="small down">解锁原因必须使用中文说明</p>}
            <div className="flex wrap mt16">
              <button className="btn btn-ghost" onClick={() => { setUnlockOpen(false); setUnlockReason('') }} disabled={pending === 'unlock'}>取消</button>
              <button className="btn btn-primary" onClick={() => void confirmUnlock()} disabled={pending === 'unlock' || !isChineseReason(unlockReason)}>{pending === 'unlock' ? '提交中…' : '确认解锁'}</button>
            </div>
          </section>
        </div>
      )}
    </div>
  )
}
