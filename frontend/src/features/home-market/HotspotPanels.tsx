import type { DataMeta, MarketHotspot, MarketKind, SelectedBoard } from './types'
import { formatDateTime, formatSignedPct, signedClass, trendLabel } from './format'

interface Props {
  title: string
  kind: MarketKind
  items: MarketHotspot[]
  selectedBoard: SelectedBoard | null
  loading: boolean
  error: string
  meta: DataMeta | null
  onSelect: (item: MarketHotspot) => void
}

export default function HotspotPanels({ title, kind, items, selectedBoard, loading, error, meta, onSelect }: Props) {
  return (
    <section className="card hotspot-panel" aria-label={title}>
      <div className="between wrap">
        <div>
          <h2 className="card-title" style={{ margin: 0 }}>{title}</h2>
          <p className="caption mt8">热度分 = 涨跌强度、成交活跃度和上涨宽度的透明综合评分</p>
        </div>
        {meta?.freshness === 'stale' && <span className="badge hold">历史数据</span>}
      </div>
      {meta?.freshness === 'stale' && <p className="caption hotspot-meta">最近有效数据：{meta.provider || '历史快照'} · 交易日 {meta.trade_date || '暂无'} · 抓取 {formatDateTime(meta.fetched_at)}</p>}
      {error && <div className="status-banner datahub-error mt16" role="alert">{error}</div>}
      {loading && items.length === 0 && <div className="empty mt16">正在加载{kind === 'industry' ? '行业板块' : '题材'}...</div>}
      {!loading && !error && items.length === 0 && <div className="empty mt16">暂无可用{kind === 'industry' ? '行业板块' : '题材'}数据</div>}
      {items.length > 0 && (
        <div className="hotspot-list mt16">
          {items.map((item) => {
            const active = selectedBoard?.kind === item.kind && selectedBoard.board_code === item.board_code
            return (
              <button
                type="button"
                className={`hotspot-item${active ? ' selected' : ''}`}
                key={`${item.kind}-${item.board_code}`}
                aria-pressed={active}
                onClick={() => onSelect(item)}
              >
                <span className="hotspot-rank mono">{item.rank}</span>
                <span className="hotspot-name">{item.board_name}<small className="muted mono">{item.board_code}</small></span>
                <span className={`hotspot-change mono ${signedClass(item.change_pct)}`}>{formatSignedPct(item.change_pct)}</span>
                <span className="hotspot-score mono">{item.hot_score.toFixed(1)}</span>
                <span className={`trend-tag trend-${item.trend_status}`}>{trendLabel(item.trend_status)}</span>
              </button>
            )
          })}
        </div>
      )}
    </section>
  )
}
