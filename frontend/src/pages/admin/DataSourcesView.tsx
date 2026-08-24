import { useCallback, useEffect, useState } from 'react'
import {
  listDataSourceRoutes,
  listDataSources,
  patchDataSource,
  saveDataSource,
  saveDataSourceRoute,
  setDataSourceEnabled,
  testDataSource,
  testSavedDataSource,
  type DataSource,
  type DataSourceRoute,
  type CredentialField,
} from '../../api/dataHub'
import { errMsg } from '../../utils/errors'

type CredentialState = Record<string, string>
type ProviderOperationResult = { kind: 'success' | 'error'; message: string }

const CAPABILITY_LABELS: Record<string, string> = {
  'market.indices': '大盘指数',
  'market.board_quotes': '板块行情',
  'market.board_constituents': '板块成分股',
  'stock.snapshot': '个股实时行情',
  'stock.profile': '公司资料',
  'stock.kline.daily': '日K线',
  'stock.financials': '财务摘要',
  'stock.fund_flow': '个股资金流',
  'stock.news': '个股/全局资讯',
  'market.fund_flow_rank': '全市场资金排名',
  'stock.shareholders': '股东户数',
  'sector.realtime': '行业实时行情',
  'sector.kline': '板块K线',
  'sector.fund_flow': '板块资金流',
  'dragon_tiger.list': '龙虎榜个股',
  'dragon_tiger.seats': '龙虎榜席位',
  'kpl.limit_list': '开盘啦涨停榜',
  'kpl.concepts': '开盘啦题材',
  'kpl.concept_constituents': '开盘啦题材成分',
  'kpl.limit_ladder': '开盘啦连板梯队',
  'kpl.strong_sectors': '开盘啦强势板块',
  'market.auction_open': '竞价开盘',
  'kpl_native.stock_tags': '开盘啦原生股票标签',
  'kpl_native.plate_ranking': '开盘啦原生板块排行',
  'kpl_native.plate_constituents': '开盘啦原生板块成分',
  'kpl_native.stock_ranking': '开盘啦原生股票排行',
}

function capabilityLabel(capability: string): string {
  return CAPABILITY_LABELS[capability] || capability
}

function isTokenField(field: string): boolean {
  return /token|key|secret|password/i.test(field)
}

function normalizeCredentialField(field: CredentialField | string): CredentialField {
  if (typeof field === 'string') {
    return {
      key: field,
      label: field === 'token' ? 'Token' : field,
      secret: isTokenField(field),
      required: false,
      help: '',
    }
  }
  return field
}

function probeLabel(source: DataSource): string {
  if (source.last_probe_status === 'ok') return `最近测试正常${source.last_probe_latency_ms ? ` · ${source.last_probe_latency_ms}ms` : ''}`
  if (source.last_probe_status) return '最近测试失败'
  return '尚未测试'
}

