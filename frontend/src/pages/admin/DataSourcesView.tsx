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
} from '../../api/dataHub'
import { errMsg } from '../../utils/errors'

type CredentialState = Record<string, string>

const CAPABILITY_LABELS: Record<string, string> = {
  'market.indices': '大盘指数',
  'market.sector_overview': '板块概览',
  'stock.snapshot': '个股实时行情',
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
}

function capabilityLabel(capability: string): string {
  return CAPABILITY_LABELS[capability] || capability
}

function isTokenField(field: string): boolean {
  return /token|key|secret|password/i.test(field)
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
  const [pending, setPending] = useState<string | null>(null)
  const [notice, setNotice] = useState('')
  const [error, setError] = useState('')
  const [selectedCapabilities, setSelectedCapabilities] = useState<Record<string, string>>({})

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
    if (pending) return
    setPending(`test:${source.provider}`)
    setNotice('')
    setError('')
    const values = credentials[source.provider] || {}
    const capability = selectedCapabilities[source.provider] || source.capabilities[0]
    try {
      const hasNewCredential = Object.values(values).some((value) => Boolean(value))
      const useSavedProbe = !hasNewCredential && source.version > 0 && source.auth_type !== 'none'
      const result = !useSavedProbe
        ? await testDataSource({ provider: source.provider, public_config: { capability }, credentials: values })
        : await testSavedDataSource(source.provider, capability)
      setNotice(`${source.display_name} 测试完成：${result.status === 'ok' ? `获取 ${String(result.rows ?? 0)} 行` : '未通过'}`)
      await refresh()
    } catch (cause) {
      setError(errMsg(cause, `${source.display_name} 测试失败`))
    } finally {
      clearCredentials(source.provider)
      setPending(null)
    }
  }

  const save = async (source: DataSource) => {
    if (pending) return
    setPending(`save:${source.provider}`)
    setNotice('')
    setError('')
    const values = credentials[source.provider] || {}
    try {
      const result = source.version > 0
        ? await patchDataSource(source.provider, { credentials: values, expected_version: source.version })
        : await saveDataSource({ provider: source.provider, credentials: values })
      setSources((current) => current.map((item) => item.provider === source.provider ? result : item))
      setNotice(`${source.display_name} 配置已保存`)
    } catch (cause) {
      if ((cause as any)?.response?.status === 409) await refresh()
      setError(errMsg(cause, '保存配置失败，请刷新后重试'))
    } finally {
      clearCredentials(source.provider)
      setPending(null)
    }
  }

  const toggle = async (source: DataSource) => {
    if (pending || source.version <= 0) return
    setPending(`toggle:${source.provider}`)
    try {
      const result = await setDataSourceEnabled(source.provider, !source.enabled, source.version)
      setSources((current) => current.map((item) => item.provider === source.provider ? result : item))
      setNotice(`${source.display_name} 已${result.enabled ? '启用' : '停用'}`)
    } catch (cause) {
      if ((cause as any)?.response?.status === 409) await refresh()
      setError(errMsg(cause, '切换数据源失败'))
    } finally {
      setPending(null)
    }
  }

  const updateRoute = async (route: DataSourceRoute, mode: 'auto' | 'fixed') => {
    if (pending) return
    setPending(`route:${route.capability}`)
    try {
      const providers = mode === 'fixed' ? route.providers.slice(0, 1) : route.providers
      if (!providers.length) throw new Error('至少选择一个可用数据源')
      const result = await saveDataSourceRoute(route.capability, { mode, providers, expected_version: route.version || null })
      setRoutes((current) => current.map((item) => item.capability === route.capability ? { ...item, ...result, provider_options: item.provider_options } : item))
      setNotice(`${capabilityLabel(route.capability)} 路由已更新`)
    } catch (cause) {
      if ((cause as any)?.response?.status === 409) await refresh()
      setError(errMsg(cause, '能力路由更新失败'))
    } finally {
      setPending(null)
    }
  }

  const toggleRouteProvider = async (route: DataSourceRoute, provider: string, checked: boolean) => {
    if (pending) return
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
    setPending(`route:${route.capability}`)
    try {
      const result = await saveDataSourceRoute(route.capability, { mode: route.mode, providers, expected_version: route.version || null })
      setRoutes((items) => items.map((item) => item.capability === route.capability ? { ...item, ...result, provider_options: item.provider_options } : item))
      setNotice(`${capabilityLabel(route.capability)} 数据源选择已更新`)
    } catch (cause) {
      if ((cause as any)?.response?.status === 409) await refresh()
      setError(errMsg(cause, '数据源选择更新失败'))
    } finally {
      setPending(null)
    }
  }

  const moveProvider = async (route: DataSourceRoute, index: number, delta: number) => {
    const target = index + delta
    if (pending || target < 0 || target >= route.providers.length) return
    const providers = [...route.providers]
    ;[providers[index], providers[target]] = [providers[target], providers[index]]
    setPending(`order:${route.capability}`)
    try {
      const result = await saveDataSourceRoute(route.capability, { mode: route.mode, providers, expected_version: route.version || null })
      setRoutes((current) => current.map((item) => item.capability === route.capability ? { ...item, ...result, provider_options: item.provider_options } : item))
      setNotice(`${capabilityLabel(route.capability)} 首选来源顺序已更新`)
    } catch (cause) {
      if ((cause as any)?.response?.status === 409) await refresh()
      setError(errMsg(cause, '来源顺序更新失败'))
    } finally {
      setPending(null)
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
          <button className="btn btn-ghost" onClick={() => void refresh()} disabled={Boolean(pending)}>刷新状态</button>
        </div>
        {notice && <p className="test-result up" role="status">{notice}</p>}
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
            {source.available === false && <p className="test-result down" role="status">暂不可用：{source.unavailable_reason || '尚未接入可验证生产接口'}</p>}
            <p className="caption" style={{ margin: '8px 0' }}>{probeLabel(source)}{source.key_hint ? ` · 凭证 ${source.key_hint}` : ''}</p>
            {source.capabilities.length > 0 && <div className="field mt8">
              <label htmlFor={`${source.provider}-capability`}>测试能力</label>
              <select id={`${source.provider}-capability`} className="input" value={selectedCapabilities[source.provider] || source.capabilities[0]} onChange={(event) => setSelectedCapabilities((current) => ({ ...current, [source.provider]: event.target.value }))}>
                {source.capabilities.map((capability) => <option key={capability} value={capability}>{capabilityLabel(capability)}（{capability}）</option>)}
              </select>
            </div>}
            {source.credential_fields.map((field) => (
              <div className="field mt8" key={field}>
                <label htmlFor={`${source.provider}-${field}`}>{field === 'token' ? 'Token' : field}</label>
                <input
                  id={`${source.provider}-${field}`}
                  className="input mono"
                  type={isTokenField(field) ? 'password' : 'text'}
                  value={credentials[source.provider]?.[field] || ''}
                  placeholder={source.key_hint ? '已配置，留空保持不变' : `请输入${field}`}
                  onChange={(event) => setCredential(source.provider, field, event.target.value)}
                />
              </div>
            ))}
            <div className="flex mt16 wrap">
              <button className="btn btn-dark" onClick={() => void save(source)} disabled={Boolean(pending) || source.available === false}>{pending === `save:${source.provider}` ? '保存中…' : '保存配置'}</button>
              <button className="btn btn-ghost" onClick={() => void runTest(source)} disabled={Boolean(pending) || source.available === false}>{pending === `test:${source.provider}` ? '测试中…' : '测试连接'}</button>
              <button className="btn-text" onClick={() => void toggle(source)} disabled={Boolean(pending) || source.version <= 0 || source.available === false}>{source.enabled ? '停用' : '启用'}</button>
            </div>
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
              <td className="small"><div>{route.providers.join(' → ')}</div><div className="flex mt8" style={{ flexDirection: 'column', alignItems: 'flex-start' }}>{(route.provider_options || []).map((option) => { const active = route.providers.includes(option.provider); const index = route.providers.indexOf(option.provider); return <label key={option.provider} className="caption" style={{ display: 'flex', alignItems: 'center', gap: 6 }}><input type="checkbox" checked={active} disabled={Boolean(pending) || option.selectable === false || (route.mode === 'fixed' && active)} onChange={(event) => void toggleRouteProvider(route, option.provider, event.target.checked)} />{option.display_name}（{option.provider}）{option.available === false ? `：${option.unavailable_reason || '不可用'}` : !option.enabled ? '：未启用' : ''}{active && route.mode === 'auto' && <><button className="btn-text" disabled={Boolean(pending) || index === 0} onClick={() => void moveProvider(route, index, -1)} aria-label={`${option.provider} 上移`}>↑</button><button className="btn-text" disabled={Boolean(pending) || index === route.providers.length - 1} onClick={() => void moveProvider(route, index, 1)} aria-label={`${option.provider} 下移`}>↓</button></>}</label> })}</div></td>
              <td className="flex"><button className="btn-text" disabled={Boolean(pending)} onClick={() => void updateRoute(route, route.mode === 'auto' ? 'fixed' : 'auto')}>{route.mode === 'auto' ? '改为固定' : '改为自动'}</button></td>
            </tr>)}</tbody>
          </table>
        </div>
      </section>
    </div>
  )
}
