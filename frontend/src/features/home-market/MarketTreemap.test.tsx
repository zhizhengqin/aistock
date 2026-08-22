import { expect, it, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import MarketTreemap from './MarketTreemap'
import type { HomeMarketData } from './useHomeMarketData'

const chartProps: { option?: any; onEvents?: any } = {}
vi.mock('echarts-for-react', () => ({ default: (props: any) => { chartProps.option = props.option; chartProps.onEvents = props.onEvents; return <div data-testid="chart" /> } }))

function market(overrides: Partial<HomeMarketData> = {}): HomeMarketData {
  return {
    indices: { data: [], meta: null, loading: false, error: '' }, industry: { data: [], meta: null, loading: false, error: '' }, theme: { data: [], meta: null, loading: false, error: '' },
    cloud: { data: [{ code: 'BK0001', name: '银行', kind: 'industry', value: 10, market_cap: null, change_pct: 2 }], meta: null, loading: false, error: '' },
    constituents: { data: [{ code: '600000.SS', name: '浦发银行', price: 10, change_pct: -2, market_cap: 0 }], meta: null, loading: false, error: '' },
    selectedBoard: { kind: 'industry', board_code: 'BK0001', board_name: '银行' }, cloudKind: 'industry',
    selectBoard: vi.fn(), setCloudKind: vi.fn(), refresh: vi.fn(), retryConstituents: vi.fn(), ...overrides,
  } as HomeMarketData
}

beforeEach(() => { chartProps.option = undefined; chartProps.onEvents = undefined })

it('uses fallback layout weight without fabricating displayed market cap and clamps colors', () => {
  render(<MarketTreemap market={market()} />)
  expect(chartProps.option.series[0].data[0].value).toBeGreaterThan(0)
  expect(screen.getByText('暂无')).toBeInTheDocument()
  expect(screen.getAllByText('银行')).not.toHaveLength(0)
})

it('clicking board drills to stock level and return restores board level', async () => {
  const m = market()
  render(<MarketTreemap market={m} />)
  chartProps.onEvents.click({ data: { code: 'BK0001', name: '银行' } })
  expect(m.selectBoard).toHaveBeenCalled()
  expect(await screen.findByRole('button', { name: '返回板块云图' })).toBeInTheDocument()
  await userEvent.click(screen.getByRole('button', { name: '返回板块云图' }))
  expect(screen.queryByRole('button', { name: '返回板块云图' })).not.toBeInTheDocument()
})
