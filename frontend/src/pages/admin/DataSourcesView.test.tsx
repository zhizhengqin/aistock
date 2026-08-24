import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import DataSourcesView from './DataSourcesView'

vi.mock('../../api/dataHub', () => ({
  listDataSources: vi.fn().mockResolvedValue({
    items: [{
      id: null,
      provider: 'tushare',
      display_name: 'Tushare Pro',
      description: '开盘啦盘后榜单',
      capabilities: ['kpl.limit_list'],
      auth_type: 'token',
      credential_fields: [{ key: 'token', label: 'Token', secret: true, required: true, help: 'Tushare Pro 访问 Token' }],
      fee_type: '积分/付费',
      update_frequency: '盘后',
      risk_note: '按能力探测',
      enabled: false,
      version: 0,
      key_hint: null,
      fingerprint: null,
      last_probe_status: null,
      last_probe_at: null,
      last_probe_latency_ms: null,
    }],
  }),
  listDataSourceRoutes: vi.fn().mockResolvedValue({ items: [] }),
  saveDataSource: vi.fn(),
  patchDataSource: vi.fn(),
  testDataSource: vi.fn().mockResolvedValue({ status: 'ok', rows: 2 }),
  testSavedDataSource: vi.fn().mockResolvedValue({ status: 'ok', rows: 1 }),
  setDataSourceEnabled: vi.fn(),
  saveDataSourceRoute: vi.fn().mockResolvedValue({ capability: 'market.indices', mode: 'auto', providers: ['tencent', 'sina'], contract_version: '1.0', version: 1 }),
}))

