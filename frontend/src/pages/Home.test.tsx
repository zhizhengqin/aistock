import { describe, expect, it, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import Home from './Home'

const { get } = vi.hoisted(() => ({ get: vi.fn() }))
vi.mock('../api/client', () => ({ default: { get } }))

const meta = { provider: '东方财富', freshness: 'fresh', data_at: '2026-08-22T07:30:00Z', fetched_at: '2026-08-22T07:30:01Z', trade_date: '2026-08-22' }
const item = (kind: 'industry' | 'theme', code: string) => ({ board_code: code, board_name: code === 'BK0001' ? '银行' : '新能源', kind, change_pct: 1.2, hot_score: 80, rank: 1, trend_status: 'steady', streak_days: 1, data_at: meta.data_at, trade_date: meta.trade_date })

describe('Home market hotspot center', () => {
  beforeEach(() => {
    get.mockReset()
    get.mockImplementation((path: string, config?: { params?: { kind?: string; board_code?: string } }) => {
      if (path.includes('market-indices')) return Promise.resolve({ data: { data: [{ code: '000001.SS', name: '上证指数', price: 3000, change_pct: 1.1 }], meta } })
      if (path.includes('market-hotspots')) return Promise.resolve({ data: { data: { kind: config?.params?.kind, items: [item(config?.params?.kind as 'industry' | 'theme', config?.params?.kind === 'theme' ? 'BK0002' : 'BK0001')], meta }, meta } })
      if (path.includes('market-cloud')) return Promise.resolve({ data: { data: { kind: config?.params?.kind, nodes: [], meta }, meta } })
      return Promise.resolve({ data: { data: { kind: config?.params?.kind || 'industry', board_code: config?.params?.board_code, items: [{ code: '600000.SS', name: '浦发银行', price: 10, change_pct: 1, rank: 1 }], meta }, meta } })
    })
  })

  it('shows industry and theme panels with signed hotspot values and no fixed periods', async () => {
    render(<Home />)
    expect(await screen.findByRole('region', { name: '热门板块' })).toBeInTheDocument()
    expect(screen.getByRole('region', { name: '热门题材' })).toBeInTheDocument()
    expect(screen.getAllByText('+1.20%').length).toBe(2)
    expect(screen.queryByText('1月')).not.toBeInTheDocument()
    expect(screen.queryByText('5年')).not.toBeInTheDocument()
  })

  it('clicking a theme updates selected representative stock area', async () => {
    render(<Home />)
    const button = await screen.findByRole('button', { name: /新能源/ })
    await userEvent.click(button)
    await waitFor(() => expect(screen.getByText('浦发银行')).toBeInTheDocument())
    expect(button).toHaveAttribute('aria-pressed', 'true')
  })

  it('keeps one panel usable when the other returns 503', async () => {
    const error = Object.assign(new Error('down'), { response: { status: 503 } })
    get.mockImplementation((path: string, config?: { params?: { kind?: string } }) => {
      if (path.includes('market-hotspots') && config?.params?.kind === 'industry') return Promise.reject(error)
      if (path.includes('market-hotspots')) return Promise.resolve({ data: { data: { kind: 'theme', items: [item('theme', 'BK0002')], meta }, meta } })
      if (path.includes('market-indices')) return Promise.resolve({ data: { data: [], meta } })
      return Promise.resolve({ data: { data: { kind: 'industry', nodes: [], meta }, meta } })
    })
    render(<Home />)
    expect(await screen.findByText('热门板块数据暂不可用，请稍后重试')).toBeInTheDocument()
    expect(await screen.findByRole('region', { name: '热门题材' })).toBeInTheDocument()
  })
})
