import HotspotPanels from '../features/home-market/HotspotPanels'
import MarketTreemap from '../features/home-market/MarketTreemap'
import RepresentativeStocks from '../features/home-market/RepresentativeStocks'
import { formatDateTime, formatSignedPct, signedClass } from '../features/home-market/format'
import { useHomeMarketData } from '../features/home-market/useHomeMarketData'

export default function Home() {
  const market = useHomeMarketData()
  const indexMeta = market.indices.meta

  return (
    <div className="home-market-page">
      <section className="home-index-section">
        <div className="between wrap home-section-heading">
          <div>
            <span className="section-label">大盘指数</span>
            <p className="caption mt8">五大指数摘要 · 数据时间以接口元数据为准</p>
          </div>
          <div className="home-refresh-meta">
            <span className="caption">{indexMeta?.data_at ? `更新于 ${formatDateTime(indexMeta.data_at)}` : '加载中...'}</span>
            <button type="button" className="btn-text" onClick={() => void market.refresh()}>刷新</button>
          </div>
        </div>
        <div className="kpi-grid home-index-grid">
          {market.indices.meta?.freshness === 'stale' && <div className="status-banner stale" role="status" style={{ gridColumn: '1/-1' }}>最近有效行情：{market.indices.meta.provider || '备用数据源'} · 交易日 {market.indices.meta.trade_date || '暂无'} · 抓取 {formatDateTime(market.indices.meta.fetched_at)}</div>}
          {market.indices.error && <div className="status-banner datahub-error" role="alert" style={{ gridColumn: '1/-1' }}>{market.indices.error}</div>}
          {market.indices.data.map((index) => (
            <div className="kpi" key={index.code}>
              <div className="k-label">{index.name}</div>
              <div className="k-value mono">{index.price.toLocaleString('zh-CN', { minimumFractionDigits: 2 })}</div>
              <div className={`k-sub mono ${signedClass(index.change_pct)}`}>{formatSignedPct(index.change_pct)}</div>
            </div>
          ))}
          {market.indices.data.length === 0 && !market.indices.error && <div className="empty" style={{ gridColumn: '1/-1' }}>{market.indices.loading ? '指数数据加载中...' : '暂无指数数据'}</div>}
        </div>
      </section>

      <section className="home-hotspot-grid" aria-label="今日市场热点">
        <HotspotPanels title="热门板块" kind="industry" items={market.industry.data} selectedBoard={market.selectedBoard} loading={market.industry.loading} error={market.industry.error} meta={market.industry.meta} onSelect={(item) => market.selectBoard({ kind: item.kind, board_code: item.board_code, board_name: item.board_name, trade_date: item.trade_date })} />
        <HotspotPanels title="热门题材" kind="theme" items={market.theme.data} selectedBoard={market.selectedBoard} loading={market.theme.loading} error={market.theme.error} meta={market.theme.meta} onSelect={(item) => market.selectBoard({ kind: item.kind, board_code: item.board_code, board_name: item.board_name, trade_date: item.trade_date })} />
      </section>

      <RepresentativeStocks selectedBoard={market.selectedBoard} items={market.constituents.data} loading={market.constituents.loading} error={market.constituents.error} meta={market.constituents.meta} onRetry={market.retryConstituents} />

      <MarketTreemap market={market} />
    </div>
  )
}