describe('DataSourcesView', () => {
  it('解释数据源、清空凭证并展示测试状态', async () => {
    render(<DataSourcesView />)
    expect(await screen.findByText('Tushare Pro')).toBeInTheDocument()
    expect(screen.getByText('开盘啦盘后榜单')).toBeInTheDocument()
    const input = screen.getByLabelText('Token') as HTMLInputElement
    fireEvent.change(input, { target: { value: 'secret-token' } })
    fireEvent.click(screen.getByRole('button', { name: '测试连接' }))
    await waitFor(() => expect(input.value).toBe(''))
    expect(await screen.findByText(/获取 2 行/)).toBeInTheDocument()
    expect(document.body.textContent).not.toContain('secret-token')
  })

  it('支持按具体能力探测并展示不可用来源原因', async () => {
    render(<DataSourcesView />)
    expect(await screen.findByLabelText('测试能力')).toHaveValue('kpl.limit_list')
    expect(screen.getByRole('button', { name: '测试连接' })).toBeEnabled()
  })

  it('按能力显示中文名称并可勾选候选来源加入自动路由', async () => {
    const api = await import('../../api/dataHub')
    vi.mocked(api.listDataSourceRoutes).mockResolvedValueOnce({ items: [{
      capability: 'market.indices', mode: 'auto', providers: ['tencent'], contract_version: '1.0', version: 1,
      provider_options: [
        { provider: 'tencent', display_name: '腾讯行情', available: true, enabled: true, selectable: true },
        { provider: 'sina', display_name: '新浪财经', available: true, enabled: true, selectable: true },
        { provider: 'official', display_name: '交易所官方', available: false, enabled: false, selectable: false, unavailable_reason: '尚未接入' },
      ],
    }] })
    render(<DataSourcesView />)
    expect(await screen.findByText('大盘指数')).toBeInTheDocument()
    const candidate = await screen.findByLabelText(/新浪财经（sina）/)
    fireEvent.click(candidate)
    await waitFor(() => expect(vi.mocked(api.saveDataSourceRoute)).toHaveBeenCalledWith('market.indices', expect.objectContaining({ providers: ['tencent', 'sina'] })))
    expect(screen.getByText(/尚未接入/)).toBeInTheDocument()
  })

  it('显示新增板块行情与板块成分股能力名称', async () => {
    const api = await import('../../api/dataHub')
    vi.mocked(api.listDataSourceRoutes).mockResolvedValueOnce({ items: [
      { capability: 'market.board_quotes', mode: 'auto', providers: ['eastmoney'], contract_version: '1.0', version: 1 },
      { capability: 'market.board_constituents', mode: 'auto', providers: ['eastmoney'], contract_version: '1.0', version: 1 },
    ] })
    render(<DataSourcesView />)
    expect(await screen.findByText('板块行情')).toBeInTheDocument()
    expect(await screen.findByText('板块成分股')).toBeInTheDocument()
    expect(screen.queryByText('板块概览')).not.toBeInTheDocument()
  })

  it('调整首选顺序后仍保留候选来源勾选项', async () => {
    const api = await import('../../api/dataHub')
    vi.mocked(api.listDataSourceRoutes).mockResolvedValueOnce({ items: [{
      capability: 'market.indices', mode: 'auto', providers: ['sina', 'tencent'], contract_version: '1.0', version: 1,
      provider_options: [
        { provider: 'tencent', display_name: '腾讯行情', available: true, enabled: true, selectable: true },
        { provider: 'sina', display_name: '新浪财经', available: true, enabled: true, selectable: true },
      ],
    }] })
    vi.mocked(api.saveDataSourceRoute).mockResolvedValueOnce({ capability: 'market.indices', mode: 'auto', providers: ['tencent', 'sina'], contract_version: '1.0', version: 2 })
    render(<DataSourcesView />)
    const moveUp = await screen.findByRole('button', { name: 'tencent 上移' })
    fireEvent.click(moveUp)
    await waitFor(() => expect(vi.mocked(api.saveDataSourceRoute)).toHaveBeenCalledWith('market.indices', expect.objectContaining({ providers: ['tencent', 'sina'] })))
    expect(await screen.findByLabelText(/新浪财经（sina）/)).toBeChecked()
  })

  it('管理端 DataHub 错误使用独立错误样式', async () => {
    const api = await import('../../api/dataHub')
    vi.mocked(api.listDataSources).mockRejectedValueOnce(new Error('network unavailable'))
    vi.mocked(api.listDataSourceRoutes).mockResolvedValueOnce({ items: [] })
    render(<DataSourcesView />)
    expect(await screen.findByRole('alert')).toHaveClass('datahub-error')
  })

  it('按凭证元数据渲染字段，并将测试结果隔离在对应数据源卡片按钮下方', async () => {
    const api = await import('../../api/dataHub')
    vi.mocked(api.listDataSources).mockResolvedValue({ items: [
      {
        id: null,
        provider: 'kpl_native',
        display_name: '开盘啦原生',
        description: '实验性原生接口',
        capabilities: ['kpl_native.stock_tags'],
        auth_type: 'token',
        credential_fields: [
          { key: 'user_id', label: 'UserID', secret: false, required: true, help: '合法账号 UserID' },
          { key: 'token', label: 'Token', secret: true, required: true, help: '合法 Token' },
        ],
        fee_type: '需合法账号',
        update_frequency: '盘中',
        risk_note: '实验性',
        available: true,
        unavailable_reason: null,
        enabled: false,
        version: 0,
        key_hint: null,
        fingerprint: null,
        last_probe_status: null,
        last_probe_at: null,
        last_probe_latency_ms: null,
      },
      {
        id: null,
        provider: 'sina',
        display_name: '新浪财经',
        description: '公开备用来源',
        capabilities: ['market.indices'],
        auth_type: 'none',
        credential_fields: [],
        fee_type: '免费',
        update_frequency: '盘中',
        risk_note: '字段按响应',
        available: true,
        unavailable_reason: null,
        enabled: true,
        version: 0,
        key_hint: null,
        fingerprint: null,
        last_probe_status: null,
        last_probe_at: null,
        last_probe_latency_ms: null,
      },
    ] })
    vi.mocked(api.listDataSourceRoutes).mockResolvedValueOnce({ items: [] })
    vi.mocked(api.testDataSource).mockImplementation(async ({ provider }) => provider === 'kpl_native'
      ? { status: 'error', rows: 0, message: '未登录或凭证无效' }
      : { status: 'ok', rows: 3 })

    render(<DataSourcesView />)
    const kplCard = await screen.findByText('开盘啦原生').then((heading) => heading.closest('section') as HTMLElement)
    const sinaCard = screen.getByText('新浪财经').closest('section') as HTMLElement
    expect(within(kplCard).getByLabelText('UserID')).toBeInTheDocument()
    expect(within(kplCard).getByLabelText('Token')).toHaveAttribute('type', 'password')

    fireEvent.click(within(kplCard).getByRole('button', { name: '测试连接' }))
    await waitFor(() => expect(within(kplCard).getByRole('status')).toHaveTextContent('未登录或凭证无效'))
    expect(within(kplCard).getByRole('status')).toHaveClass('datahub-error')
    expect(within(kplCard).getByRole('status')).not.toHaveClass('down')
    expect(within(sinaCard).queryByRole('status')).not.toBeInTheDocument()
    expect(kplCard.textContent!.indexOf('未登录或凭证无效')).toBeGreaterThan(kplCard.textContent!.indexOf('测试连接'))

    fireEvent.click(within(sinaCard).getByRole('button', { name: '测试连接' }))
    await waitFor(() => expect(within(sinaCard).getByRole('status')).toHaveTextContent('获取 3 行'))
    expect(within(kplCard).getByRole('status')).toHaveTextContent('未登录或凭证无效')
  })

  it('在对应能力路由操作下方显示独立操作反馈', async () => {
    const api = await import('../../api/dataHub')
    vi.mocked(api.listDataSourceRoutes).mockResolvedValueOnce({ items: [{
      capability: 'market.indices',
      mode: 'auto',
      providers: ['tencent'],
      contract_version: '1.0',
      version: 1,
      provider_options: [],
    }] })
    vi.mocked(api.saveDataSourceRoute).mockResolvedValueOnce({
      capability: 'market.indices',
      mode: 'fixed',
      providers: ['tencent'],
      contract_version: '1.0',
      version: 2,
    })

    render(<DataSourcesView />)
    const row = (await screen.findByText('大盘指数')).closest('tr') as HTMLElement
    fireEvent.click(within(row).getByRole('button', { name: '改为固定' }))

    await waitFor(() => expect(within(row).getByRole('status')).toHaveTextContent(/大盘指数.*路由已更新/))
    expect(within(row).getByRole('status')).toHaveClass('datahub-success')
    expect(within(row).getByRole('status')).not.toHaveClass('down')
  })

  it('不可用来源使用错误语义状态样式', async () => {
    const api = await import('../../api/dataHub')
    vi.mocked(api.listDataSources).mockResolvedValueOnce({ items: [{
      id: null,
      provider: 'official',
      display_name: '交易所官方',
      description: '协议占位',
      capabilities: [],
      auth_type: 'none',
      credential_fields: [],
      fee_type: '免费',
      update_frequency: '盘后',
      risk_note: '未接入',
      available: false,
      unavailable_reason: '接口尚未接入',
      enabled: false,
      version: 0,
      key_hint: null,
      fingerprint: null,
      last_probe_status: null,
      last_probe_at: null,
      last_probe_latency_ms: null,
    }] })
    vi.mocked(api.listDataSourceRoutes).mockResolvedValueOnce({ items: [] })

    render(<DataSourcesView />)
    const card = await screen.findByText('交易所官方').then((heading) => heading.closest('section') as HTMLElement)
    expect(within(card).getByRole('status')).toHaveClass('datahub-error')
  })

  it('将 profile 与四项开盘啦原生能力显示为中文名称', async () => {
    const api = await import('../../api/dataHub')
    vi.mocked(api.listDataSourceRoutes).mockResolvedValueOnce({ items: [
      { capability: 'stock.profile', mode: 'auto', providers: ['eastmoney'], contract_version: '1.0', version: 1 },
      { capability: 'kpl_native.stock_tags', mode: 'auto', providers: ['kpl_native'], contract_version: '1.0', version: 1 },
      { capability: 'kpl_native.plate_ranking', mode: 'auto', providers: ['kpl_native'], contract_version: '1.0', version: 1 },
      { capability: 'kpl_native.plate_constituents', mode: 'auto', providers: ['kpl_native'], contract_version: '1.0', version: 1 },
      { capability: 'kpl_native.stock_ranking', mode: 'auto', providers: ['kpl_native'], contract_version: '1.0', version: 1 },
    ] })

    render(<DataSourcesView />)

    expect(await screen.findByText('公司资料')).toBeInTheDocument()
    expect(screen.getByText('开盘啦原生股票标签')).toBeInTheDocument()
    expect(screen.getByText('开盘啦原生板块排行')).toBeInTheDocument()
    expect(screen.getByText('开盘啦原生板块成分')).toBeInTheDocument()
    expect(screen.getByText('开盘啦原生股票排行')).toBeInTheDocument()
  })
})
