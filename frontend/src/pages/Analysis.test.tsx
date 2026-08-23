import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import Analysis from './Analysis'

const { get, post } = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }))
vi.mock('../api/client', () => ({ default: { get, post } }))
vi.mock('echarts-for-react', () => ({ default: () => <div data-testid="mock-chart" /> }))

const partialTask = {
  id: 7,
  task_type: 'stock_analysis',
  status: 'running',
  progress: 62,
  phase: 'analyzing',
  error: null,
  result: null,
  steps: [
    { key: 'technical', label: '技术面分析师', status: 'completed', result: { trend: '震荡向上', short_trend: '短线偏强', mid_trend: '中期震荡', long_trend: '长期向上', score: 72, pattern: '上升通道' }, error: null },
    { key: 'fundamental', label: '基本面分析师', status: 'analyzing', result: null, error: null },
    { key: 'capital', label: '资金面分析师', status: 'waiting', result: null, error: null },
    { key: 'news', label: '消息面分析师', status: 'waiting', result: null, error: null },
    { key: 'sentiment', label: '情绪面分析师', status: 'waiting', result: null, error: null },
    { key: 'risk', label: '风险分析师', status: 'waiting', result: null, error: null },
    { key: 'chief', label: '首席分析师', status: 'waiting', result: null, error: null },
  ],
}

const oldReport = {
  stock_code: '600519',
  stock_name: '贵州茅台',
  stock_info: { price: 1500, change_pct: 1.2, pe_ttm: 20, pb: 2, market_cap: 10000, industry: '白酒' },
  indicators: { ma: {}, macd: {}, rsi: {}, kdj: {}, boll: {} },
  analysts: { technical: { trend: '震荡向上', score: 72 } },
  decision: { rating: '持有', target_price: 1550, stop_loss: 1400, confidence: 70, entry_range: '1450-1500', take_profit: '1550', holding_period: '中期', position_size: '20%', risk_warning: '关注波动', key_watchpoints: [], meeting_summary: '综合判断' },
  disclaimer: '仅供参考',
  analyzed_at: '2026-08-23T00:00:00Z',
}

const snapshot = {
  info: { code: '600519.SS', name: '贵州茅台', price: 1500, change_pct: 1.2, pe_ttm: 20, pb: 2, market_cap: 10000, industry: '白酒' },
  indicators: { ma: { MA5: 1490, MA20: 1470, MA60: 1400 }, macd: { DIF: 1, DEA: 0.8, MACD: 0.2 }, rsi: { RSI: 65 }, kdj: { K: 70, D: 60, J: 80 }, boll: { UP: 1510, MID: 1480, LOW: 1450 } },
  kline: [
    { date: '2026-08-20', open: 1480, close: 1490, high: 1500, low: 1470, volume: 10000 },
    { date: '2026-08-21', open: 1490, close: 1500, high: 1510, low: 1485, volume: 11000 },
  ],
  financial: null,
  warnings: [],
}

