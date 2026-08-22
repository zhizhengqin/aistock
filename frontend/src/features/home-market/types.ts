export type MarketKind = 'industry' | 'theme'
export type TrendStatus = 'new' | 'heating' | 'cooling' | 'steady' | 'insufficient_history'

export interface DataMeta {
  capability?: string
  provider?: string
  data_at?: string | null
  fetched_at?: string | null
  freshness?: 'fresh' | 'stale'
  fallback_used?: boolean
  warnings?: string[]
  trade_date?: string | null
  source?: string
}

export interface DatasetState<T> {
  data: T
  meta: DataMeta | null
  loading: boolean
  error: string
}

export interface IndexData {
  code: string
  name: string
  price: number
  change_pct: number
  data_at?: string | null
}

export interface MarketHotspot {
  board_code: string
  board_name: string
  kind: MarketKind
  change_pct: number | null
  turnover?: number | null
  market_cap?: number | null
  rise_count?: number | null
  fall_count?: number | null
  flat_count?: number | null
  leader_code?: string | null
  leader_name?: string | null
  leader_change_pct?: number | null
  hot_score: number
  rank: number
  trend_status: TrendStatus
  streak_days?: number
  rank_change?: number | null
  data_at?: string | null
  trade_date?: string | null
}

export interface RepresentativeStock {
  code: string
  name: string
  price?: number | null
  change_pct?: number | null
  turnover?: number | null
  market_cap?: number | null
  rank?: number
  data_at?: string | null
  trade_date?: string | null
}

export interface MarketCloudNode {
  code: string
  name: string
  kind: MarketKind
  value: number
  change_pct?: number | null
  market_cap?: number | null
  data_at?: string | null
  trade_date?: string | null
}

export interface SelectedBoard {
  kind: MarketKind
  board_code: string
  board_name: string
  trade_date?: string | null
}
