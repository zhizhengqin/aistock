import { useState, useEffect } from 'react'
import client from '../api/client'

const CATEGORIES = ['银行金融', '科技互联网', '新能源', '大消费', '高端制造', '周期资源']
const PERIODS = ['1月', '3月', '1年', '5年', '全部']

interface IndexData {
  code: string
  name: string
  price: number
  change_pct: number
}

interface DataMeta {
  provider?: string
  freshness?: 'fresh' | 'stale'
  data_at?: string | null
  warnings?: string[]
}

interface StockItem {
  code: string
  name: string
  price: number
  change_pct: number
}

interface SectorData {
  category: string
  period: string
  sectors: { name: string; price: number; change_pct: number }[]
  stocks: StockItem[]
  updated_at: string
  period_label?: string
}

const fmtPct = (v: number) => (v >= 0 ? '+' : '\u2212') + Math.abs(v).toFixed(2) + '%'
const cls = (v: number) => (v >= 0 ? 'up' : 'down')

/* 热力图色深：幅度越大色越深（红涨 hue 27 / 绿跌 hue 155） */
function heatColor(pct: number): string {
  const m = Math.min(Math.abs(pct) / 3, 1)
  const L = 0.7 - 0.14 * m
  const C = 0.08 + 0.11 * m
  return `oklch(${L.toFixed(2)} ${C.toFixed(2)} ${pct >= 0 ? 27 : 155})`
}

