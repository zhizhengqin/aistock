import { act, renderHook, waitFor } from '@testing-library/react'
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { useHomeMarketData } from './useHomeMarketData'

const { get } = vi.hoisted(() => ({ get: vi.fn() }))
vi.mock('../../api/client', () => ({ default: { get } }))

const meta = { provider: '东方财富', freshness: 'fresh', data_at: '2026-08-22T07:30:00Z', fetched_at: '2026-08-22T07:30:01Z', trade_date: '2026-08-22' }
const hotspot = (kind: 'industry' | 'theme', code: string) => ({ board_code: code, board_name: code, kind, change_pct: 1, hot_score: 80, rank: 1, trend_status: 'steady', streak_days: 1, data_at: meta.data_at, trade_date: meta.trade_date })

describe('useHomeMarketData', () => {
  beforeEach(() => {
    vi.useRealTimers()
    get.mockReset()
    get.mockImplementation((path: string, config?: { params?: { kind?: string } }) => {
      if (path.includes('market-indices')) return Promise.resolve({ data: { data: [], meta } })
      if (path.includes('market-hotspots')) return Promise.resolve({ data: { data: { kind: config?.params?.kind, items: [hotspot(config?.params?.kind as 'industry' | 'theme', config?.params?.kind === 'theme' ? 'BK0003' : 'BK0001')], meta }, meta } })
      if (path.includes('market-cloud')) return Promise.resolve({ data: { data: { kind: config?.params?.kind, nodes: [], meta }, meta } })
      return Promise.resolve({ data: { data: { kind: 'industry', board_code: 'BK0001', items: [], meta }, meta } })
    })
  })

  it('loads indices, industry, theme and selects the first industry board independently', async () => {
    const { result } = renderHook(() => useHomeMarketData())
    await waitFor(() => expect(result.current.selectedBoard?.board_code).toBe('BK0001'))
    expect(get.mock.calls.some(([path]) => path.includes('market-hotspots'))).toBe(true)
    expect(result.current.industry.data).toHaveLength(1)
    expect(result.current.theme.data).toHaveLength(1)
  })

  it('a slow previous constituent response cannot overwrite the latest selected board', async () => {
    let resolveFirst!: (value: unknown) => void
    let resolveSecond!: (value: unknown) => void
    get.mockImplementation((path: string, config?: { params?: { kind?: string } }) => {
      if (path.includes('constituents')) return new Promise((resolve) => {
        if (path.includes('BK0001')) resolveFirst = resolve
        else resolveSecond = resolve
      })
      if (path.includes('market-indices')) return Promise.resolve({ data: { data: [], meta } })
      if (path.includes('market-hotspots')) return Promise.resolve({ data: { data: { kind: config?.params?.kind, items: [hotspot(config?.params?.kind as 'industry' | 'theme', 'BK0001'), { ...hotspot(config?.params?.kind as 'industry' | 'theme', 'BK0002'), rank: 2, board_code: 'BK0002' }], meta }, meta } })
      return Promise.resolve({ data: { data: { kind: 'industry', nodes: [], meta }, meta } })
    })
    const { result } = renderHook(() => useHomeMarketData())
    await waitFor(() => expect(result.current.selectedBoard?.board_code).toBe('BK0001'))
    act(() => result.current.selectBoard({ kind: 'industry', board_code: 'BK0002', board_name: 'BK0002', trade_date: '2026-08-22' }))
    resolveSecond?.({ data: { data: { kind: 'industry', board_code: 'BK0002', items: [{ code: '600000.SS', name: '新选择' }], meta } } })
    resolveFirst?.({ data: { data: { kind: 'industry', board_code: 'BK0001', items: [{ code: '600001.SS', name: '旧选择' }], meta } } })
    await waitFor(() => expect(result.current.constituents.data?.[0]?.name).toBe('新选择'))
  })

  it('aborts a pending constituent request when the hook unmounts', async () => {
    let signal: AbortSignal | undefined
    get.mockImplementation((path: string, config?: { signal?: AbortSignal; params?: { kind?: string } }) => {
      if (path.includes('constituents')) {
        signal = config?.signal
        return new Promise(() => undefined)
      }
      if (path.includes('market-indices')) return Promise.resolve({ data: { data: [], meta } })
      if (path.includes('market-hotspots')) return Promise.resolve({ data: { data: { kind: config?.params?.kind, items: [hotspot(config?.params?.kind as 'industry' | 'theme', 'BK0001')], meta }, meta } })
      return Promise.resolve({ data: { data: { kind: 'industry', nodes: [], meta }, meta } })
    })
    const { result, unmount } = renderHook(() => useHomeMarketData())
    await waitFor(() => expect(result.current.selectedBoard?.board_code).toBe('BK0001'))
    unmount()
    expect(signal?.aborted).toBe(true)
  })

  it('keeps theme data when industry request fails', async () => {
    get.mockImplementation((path: string, config?: { params?: { kind?: string } }) => {
      if (path.includes('market-hotspots') && config?.params?.kind === 'industry') return Promise.reject(new Error('industry down'))
      if (path.includes('market-hotspots')) return Promise.resolve({ data: { data: { kind: 'theme', items: [hotspot('theme', 'BK0003')], meta }, meta } })
      if (path.includes('market-indices')) return Promise.resolve({ data: { data: [], meta } })
      return Promise.resolve({ data: { data: { kind: 'industry', nodes: [], meta }, meta } })
    })
    const { result } = renderHook(() => useHomeMarketData())
    await waitFor(() => expect(result.current.theme.data).toHaveLength(1))
    expect(result.current.industry.error).toBeTruthy()
  })

  it('does not let a slow old industry cloud success replace the selected theme cloud', async () => {
    let resolveIndustry!: (value: unknown) => void
    let resolveTheme!: (value: unknown) => void
    const cloudResponse = (kind: 'industry' | 'theme', code: string) => ({ data: { data: { kind, nodes: [{ code, name: code, kind, value: 1, change_pct: 1 }], meta }, meta } })
    get.mockImplementation((path: string, config?: { params?: { kind?: string } }) => {
      if (path.includes('market-cloud')) {
        return new Promise((resolve) => {
          if (config?.params?.kind === 'industry') resolveIndustry = resolve
          else resolveTheme = resolve
        })
      }
      if (path.includes('market-indices')) return Promise.resolve({ data: { data: [], meta } })
      if (path.includes('market-hotspots')) return Promise.resolve({ data: { data: { kind: config?.params?.kind, items: [], meta }, meta } })
      return Promise.resolve({ data: { data: { kind: 'industry', items: [], meta }, meta } })
    })
    const { result } = renderHook(() => useHomeMarketData())
    await waitFor(() => expect(get.mock.calls.some(([path, config]) => path.includes('market-cloud') && config?.params?.kind === 'industry')).toBe(true))
    act(() => result.current.setCloudKind('theme'))
    await waitFor(() => expect(get.mock.calls.some(([path, config]) => path.includes('market-cloud') && config?.params?.kind === 'theme')).toBe(true))
    await act(async () => { resolveTheme(cloudResponse('theme', 'THEME')) })
    await waitFor(() => expect(result.current.cloud.data[0]?.code).toBe('THEME'))
    await act(async () => { resolveIndustry(cloudResponse('industry', 'INDUSTRY')) })
    expect(result.current.cloudKind).toBe('theme')
    expect(result.current.cloud.data[0]?.code).toBe('THEME')
    expect(result.current.cloud.error).toBe('')
  })

  it('does not let a slow old industry cloud error overwrite the selected theme cloud', async () => {
    let rejectIndustry!: (error: Error) => void
    let resolveTheme!: (value: unknown) => void
    get.mockImplementation((path: string, config?: { params?: { kind?: string } }) => {
      if (path.includes('market-cloud')) {
        return new Promise((resolve, reject) => {
          if (config?.params?.kind === 'industry') rejectIndustry = reject
          else resolveTheme = resolve
        })
      }
      if (path.includes('market-indices')) return Promise.resolve({ data: { data: [], meta } })
      if (path.includes('market-hotspots')) return Promise.resolve({ data: { data: { kind: config?.params?.kind, items: [], meta }, meta } })
      return Promise.resolve({ data: { data: { kind: 'industry', items: [], meta }, meta } })
    })
    const { result } = renderHook(() => useHomeMarketData())
    await waitFor(() => expect(get.mock.calls.some(([path, config]) => path.includes('market-cloud') && config?.params?.kind === 'industry')).toBe(true))
    act(() => result.current.setCloudKind('theme'))
    await waitFor(() => expect(get.mock.calls.some(([path, config]) => path.includes('market-cloud') && config?.params?.kind === 'theme')).toBe(true))
    await act(async () => { resolveTheme({ data: { data: { kind: 'theme', nodes: [{ code: 'THEME', name: 'THEME', kind: 'theme', value: 1, change_pct: 1 }], meta }, meta } }) })
    await waitFor(() => expect(result.current.cloud.data[0]?.code).toBe('THEME'))
    await act(async () => { rejectIndustry(new Error('old cloud failed')) })
    expect(result.current.cloudKind).toBe('theme')
    expect(result.current.cloud.data[0]?.code).toBe('THEME')
    expect(result.current.cloud.error).toBe('')
  })

  it('clears old constituent data and metadata immediately when selecting another board', async () => {
    let resolveNext!: (value: unknown) => void
    get.mockImplementation((path: string, config?: { params?: { kind?: string } }) => {
      if (path.includes('constituents') && path.includes('BK0002')) return new Promise((resolve) => { resolveNext = resolve })
      if (path.includes('constituents')) return Promise.resolve({ data: { data: { kind: 'industry', board_code: 'BK0001', items: [{ code: '600000.SS', name: '旧代表股' }], meta }, meta } })
      if (path.includes('market-indices')) return Promise.resolve({ data: { data: [], meta } })
      if (path.includes('market-hotspots')) return Promise.resolve({ data: { data: { kind: config?.params?.kind, items: [hotspot(config?.params?.kind as 'industry' | 'theme', config?.params?.kind === 'theme' ? 'BK0003' : 'BK0001'), { ...hotspot('industry', 'BK0002'), rank: 2 }], meta }, meta } })
      return Promise.resolve({ data: { data: { kind: 'industry', nodes: [], meta }, meta } })
    })
    const { result } = renderHook(() => useHomeMarketData())
    await waitFor(() => expect(result.current.constituents.data[0]?.name).toBe('旧代表股'))
    act(() => result.current.selectBoard({ kind: 'industry', board_code: 'BK0002', board_name: '新板块', trade_date: '2026-08-22' }))
    expect(result.current.constituents.data).toEqual([])
    expect(result.current.constituents.meta).toBeNull()
    expect(result.current.constituents.loading).toBe(true)
    await act(async () => { resolveNext({ data: { data: { kind: 'industry', board_code: 'BK0002', items: [], meta }, meta } }) })
  })

  it('does not let refresh for a previous board overwrite a board selected during refresh', async () => {
    let refreshStarted = false
    const refreshResolvers: Array<(value: unknown) => void> = []
    const boardResponse = (code: string, name: string) => ({
      data: { data: { kind: 'industry', board_code: code, items: [{ code: `${code}.SS`, name }], meta }, meta },
    })
    get.mockImplementation((path: string, config?: { params?: { kind?: string } }) => {
      if (path.includes('constituents')) {
        if (path.includes('BK0001')) return refreshStarted ? Promise.resolve(boardResponse('BK0001', '刷新旧板块')) : Promise.resolve(boardResponse('BK0001', '初始板块'))
        return Promise.resolve(boardResponse('BK0002', '切换后板块'))
      }
      if (refreshStarted) return new Promise((resolve) => { refreshResolvers.push(resolve) })
      if (path.includes('market-indices')) return Promise.resolve({ data: { data: [], meta } })
      if (path.includes('market-hotspots')) return Promise.resolve({ data: { data: { kind: config?.params?.kind, items: [hotspot(config?.params?.kind as 'industry' | 'theme', 'BK0001'), { ...hotspot('industry', 'BK0002'), rank: 2 }], meta }, meta } })
      return Promise.resolve({ data: { data: { kind: 'industry', nodes: [], meta }, meta } })
    })
    const { result } = renderHook(() => useHomeMarketData())
    await waitFor(() => expect(result.current.constituents.data[0]?.name).toBe('初始板块'))
    refreshStarted = true
    let refreshPromise!: Promise<void>
    act(() => { refreshPromise = result.current.refresh() })
    await waitFor(() => expect(refreshResolvers).toHaveLength(4))

    act(() => result.current.selectBoard({ kind: 'industry', board_code: 'BK0002', board_name: '切换后板块', trade_date: '2026-08-22' }))
    await waitFor(() => expect(result.current.constituents.data[0]?.name).toBe('切换后板块'))
    refreshResolvers.forEach((resolve) => resolve({ data: { data: [], meta } }))
    await act(async () => { await refreshPromise })

    expect(result.current.selectedBoard?.board_code).toBe('BK0002')
    expect(result.current.constituents.data[0]?.name).toBe('切换后板块')
  })

  it('invalidates a pending cloud request when the hook unmounts', async () => {
    let resolveIndustry!: (value: unknown) => void
    let industrySignal: AbortSignal | undefined
    get.mockImplementation((path: string, config?: { params?: { kind?: string }; signal?: AbortSignal }) => {
      if (path.includes('market-cloud')) {
        industrySignal = config?.signal
        return new Promise((resolve) => { resolveIndustry = resolve })
      }
      if (path.includes('market-indices')) return Promise.resolve({ data: { data: [], meta } })
      if (path.includes('market-hotspots')) return Promise.resolve({ data: { data: { kind: config?.params?.kind, items: [], meta }, meta } })
      return Promise.resolve({ data: { data: { kind: 'industry', items: [], meta }, meta } })
    })
    const { unmount } = renderHook(() => useHomeMarketData())
    await waitFor(() => expect(industrySignal).toBeDefined())
    unmount()
    expect(industrySignal?.aborted).toBe(true)
    resolveIndustry({ data: { data: { kind: 'industry', nodes: [], meta }, meta } })
  })
})