describe('Analysis progressive task rendering', () => {
  beforeEach(() => {
    get.mockReset()
    post.mockReset()
    post.mockResolvedValue({ data: { data: { tasks: [{ task_id: 7, stock_code: '600519' }] } } })
  })

  it('renders a validated completed analyst result before the whole task succeeds', async () => {
    get.mockResolvedValue({ data: { data: partialTask } })
    render(<Analysis />)
    fireEvent.change(screen.getByPlaceholderText(/输入股票代码/), { target: { value: '600519' } })
    fireEvent.click(screen.getByRole('button', { name: '开始分析' }))

    await waitFor(() => expect(get).toHaveBeenCalledWith('/tasks/7'), { timeout: 3500 })
    expect(await screen.findByText('技术面分析师')).toBeInTheDocument()
    expect(screen.getByText('震荡向上')).toBeInTheDocument()
    expect(screen.getByText('短期：')).toBeInTheDocument()
    expect(screen.getByText('短线偏强')).toBeInTheDocument()
    expect(screen.getByText('中期：')).toBeInTheDocument()
    expect(screen.getByText('中期震荡')).toBeInTheDocument()
    expect(screen.getByText('长期：')).toBeInTheDocument()
    expect(screen.getByText('长期向上')).toBeInTheDocument()
    expect(screen.getByText('已完成')).toBeInTheDocument()
    expect(screen.getByText('基本面分析师')).toBeInTheDocument()
    expect(screen.getAllByRole('heading', { level: 3 }).map((node) => node.textContent)).toEqual([
      '技术面分析师', '基本面分析师', '资金面分析师', '消息面分析师', '情绪面分析师', '风险分析师', '首席分析师',
    ])
    expect(screen.queryByText(/"trend"|\{\s*"/)).not.toBeInTheDocument()
  })

  it('keeps old reports without risk or kline usable after final completion', async () => {
    get.mockImplementation((path: string) => path.startsWith('/tasks/')
      ? Promise.resolve({ data: { data: { ...partialTask, status: 'success', phase: 'completed', result: { report_id: 88 }, steps: [] } } })
      : Promise.resolve({ data: { data: { id: 88, report: oldReport } } }))
    render(<Analysis />)
    fireEvent.change(screen.getByPlaceholderText(/输入股票代码/), { target: { value: '600519' } })
    fireEvent.click(screen.getByRole('button', { name: '开始分析' }))
    await waitFor(() => expect(screen.getByText('AI 投研会议 · 最终决策')).toBeInTheDocument(), { timeout: 3500 })
    expect(screen.getByText('贵州茅台')).toBeInTheDocument()
    expect(screen.getAllByText('K线与成交量')).toHaveLength(1)
    expect(screen.getAllByText('技术指标')).toHaveLength(1)
    expect(screen.queryByText('分析阶段：')).not.toBeInTheDocument()
    expect(screen.queryByText('undefined')).not.toBeInTheDocument()
  })

  it('requests the stock snapshot and renders its overview while the task is partial', async () => {
    get.mockImplementation((path: string) => path === '/stocks/600519/snapshot'
      ? Promise.resolve({ data: { data: snapshot } })
      : Promise.resolve({ data: { data: partialTask } }))
    render(<Analysis />)
    fireEvent.change(screen.getByPlaceholderText(/输入股票代码/), { target: { value: '600519' } })
    fireEvent.click(screen.getByRole('button', { name: '开始分析' }))

    await waitFor(() => expect(get).toHaveBeenCalledWith('/stocks/600519/snapshot'))
    expect(await screen.findByText('贵州茅台')).toBeInTheDocument()
    expect(screen.getByText('1500')).toBeInTheDocument()
    expect(screen.getByText('K线与成交量')).toBeInTheDocument()
    expect(screen.getByText('技术指标')).toBeInTheDocument()
    expect(screen.getByText('1490')).toBeInTheDocument()
    expect(await screen.findByText('震荡向上')).toBeInTheDocument()
  })

  it('keeps polling and visible analyst results when snapshot loading fails', async () => {
    let taskPolls = 0
    get.mockImplementation((path: string) => {
      if (path === '/stocks/600519/snapshot') return Promise.reject(new Error('snapshot unavailable'))
      if (path.startsWith('/tasks/')) {
        taskPolls += 1
        return Promise.resolve({ data: { data: taskPolls < 2 ? partialTask : { ...partialTask, status: 'success', phase: 'completed', result: { report_id: 88 }, steps: [] } } })
      }
      return Promise.resolve({ data: { data: { id: 88, report: oldReport } } })
    })
    render(<Analysis />)
    fireEvent.change(screen.getByPlaceholderText(/输入股票代码/), { target: { value: '600519' } })
    fireEvent.click(screen.getByRole('button', { name: '开始分析' }))

    await waitFor(() => expect(get).toHaveBeenCalledWith('/stocks/600519/snapshot'))
    expect(await screen.findByText('股票行情概览暂不可用，分析仍在继续')).toBeInTheDocument()
    expect(await screen.findByText('震荡向上')).toBeInTheDocument()
    await waitFor(() => expect(taskPolls).toBeGreaterThanOrEqual(2), { timeout: 5000 })
  })
})
