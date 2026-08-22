import { describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import Home from './Home'

const { get } = vi.hoisted(() => ({ get: vi.fn() }))
vi.mock('../api/client', () => ({ default: { get } }))

describe('Home DataHub 状态', () => {
  it('完全失败时显示可重试提示而不填充 0.00 行情', async () => {
    const error = Object.assign(new Error('unavailable'), { response: { status: 503 } })
    get.mockRejectedValue(error)
    render(<Home />)
    expect(await screen.findByText('行情数据暂不可用，请稍后重试')).toBeInTheDocument()
    expect(screen.queryByText('0.00')).not.toBeInTheDocument()
  })

  it('最近有效数据显示来源和数据时间', async () => {
    get.mockImplementation((path: string) => path.includes('market-indices')
      ? Promise.resolve({ data: { data: [{ code: '000001', name: '上证指数', price: 3000, change_pct: 1 }], meta: { freshness: 'stale', provider: '腾讯财经', data_at: '2026-08-22T07:30:00Z' } } })
      : Promise.resolve({ data: { data: { category: '银行金融', period: '1月', sectors: [], stocks: [] }, meta: {} } }))
    render(<Home />)
    expect(await screen.findByText(/最近有效行情：腾讯财经/)).toBeInTheDocument()
    expect(screen.getByText(/数据更新于/)).not.toHaveTextContent('加载中')
  })

  it('指数成功后即使板块失败也显示最近一次行情更新时间', async () => {
    const error = Object.assign(new Error('unavailable'), { response: { status: 503 } })
    get.mockImplementation((path: string) => path.includes('market-indices')
      ? Promise.resolve({ data: { data: [{ code: '000001', name: '上证指数', price: 3000, change_pct: 1 }], meta: { freshness: 'fresh', data_at: '2026-08-22T07:30:00Z' } } })
      : Promise.reject(error))
    render(<Home />)
    expect(await screen.findByText(/数据更新于/)).not.toHaveTextContent('加载中')
  })

  it('指数和板块 DataHub 错误使用独立错误样式', async () => {
    const error = Object.assign(new Error('unavailable'), { response: { status: 503 } })
    get.mockRejectedValue(error)
    render(<Home />)
    expect(await screen.findByText('行情数据暂不可用，请稍后重试')).toHaveClass('datahub-error')
    expect(await screen.findByText('板块数据暂不可用，请稍后重试')).toHaveClass('datahub-error')
  })

  it('首次挂载只请求一次板块数据，指数失败时热力图显示不可用状态', async () => {
    get.mockClear()
    const error = Object.assign(new Error('unavailable'), { response: { status: 503 } })
    get.mockImplementation((path: string) => path.includes('market-indices')
      ? Promise.reject(error)
      : Promise.resolve({ data: { data: { category: '银行金融', period: '1月', sectors: [], stocks: [] }, meta: {} } }))
    render(<Home />)
    await waitFor(() => expect(get.mock.calls.filter(([path]) => path.includes('sectors/overview'))).toHaveLength(1))
    expect(await screen.findAllByText('指数行情暂不可用，请重试')).toHaveLength(1)
  })

  it('板块返回最近有效数据时显示来源和数据时间', async () => {
    get.mockImplementation((path: string) => path.includes('market-indices')
      ? Promise.resolve({ data: { data: [{ code: '000001', name: '上证指数', price: 3000, change_pct: 1 }], meta: { freshness: 'fresh' } } })
      : Promise.resolve({ data: { data: { category: '银行金融', period: '1月', sectors: [], stocks: [] }, meta: { freshness: 'stale', provider: '东方财富', data_at: '2026-08-22T07:30:00Z' } } }))
    render(<Home />)
    expect(await screen.findByText(/最近有效板块数据：东方财富/)).toBeInTheDocument()
  })
})
