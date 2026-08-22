import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
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
      credential_fields: ['token'],
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
})
