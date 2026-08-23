import { expect, it, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
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

it('uses the API area weight without fabricating displayed market cap and clamps colors', () => {
  render(<MarketTreemap market={market()} />)
  expect(chartProps.option.series[0].data[0].value).toBe(10)
  expect(screen.getAllByText(/成交额/).length).toBeGreaterThan(0)
  expect(screen.getAllByText('银行')).not.toHaveLength(0)
})

it('uses turnover then equal weight for stock areas and exposes label tooltip settings', async () => {
  const m = market({
    constituents: { data: [{ code: '600000.SS', name: '浦发银行', price: 10, change_pct: -2, market_cap: null, turnover: 500 }], meta: null, loading: false, error: '' },
  })
  render(<MarketTreemap market={m} />)
  chartProps.onEvents.click({ data: { code: 'BK0001', name: '银行' } })
  await waitFor(() => expect(chartProps.option.series[0].data[0].value).toBe(500))
  const series = chartProps.option.series[0]
  expect(chartProps.option.tooltip).toMatchObject({ show: true, trigger: 'item', confine: true })
  expect(chartProps.option.tooltip.renderMode).toBe('richText')
  expect(series.label.show).toBe(true)
  expect(series.label.overflow).toBe('truncate')
  expect(series.label.color).toBe('#201515')
  expect(series.label.fontWeight).toBe(600)
  expect(series.label.textBorderColor).toBe('#fffefb')
  expect(series.label.textBorderWidth).toBe(2)
  expect(series.label.formatter({ data: { name: '浦发银行', change_pct: -2 } })).toBe('浦发银行\n−2.00%')
  const tooltip = chartProps.option.tooltip.formatter({ data: { name: '浦发银行', code: '600000.SS', change_pct: -2, market_cap: null, turnover: 500, value: 500, area_basis: '成交额', area_value: 500 } })
  expect(tooltip).toContain('浦发银行')
  expect(tooltip).toContain('−2.00%')
  expect(tooltip).toContain('成交额')
  expect(tooltip).not.toContain('<')
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

it('keeps the selected board area basis in fixed detail while showing constituents', async () => {
  const m = market()
  render(<MarketTreemap market={m} />)
  chartProps.onEvents.click({ data: { code: 'BK0001', name: '银行' } })
  await waitFor(() => expect(screen.getByRole('button', { name: '返回板块云图' })).toBeInTheDocument())
  expect(document.querySelector('.treemap-detail')).toHaveTextContent('成交额：10')
})

it('reuses a compact quick定位 select for board drilldown', async () => {
  const m = market()
  render(<MarketTreemap market={m} />)
  const select = screen.getByRole('combobox', { name: '快速定位行业' })
  expect(screen.queryByRole('button', { name: /银行.*\+2.00%/ })).not.toBeInTheDocument()
  await userEvent.selectOptions(select, 'BK0001')
  expect(m.selectBoard).toHaveBeenCalled()
  expect(await screen.findByRole('combobox', { name: '快速定位个股' })).toBeInTheDocument()
})
