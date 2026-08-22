import { useCallback, useEffect, useRef, useState } from 'react'
import client from '../../api/client'
import type { DataMeta, DatasetState, IndexData, MarketCloudNode, MarketHotspot, MarketKind, RepresentativeStock, SelectedBoard } from './types'

const initial = <T,>(data: T): DatasetState<T> => ({ data, meta: null, loading: false, error: '' })

function responseData<T>(response: any, key: 'items' | 'nodes'): { data: T; meta: DataMeta | null } {
  const envelope = response?.data || {}
  const body = envelope.data ?? envelope
  const data = Array.isArray(body) ? body : body?.[key] ?? []
  return { data: data as T, meta: envelope.meta ?? body?.meta ?? null }
}

function errorMessage(error: any, fallback: string): string {
  if (error?.response?.status === 503) return `${fallback}暂不可用，请稍后重试`
  return `${fallback}加载失败，请重试`
}

export function useHomeMarketData() {
  const [indices, setIndices] = useState<DatasetState<IndexData[]>>(initial([]))
  const [industry, setIndustry] = useState<DatasetState<MarketHotspot[]>>(initial([]))
  const [theme, setTheme] = useState<DatasetState<MarketHotspot[]>>(initial([]))
  const [cloud, setCloud] = useState<DatasetState<MarketCloudNode[]>>(initial([]))
  const [constituents, setConstituents] = useState<DatasetState<RepresentativeStock[]>>(initial([]))
  const [selectedBoard, setSelectedBoard] = useState<SelectedBoard | null>(null)
  const [cloudKind, setCloudKindState] = useState<MarketKind>('industry')
  const constituentController = useRef<AbortController | null>(null)
  const constituentSequence = useRef(0)
  const cloudController = useRef<AbortController | null>(null)
  const cloudSequence = useRef(0)

  const loadIndices = useCallback(async () => {
    setIndices((previous) => ({ ...previous, loading: true, error: '' }))
    try {
      const response = await client.get('/stocks/market-indices')
      const body = responseData<IndexData[]>(response, 'items')
      setIndices({ data: body.data, meta: body.meta, loading: false, error: '' })
    } catch (error) {
      setIndices((previous) => ({ ...previous, loading: false, error: errorMessage(error, '行情数据') }))
    }
  }, [])

  const loadHotspots = useCallback(async (kind: MarketKind) => {
    const setter = kind === 'industry' ? setIndustry : setTheme
    setter((previous) => ({ ...previous, loading: true, error: '' }))
    try {
      const response = await client.get('/stocks/market-hotspots', { params: { kind, limit: 12 } })
      const body = responseData<MarketHotspot[]>(response, 'items')
      setter({ data: body.data, meta: body.meta, loading: false, error: '' })
    } catch (error) {
      setter((previous) => ({ ...previous, loading: false, error: errorMessage(error, kind === 'industry' ? '热门板块数据' : '热门题材数据') }))
    }
  }, [])

  const loadCloud = useCallback(async (kind: MarketKind) => {
    cloudController.current?.abort()
    const controller = new AbortController()
    cloudController.current = controller
    const sequence = ++cloudSequence.current
    setCloud((previous) => ({ ...previous, loading: true, error: '' }))
    try {
      const response = await client.get('/stocks/market-cloud', { params: { kind, limit: 80 }, signal: controller.signal })
      if (sequence !== cloudSequence.current) return
      const body = responseData<MarketCloudNode[]>(response, 'nodes')
      setCloud({ data: body.data, meta: body.meta, loading: false, error: '' })
    } catch (error: any) {
      if (sequence !== cloudSequence.current || error?.name === 'CanceledError' || error?.code === 'ERR_CANCELED') return
      setCloud((previous) => ({ ...previous, loading: false, error: errorMessage(error, '大盘云图') }))
    }
  }, [])

  const loadConstituents = useCallback(async (board: SelectedBoard) => {
    constituentController.current?.abort()
    const controller = new AbortController()
    constituentController.current = controller
    const sequence = ++constituentSequence.current
    setConstituents((previous) => ({ ...previous, loading: true, error: '' }))
    try {
      const response = await client.get(`/stocks/boards/${board.board_code}/constituents`, {
        params: { kind: board.kind, limit: 20 },
        signal: controller.signal,
      })
      if (sequence !== constituentSequence.current) return
      const body = responseData<RepresentativeStock[]>(response, 'items')
      setConstituents({ data: body.data, meta: body.meta, loading: false, error: '' })
    } catch (error: any) {
      if (sequence !== constituentSequence.current || error?.name === 'CanceledError' || error?.code === 'ERR_CANCELED') return
      setConstituents((previous) => ({ ...previous, loading: false, error: errorMessage(error, '代表个股数据') }))
    }
  }, [])

  const selectBoard = useCallback((board: SelectedBoard) => {
    setSelectedBoard(board)
    setConstituents({ data: [], meta: null, loading: true, error: '' })
    void loadConstituents(board)
  }, [loadConstituents])

  const setCloudKind = useCallback((kind: MarketKind) => {
    setCloudKindState(kind)
    void loadCloud(kind)
  }, [loadCloud])

  const refresh = useCallback(async () => {
    const boardAtRefresh = selectedBoard
    const requests: Promise<unknown>[] = [loadIndices(), loadHotspots('industry'), loadHotspots('theme'), loadCloud(cloudKind)]
    if (boardAtRefresh) requests.push(loadConstituents(boardAtRefresh))
    await Promise.all(requests)
  }, [cloudKind, loadCloud, loadConstituents, loadHotspots, loadIndices, selectedBoard])

  const retryConstituents = useCallback(() => {
    if (selectedBoard) void loadConstituents(selectedBoard)
  }, [loadConstituents, selectedBoard])

  useEffect(() => {
    void loadIndices()
    void loadHotspots('industry')
    void loadHotspots('theme')
    void loadCloud('industry')
    const interval = window.setInterval(() => void loadIndices(), 60000)
    return () => {
      window.clearInterval(interval)
      constituentController.current?.abort()
      constituentSequence.current += 1
      cloudController.current?.abort()
      cloudSequence.current += 1
    }
  }, [loadCloud, loadHotspots, loadIndices])

  useEffect(() => {
    if (selectedBoard) return
    const board = industry.data[0] || theme.data[0]
    if (board) selectBoard({ kind: board.kind, board_code: board.board_code, board_name: board.board_name, trade_date: board.trade_date })
  }, [industry.data, selectedBoard, selectBoard, theme.data])

  return { indices, industry, theme, cloud, constituents, selectedBoard, cloudKind, selectBoard, setCloudKind, refresh, retryConstituents }
}

export type HomeMarketData = ReturnType<typeof useHomeMarketData>
