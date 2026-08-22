import { expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import HotspotPanels from './HotspotPanels'
import type { MarketHotspot } from './types'

const item: MarketHotspot = { board_code: 'BK0475', board_name: '银行', kind: 'industry', change_pct: 1.25, hot_score: 86.4, rank: 1, trend_status: 'heating', streak_days: 3, data_at: '2026-08-22T07:30:00Z', trade_date: '2026-08-22' }

it('renders accessible hotspot button fields and selected state', async () => {
  const onSelect = vi.fn()
  render(<HotspotPanels title="热门板块" kind="industry" items={[item]} selectedBoard={null} loading={false} error="" meta={{ provider: '东方财富', freshness: 'fresh' }} onSelect={onSelect} />)
  const button = screen.getByRole('button', { name: /银行/ })
  expect(button).toHaveAttribute('aria-pressed', 'false')
  expect(button).toHaveTextContent('+1.25%')
  expect(button).toHaveTextContent('86.4')
  expect(button).toHaveTextContent('升温')
  await userEvent.click(button)
  expect(onSelect).toHaveBeenCalledWith(item)
})

it('shows stale source and date without replacing the data', () => {
  render(<HotspotPanels title="热门题材" kind="theme" items={[{ ...item, kind: 'theme' }]} selectedBoard={{ kind: 'theme', board_code: 'BK0475', board_name: '银行' }} loading={false} error="" meta={{ provider: '历史快照', freshness: 'stale', trade_date: '2026-08-21', fetched_at: '2026-08-22T01:00:00Z' }} onSelect={() => undefined} />)
  expect(screen.getByText(/历史数据/)).toBeInTheDocument()
  expect(screen.getByText(/2026-08-21/)).toBeInTheDocument()
})
