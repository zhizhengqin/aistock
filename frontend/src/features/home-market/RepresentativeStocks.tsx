import type { DataMeta, RepresentativeStock, SelectedBoard } from './types'
import { formatAmount, formatDateTime, formatSignedPct, signedClass } from './format'

interface Props {
  selectedBoard: SelectedBoard | null
  items: RepresentativeStock[]
  loading: boolean
  error: string
  meta: DataMeta | null
  onRetry: () => void
}

export default function RepresentativeStocks({ selectedBoard, items, loading, error, meta, onRetry }: Props) {
  return (
    <section className="card representative-stocks" aria-label="代表个股">
      <div className="between wrap">
        <div>
          <h2 className="card-title" style={{ margin: 0 }}>代表个股</h2>
          <p className="caption mt8">{selectedBoard ? `${selectedBoard.board_name} · ${selectedBoard.board_code}` : '选择一个热点板块或题材查看代表个股'}</p>
        </div>
        {meta?.freshness === 'stale' && <span className="badge hold">历史数据</span>}
      </div>
      {meta?.freshness === 'stale' && <p className="caption hotspot-meta">最近有效数据：{meta.provider || '历史快照'} · 交易日 {meta.trade_date || '暂无'} · 抓取 {formatDateTime(meta.fetched_at)}</p>}
      {error && <div className="status-banner datahub-error mt16" role="alert">{error}<button type="button" className="btn-text ml8" onClick={onRetry}>重试</button></div>}
      {loading && items.length === 0 && <div className="empty mt16">正在加载代表个股...</div>}
      {!loading && !error && items.length === 0 && <div className="empty mt16">暂无代表个股数据</div>}
      {items.length > 0 && (
        <div className="stock-table-wrap mt16">
          <table className="table stock-table">
            <thead><tr><th>股票</th><th className="num">最新价</th><th className="num">涨跌幅</th><th className="num">成交额</th><th className="num">市值</th></tr></thead>
            <tbody>
              {items.slice(0, 10).map((stock) => (
                <tr key={stock.code}>
                  <td><span>{stock.name}</span><small className="muted mono stock-code">{stock.code}</small></td>
                  <td className="num mono">{stock.price == null ? '暂无' : stock.price.toFixed(2)}</td>
                  <td className={`num mono ${signedClass(stock.change_pct)}`}>{formatSignedPct(stock.change_pct)}</td>
                  <td className="num mono">{formatAmount(stock.turnover)}</td>
                  <td className="num mono">{formatAmount(stock.market_cap)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}