export default function DataSourcesView() {
  const [sources, setSources] = useState<DataSource[]>([])
  const [routes, setRoutes] = useState<DataSourceRoute[]>([])
  const [credentials, setCredentials] = useState<Record<string, CredentialState>>({})
  const [pending, setPending] = useState<Record<string, string>>({})
  const [operationResults, setOperationResults] = useState<Record<string, ProviderOperationResult | undefined>>({})
  const [error, setError] = useState('')
  const [selectedCapabilities, setSelectedCapabilities] = useState<Record<string, string>>({})

  const isProviderPending = (provider: string) => Boolean(pending[provider])
  const hasPending = Object.keys(pending).length > 0
  const beginOperation = (provider: string, operation: string) => {
    setPending((current) => ({ ...current, [provider]: operation }))
    setOperationResults((current) => ({ ...current, [provider]: undefined }))
  }
  const endOperation = (provider: string) => {
    setPending((current) => {
      const next = { ...current }
      delete next[provider]
      return next
    })
  }
  const setOperationResult = (provider: string, result: ProviderOperationResult) => {
    setOperationResults((current) => ({ ...current, [provider]: result }))
  }

  const refresh = useCallback(async () => {
    setError('')
    try {
      const [sourceData, routeData] = await Promise.all([listDataSources(), listDataSourceRoutes()])
      setSources(sourceData.items)
      setRoutes(routeData.items)
      setSelectedCapabilities((current) => {
        const next = { ...current }
        for (const source of sourceData.items) if (!next[source.provider]) next[source.provider] = source.capabilities[0] || ''
        return next
      })
    } catch (cause) {
      setError(errMsg(cause, '加载数据源配置失败'))
    }
  }, [])

  useEffect(() => { void refresh() }, [refresh])

  const setCredential = (provider: string, field: string, value: string) => {
    setCredentials((current) => ({ ...current, [provider]: { ...(current[provider] || {}), [field]: value } }))
  }

  const clearCredentials = (provider: string) => {
    setCredentials((current) => ({ ...current, [provider]: {} }))
  }

  const runTest = async (source: DataSource) => {
    if (isProviderPending(source.provider)) return
    beginOperation(source.provider, 'test')
    const values = credentials[source.provider] || {}
    const capability = selectedCapabilities[source.provider] || source.capabilities[0]
    try {
      const hasNewCredential = Object.values(values).some((value) => Boolean(value))
      const useSavedProbe = !hasNewCredential && source.version > 0 && source.auth_type !== 'none'
      const result = !useSavedProbe
        ? await testDataSource({ provider: source.provider, public_config: { capability }, credentials: values })
        : await testSavedDataSource(source.provider, capability)
      setOperationResult(source.provider, {
        kind: result.status === 'ok' ? 'success' : 'error',
        message: `${source.display_name} 测试完成：${result.status === 'ok' ? `获取 ${String(result.rows ?? 0)} 行` : String(result.message || '未通过')}`,
      })
      await refresh()
    } catch (cause) {
      setOperationResult(source.provider, { kind: 'error', message: errMsg(cause, `${source.display_name} 测试失败`) })
    } finally {
      clearCredentials(source.provider)
      endOperation(source.provider)
    }
  }

  const save = async (source: DataSource) => {
    if (isProviderPending(source.provider)) return
    beginOperation(source.provider, 'save')
    const values = credentials[source.provider] || {}
    try {
      const result = source.version > 0
        ? await patchDataSource(source.provider, { credentials: values, expected_version: source.version })
        : await saveDataSource({ provider: source.provider, credentials: values })
      setSources((current) => current.map((item) => item.provider === source.provider ? result : item))
      setOperationResult(source.provider, { kind: 'success', message: `${source.display_name} 配置已保存` })
    } catch (cause) {
      if ((cause as any)?.response?.status === 409) await refresh()
      setOperationResult(source.provider, { kind: 'error', message: errMsg(cause, '保存配置失败，请刷新后重试') })
    } finally {
      clearCredentials(source.provider)
      endOperation(source.provider)
    }
  }

  const toggle = async (source: DataSource) => {
    if (isProviderPending(source.provider) || source.version <= 0) return
    beginOperation(source.provider, 'toggle')
    try {
      const result = await setDataSourceEnabled(source.provider, !source.enabled, source.version)
      setSources((current) => current.map((item) => item.provider === source.provider ? result : item))
      setOperationResult(source.provider, { kind: 'success', message: `${source.display_name} 已${result.enabled ? '启用' : '停用'}` })
    } catch (cause) {
      if ((cause as any)?.response?.status === 409) await refresh()
      setOperationResult(source.provider, { kind: 'error', message: errMsg(cause, '切换数据源失败') })
    } finally {
      endOperation(source.provider)
    }
  }

  const updateRoute = async (route: DataSourceRoute, mode: 'auto' | 'fixed') => {
    const operationKey = `route:${route.capability}`
    if (hasPending) return
    beginOperation(operationKey, 'route')
    try {
      const providers = mode === 'fixed' ? route.providers.slice(0, 1) : route.providers
      if (!providers.length) throw new Error('至少选择一个可用数据源')
      const result = await saveDataSourceRoute(route.capability, { mode, providers, expected_version: route.version || null })
      setRoutes((current) => current.map((item) => item.capability === route.capability ? { ...item, ...result, provider_options: item.provider_options } : item))
      setOperationResult(operationKey, { kind: 'success', message: `${capabilityLabel(route.capability)} 路由已更新` })
    } catch (cause) {
      if ((cause as any)?.response?.status === 409) await refresh()
      setOperationResult(operationKey, { kind: 'error', message: errMsg(cause, '能力路由更新失败') })
    } finally {
      endOperation(operationKey)
    }
  }

  const toggleRouteProvider = async (route: DataSourceRoute, provider: string, checked: boolean) => {
    if (hasPending) return
    const option = route.provider_options?.find((item) => item.provider === provider)
    if (option?.selectable === false && checked) return
    const current = route.providers
    let providers: string[]
    if (route.mode === 'fixed') {
      if (!checked && current.includes(provider)) return
      providers = checked ? [provider] : current
    } else {
      providers = checked
        ? [...current, provider]
        : current.filter((item) => item !== provider)
      if (!providers.length) return
    }
    const operationKey = `route:${route.capability}`
    beginOperation(operationKey, 'route')
    try {
      const result = await saveDataSourceRoute(route.capability, { mode: route.mode, providers, expected_version: route.version || null })
      setRoutes((items) => items.map((item) => item.capability === route.capability ? { ...item, ...result, provider_options: item.provider_options } : item))
      setOperationResult(operationKey, { kind: 'success', message: `${capabilityLabel(route.capability)} 数据源选择已更新` })
    } catch (cause) {
      if ((cause as any)?.response?.status === 409) await refresh()
      setOperationResult(operationKey, { kind: 'error', message: errMsg(cause, '数据源选择更新失败') })
    } finally {
      endOperation(operationKey)
    }
  }

  const moveProvider = async (route: DataSourceRoute, index: number, delta: number) => {
    const target = index + delta
    if (hasPending || target < 0 || target >= route.providers.length) return
    const providers = [...route.providers]
    ;[providers[index], providers[target]] = [providers[target], providers[index]]
    const operationKey = `route:${route.capability}`
    beginOperation(operationKey, 'route')
    try {
      const result = await saveDataSourceRoute(route.capability, { mode: route.mode, providers, expected_version: route.version || null })
      setRoutes((current) => current.map((item) => item.capability === route.capability ? { ...item, ...result, provider_options: item.provider_options } : item))
      setOperationResult(operationKey, { kind: 'success', message: `${capabilityLabel(route.capability)} 首选来源顺序已更新` })
    } catch (cause) {
      if ((cause as any)?.response?.status === 409) await refresh()
      setOperationResult(operationKey, { kind: 'error', message: errMsg(cause, '来源顺序更新失败') })
    } finally {
      endOperation(operationKey)
    }
  }

  return (
    <div className="datahub-view">
      <section className="card">
        <div className="between wrap">
          <div>
            <h2 className="card-title" style={{ marginBottom: 4 }}>数据源配置</h2>
            <p className="caption" style={{ margin: 0 }}>由平台注册表统一解释能力、费用、鉴权和风险；凭证只在本次操作中使用。</p>
          </div>
          <button className="btn btn-ghost" onClick={() => void refresh()} disabled={hasPending}>刷新状态</button>
        </div>
        {error && <p className="test-result datahub-error" role="alert">{error}</p>}
      </section>

      <div className="kpi-grid" style={{ gridTemplateColumns: 'repeat(auto-fit,minmax(280px,1fr))' }}>
        {sources.map((source) => (
          <section className="card" key={source.provider}>
            <div className="between wrap">
              <h2 className="card-title" style={{ marginBottom: 4 }}>{source.display_name}</h2>
              <span className={`badge ${source.enabled && source.available !== false ? 'up' : ''}`}>{source.available === false ? '不可用' : source.enabled ? '已启用' : '未启用'}</span>
            </div>
            <p className="small" style={{ margin: '8px 0' }}>{source.description}</p>
            <p className="caption" style={{ margin: '4px 0' }}>能力：{source.capabilities.map((capability) => `${capabilityLabel(capability)}（${capability}）`).join('、')}</p>
            <p className="caption" style={{ margin: '4px 0' }}>费用：{source.fee_type} · 更新：{source.update_frequency}</p>
            <p className="caption" style={{ margin: '4px 0' }}>风险：{source.risk_note}</p>
            {source.available === false && <p className="test-result datahub-error" role="status">暂不可用：{source.unavailable_reason || '尚未接入可验证生产接口'}</p>}
            <p className="caption" style={{ margin: '8px 0' }}>{probeLabel(source)}{source.key_hint ? ` · 凭证 ${source.key_hint}` : ''}</p>
            {source.capabilities.length > 0 && <div className="field mt8">
              <label htmlFor={`${source.provider}-capability`}>测试能力</label>
              <select id={`${source.provider}-capability`} className="input" value={selectedCapabilities[source.provider] || source.capabilities[0]} onChange={(event) => setSelectedCapabilities((current) => ({ ...current, [source.provider]: event.target.value }))}>
                {source.capabilities.map((capability) => <option key={capability} value={capability}>{capabilityLabel(capability)}（{capability}）</option>)}
              </select>
            </div>}
            {source.credential_fields.map((rawField) => {
              const field = normalizeCredentialField(rawField)
              return <div className="field mt8" key={field.key}>
                <label htmlFor={`${source.provider}-${field.key}`}>{field.label}</label>
                {field.required && <span className="caption">（必填）</span>}
                <input
                  id={`${source.provider}-${field.key}`}
                  className="input mono"
                  type={field.secret || isTokenField(field.key) ? 'password' : 'text'}
                  value={credentials[source.provider]?.[field.key] || ''}
                  placeholder={source.key_hint ? '已配置，留空保持不变' : `请输入${field.label}`}
                  onChange={(event) => setCredential(source.provider, field.key, event.target.value)}
                />
                {field.help && <span className="caption">{field.help}</span>}
              </div>
            })}
            <div className="flex mt16 wrap">
              <button className="btn btn-dark" onClick={() => void save(source)} disabled={isProviderPending(source.provider) || source.available === false}>{pending[source.provider] === 'save' ? '保存中…' : '保存配置'}</button>
              <button className="btn btn-ghost" onClick={() => void runTest(source)} disabled={isProviderPending(source.provider) || source.available === false}>{pending[source.provider] === 'test' ? '测试中…' : '测试连接'}</button>
              <button className="btn-text" onClick={() => void toggle(source)} disabled={isProviderPending(source.provider) || source.version <= 0 || source.available === false}>{source.enabled ? '停用' : '启用'}</button>
            </div>
            {operationResults[source.provider] && <p className={`test-result ${operationResults[source.provider]?.kind === 'success' ? 'datahub-success' : 'datahub-error'}`} role="status">{operationResults[source.provider]?.message}</p>}
          </section>
        ))}
        {sources.length === 0 && <div className="empty" style={{ gridColumn: '1/-1' }}>{error || '加载中…'}</div>}
      </div>

      <section className="card">
        <h2 className="card-title">能力路由</h2>
        <p className="caption">自动模式会按顺序降级；固定模式失败时不会偷偷切换。切换前需有 15 分钟内的能力级探测记录。</p>
        <div style={{ overflowX: 'auto' }}>
          <table className="table">
            <thead><tr><th>能力</th><th>模式</th><th>来源顺序</th><th>操作</th></tr></thead>
            <tbody>{routes.map((route) => <tr key={route.capability}>
              <td><span>{capabilityLabel(route.capability)}</span><div className="caption mono">{route.capability}</div></td>
              <td><span className="badge">{route.mode === 'auto' ? '自动' : '固定'}</span></td>
              <td className="small"><div>{route.providers.join(' → ')}</div><div className="flex mt8" style={{ flexDirection: 'column', alignItems: 'flex-start' }}>{(route.provider_options || []).map((option) => { const active = route.providers.includes(option.provider); const index = route.providers.indexOf(option.provider); return <label key={option.provider} className="caption" style={{ display: 'flex', alignItems: 'center', gap: 6 }}><input type="checkbox" checked={active} disabled={hasPending || option.selectable === false || (route.mode === 'fixed' && active)} onChange={(event) => void toggleRouteProvider(route, option.provider, event.target.checked)} />{option.display_name}（{option.provider}）{option.available === false ? `：${option.unavailable_reason || '不可用'}` : !option.enabled ? '：未启用' : ''}{active && route.mode === 'auto' && <><button className="btn-text" disabled={hasPending || index === 0} onClick={() => void moveProvider(route, index, -1)} aria-label={`${option.provider} 上移`}>↑</button><button className="btn-text" disabled={hasPending || index === route.providers.length - 1} onClick={() => void moveProvider(route, index, 1)} aria-label={`${option.provider} 下移`}>↓</button></>}</label> })}</div></td>
              <td className="flex" style={{ flexDirection: 'column', alignItems: 'flex-start' }}>
                <button className="btn-text" disabled={hasPending} onClick={() => void updateRoute(route, route.mode === 'auto' ? 'fixed' : 'auto')}>{route.mode === 'auto' ? '改为固定' : '改为自动'}</button>
                {operationResults[`route:${route.capability}`] && <p className={`test-result ${operationResults[`route:${route.capability}`]?.kind === 'success' ? 'datahub-success' : 'datahub-error'}`} role="status">{operationResults[`route:${route.capability}`]?.message}</p>}
              </td>
            </tr>)}</tbody>
          </table>
        </div>
      </section>
    </div>
  )
}
