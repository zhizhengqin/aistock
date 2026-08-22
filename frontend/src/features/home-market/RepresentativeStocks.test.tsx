import { expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import RepresentativeStocks from './RepresentativeStocks'

it('shows stock name/code/price/change/turnover/market cap', () => {
  render(<RepresentativeStocks selectedBoard={{ kind: 'industry', board_code: 'BK0475', board_name: '银行' }} items={[{ code: '600000.SS', name: '浦发银行', price: 10.2, change_pct: -1.2, turnover: 100000, market_cap: 900000000, rank: 1 }]} loading={false} error="" meta={{ provider: '东方财富', freshness: 'fresh' }} onRetry={() => undefined} />)
  expect(screen.getByText('浦发银行')).toBeInTheDocument()
  expect(screen.getByText('600000.SS')).toBeInTheDocument()
  expect(screen.getByText('−1.20%')).toBeInTheDocument()
  expect(screen.getByText(/成交额/)).toBeInTheDocument()
  expect(screen.getByText(/市值/)).toBeInTheDocument()
})

it('retry button invokes action when the constituent request fails', async () => {
  const onRetry = vi.fn()
  render(<RepresentativeStocks selectedBoard={{ kind: 'industry', board_code: 'BK0475', board_name: '银行' }} items={[]} loading={false} error="代表个股加载失败，请重试" meta={null} onRetry={onRetry} />)
  await userEvent.click(screen.getByRole('button', { name: '重试' }))
  expect(onRetry).toHaveBeenCalled()
})