export default function Home() {
  const [category, setCategory] = useState(CATEGORIES[0])
  const [period, setPeriod] = useState(PERIODS[0])
  const [indices, setIndices] = useState<IndexData[]>([])
  const [sectorData, setSectorData] = useState<SectorData | null>(null)
  const [updatedTime, setUpdatedTime] = useState('')
  const [loading, setLoading] = useState(false)
  const [indicesMeta, setIndicesMeta] = useState<DataMeta | null>(null)
  const [sectorMeta, setSectorMeta] = useState<DataMeta | null>(null)
  const [indicesError, setIndicesError] = useState('')
  const [sectorError, setSectorError] = useState('')

  const fetchIndices = async () => {
    try {
      const resp = await client.get('/stocks/market-indices')
      setIndices(resp.data.data || [])
      const meta: DataMeta | null = resp.data.meta || null
      setIndicesMeta(meta)
      const parsedTime = meta?.data_at ? new Date(meta.data_at) : new Date()
      setUpdatedTime(Number.isNaN(parsedTime.getTime()) ? new Date().toLocaleTimeString('zh-CN') : parsedTime.toLocaleTimeString('zh-CN'))
      setIndicesError('')
    } catch (error: any) {
      setIndicesError(error?.response?.status === 503 ? '行情数据暂不可用，请稍后重试' : '行情数据加载失败，请重试')
      setIndices([])
      setIndicesMeta(null)
    }
  }

  const fetchSector = async () => {
    setLoading(true)
    try {
      const resp = await client.get('/stocks/sectors/overview', { params: { category, period } })
      const data: SectorData = resp.data.data
      setSectorData(data)
      setSectorMeta(resp.data.meta || null)
      setSectorError('')
      setUpdatedTime(new Date().toLocaleTimeString('zh-CN'))
    } catch (error: any) {
      setSectorError(error?.response?.status === 503 ? '板块数据暂不可用，请稍后重试' : '板块数据加载失败，请重试')
      setSectorData(null)
      setSectorMeta(null)
    }
    finally { setLoading(false) }
  }

  useEffect(() => {
    fetchIndices()
    const id = setInterval(fetchIndices, 60000)
    return () => clearInterval(id)
  }, [])

  useEffect(() => {
    fetchSector()
  }, [category, period])

  const sectors = sectorData?.sectors || []
  const maxAbs = Math.max(...sectors.map((s) => Math.abs(s.change_pct)), 0.01)

  return (
    <>
      {/* 大盘指数 */}
      <section>
        <div className="between wrap" style={{ marginBottom: 16 }}>
          <span className="section-label" style={{ margin: 0 }}>大盘指数</span>
          <span className="caption">
            数据更新于 {updatedTime || '加载中...'} · 每分钟自动刷新{' '}
            <button className="btn-text" style={{ fontSize: 12 }} onClick={() => { fetchIndices(); fetchSector() }}>刷新</button>
          </span>
        </div>
        <div className="kpi-grid home-index-grid">
          {indicesMeta?.freshness === 'stale' && <div className="status-banner stale" role="status" style={{ gridColumn: '1/-1' }}>
            最近有效行情：{indicesMeta.provider || '备用数据源'}{indicesMeta.data_at ? ` · 数据时间 ${new Date(indicesMeta.data_at).toLocaleString('zh-CN')}` : ''}
          </div>}
          {indicesError && <div className="status-banner datahub-error" role="alert" style={{ gridColumn: '1/-1' }}>{indicesError}</div>}
          {indices.map((idx) => (
            <div className="kpi" key={idx.code}>
              <div className="k-label">{idx.name}</div>
              <div className="k-value mono">{idx.price.toLocaleString('zh-CN', { minimumFractionDigits: 2 })}</div>
              <div className={`k-sub mono ${cls(idx.change_pct)}`}>{fmtPct(idx.change_pct)}</div>
            </div>
          ))}
          {indices.length === 0 && !indicesError && <div className="empty" style={{ gridColumn: '1/-1' }}>指数数据加载中...</div>}
        </div>
      </section>

      {/* 板块走势 */}
      <section className="card">
        <div className="between wrap">
          <h2 className="card-title" style={{ margin: 0 }}>板块走势</h2>
          <div className="flex wrap" style={{ gap: 8 }}>
            {CATEGORIES.map((c) => (
              <button key={c} className={`pill${category === c ? ' active' : ''}`} onClick={() => setCategory(c)}>{c}</button>
            ))}
          </div>
        </div>
        <div className="flex mt16" style={{ gap: 8 }}>
          {PERIODS.map((p) => (
            <button key={p} className={`pill${period === p ? ' active' : ''}`} onClick={() => setPeriod(p)}>{p}</button>
          ))}
        </div>
        {sectorError && <div className="status-banner datahub-error mt16" role="alert">{sectorError}</div>}
        {sectorMeta?.freshness === 'stale' && <div className="status-banner stale mt16" role="status">
          最近有效板块数据：{sectorMeta.provider || '备用数据源'}{sectorMeta.data_at ? ` · 数据时间 ${new Date(sectorMeta.data_at).toLocaleString('zh-CN')}` : ''}
        </div>}
        {sectors.length > 0 ? (
          <div className="bars mt16">
            {sectors.map((s) => (
              <div className="bar-col" key={s.name}>
                <span className={`bar-val ${cls(s.change_pct)}`}>{fmtPct(s.change_pct)}</span>
                <div className={`bar-fill ${cls(s.change_pct)}`} style={{ height: `${Math.max(6, Math.round((Math.abs(s.change_pct) / maxAbs) * 75))}%` }}></div>
                <span className="bar-label">{s.name}</span>
              </div>
            ))}
          </div>
        ) : (
          <div className="empty mt16">{loading ? '加载中...' : sectorError ? '暂无可用板块数据，请稍后重试' : '暂无板块数据'}</div>
        )}
        <p className="caption mt16">{sectorData ? `${sectorData.category}板块走势 · ${sectorData.period_label || `近${sectorData.period}`}涨跌幅 %` : '板块走势'}</p>
      </section>

      {/* 代表个股 */}
      <section className="card">
        <h2 className="card-title">代表个股</h2>
        <div className="kpi-grid home-stock-grid">
          {(sectorData?.stocks || []).map((s) => (
            <div className="kpi" key={s.code}>
              <div className="k-label">{s.name} <span className="muted mono">{s.code}</span></div>
              <div className="k-value mono" style={{ fontSize: 24 }}>{s.price.toLocaleString('zh-CN', { minimumFractionDigits: 2 })}</div>
              <div className={`k-sub mono ${cls(s.change_pct)}`}>{fmtPct(s.change_pct)}</div>
            </div>
          ))}
          {(!sectorData?.stocks || sectorData.stocks.length === 0) && (
            <div className="empty" style={{ gridColumn: '1/-1' }}>暂无数据</div>
          )}
        </div>
      </section>

      {/* 指数热力图 */}
      <section className="card">
        <h2 className="card-title">大盘指数热力图</h2>
        {indices.length > 0 ? (
          <div className="heatmap home-heatmap">
            {indices.map((idx) => (
              <div className="heat-cell" key={idx.code} style={{ background: heatColor(idx.change_pct) }}>
                {idx.name}<br />{fmtPct(idx.change_pct)}
              </div>
            ))}
          </div>
        ) : (
          <div className="empty">{indicesError ? '指数行情暂不可用，请重试' : '指数数据加载中...'}</div>
        )}
        <p className="caption mt8">指数涨跌 · 红涨绿跌，色深代表幅度</p>
      </section>
    </>
  )
}
