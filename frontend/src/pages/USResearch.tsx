import { useState, useEffect, useCallback, useRef } from 'react'
import client from '../api/client'

interface Cards {
  us_sentiment: string
  a_share_impact: string
  risk_level: string
  focus_directions: string[]
}

interface IndexQuote { name: string; ticker: string; close: number; change_pct: number }
interface CoreStock { ticker: string; name: string; close: number; change_pct: number; a_share_mapping: string }
interface Mover { ticker: string; name: string; change_pct: number }
interface NewsItem { title: string; source: string; url: string }
interface Section { title: string; content: string }

interface Report {
  id: number
  trade_date: string
  generated_at: string
  created_at: string | null
  cards: Cards
  indices: IndexQuote[]
  core_stocks: CoreStock[]
  movers: { gainers: Mover[]; losers: Mover[] }
  sector_samples: { name: string; ticker: string; change_pct: number }[]
  bond_yields: { y2: number; y10: number; y30: number; y2_chg?: number; y10_chg?: number; y30_chg?: number }
  important_news: NewsItem[]
  sections: Section[]
  data_status: Record<string, string>
}

function Pct({ v }: { v: number }) {
  const cls = v > 0 ? 'up' : v < 0 ? 'down' : ''
  return <span className={`mono ${cls}`}>{v > 0 ? '+' : ''}{v?.toFixed(2)}%</span>
}

// 按文案倾向映射徽标色调: 积极=up(红), 消极=down(绿), 其余=hold
function toneBadge(text: string): string {
  if (/偏多|正面|积极|低/.test(text)) return 'badge up'
  if (/偏空|负面|消极|高/.test(text)) return 'badge down'
  return 'badge hold'
}

export default function USResearch() {
  const [report, setReport] = useState<Report | null>(null)
  const [loading, setLoading] = useState(true)
  const [generating, setGenerating] = useState(false)
  const [msg, setMsg] = useState('')
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const load = useCallback(async () => {
    try {
      const resp = await client.get('/us-research/latest')
      setReport(resp.data.data)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])
  useEffect(() => () => { if (pollRef.current) clearInterval(pollRef.current) }, [])

  const generate = async () => {
    setGenerating(true)
    setMsg('')
    try {
      const resp = await client.post('/us-research/generate')
      const taskId = resp.data.data.task_id
      if (pollRef.current) clearInterval(pollRef.current)
      pollRef.current = setInterval(async () => {
        const t = await client.get(`/tasks/${taskId}`)
        const status = t.data.data.status
        if (status === 'success' || status === 'failed') {
          if (pollRef.current) clearInterval(pollRef.current)
          setGenerating(false)
          if (status === 'failed') setMsg('生成失败，请重试')
          load()
        }
      }, 2000)
    } catch {
      setGenerating(false)
      setMsg('任务提交失败')
    }
  }

  if (loading) return <div className="empty">加载中…</div>

  if (!report) {
    return (
      <div className="empty">
        <p style={{ margin: 0 }}>还没有美股隔夜研报</p>
        <div className="mt16">
          <button className="btn btn-primary" onClick={generate} disabled={generating}>
            {generating ? '生成中…' : '立即生成'}
          </button>
        </div>
        {msg && <p className="small up mt8" style={{ margin: 0 }}>{msg}</p>}
      </div>
    )
  }

  const { cards } = report

  return (
    <>
      <div className="between wrap">
        <div>
          <h1 style={{ font: '600 28px/1.2 var(--font-body)', letterSpacing: '-0.5px', margin: 0 }}>美股隔夜研报 · {report.trade_date}</h1>
          <p className="fg2 small" style={{ margin: '8px 0 0' }}>
            生成于 {report.created_at ? new Date(report.created_at).toLocaleString('zh-CN') : '-'}，总结前一交易日美股、美债与新闻变化，研判对 A 股市场的潜在影响。
          </p>
        </div>
        <button className="btn btn-ghost" onClick={generate} disabled={generating}>
          {generating ? '生成中…' : '重新生成'}
        </button>
      </div>
      {msg && <p className="small up" style={{ margin: 0 }}>{msg}</p>}

      {report.data_status && Object.values(report.data_status).some((s) => s !== 'ok') && (
        <div className="banner">
          部分数据源使用了备用数据：{Object.entries(report.data_status).filter(([, s]) => s !== 'ok').map(([k]) => k).join('、')}
        </div>
      )}

      <div className="grid4">
        <div className="kpi">
          <div className="k-label">美股情绪</div>
          <div className="mt8"><span className={toneBadge(cards.us_sentiment)} style={{ fontSize: 16, padding: '8px 14px' }}>{cards.us_sentiment}</span></div>
        </div>
        <div className="kpi">
          <div className="k-label">A股影响</div>
          <div className="mt8"><span className={toneBadge(cards.a_share_impact)} style={{ fontSize: 16, padding: '8px 14px' }}>{cards.a_share_impact}</span></div>
        </div>
        <div className="kpi">
          <div className="k-label">风险等级</div>
          <div className="mt8"><span className={toneBadge(cards.risk_level)} style={{ fontSize: 16, padding: '8px 14px' }}>{cards.risk_level}</span></div>
        </div>
        <div className="kpi">
          <div className="k-label">关注方向</div>
          <div className="mt8 flex wrap" style={{ gap: 8 }}>
            {cards.focus_directions?.map((d) => <span key={d} className="badge">{d}</span>)}
          </div>
        </div>
      </div>

      {report.sections.filter((s) => s.title === '核心结论').map((s) => (
        <section key={s.title} className="card card-accent">
          <h2 className="card-title-sm">{s.title}</h2>
          <p className="fg2" style={{ margin: 0 }}>{s.content}</p>
        </section>
      ))}

      <section className="card">
        <h2 className="card-title-sm">隔夜美股市场表现</h2>
        <div className="grid3">
          {report.indices.map((idx) => (
            <div className="kpi" key={idx.ticker}>
              <div className="k-label">{idx.name}</div>
              <div className="k-value mono" style={{ fontSize: 28 }}>{idx.close?.toLocaleString()}</div>
              <div className="k-sub"><Pct v={idx.change_pct} /></div>
            </div>
          ))}
        </div>
        {report.sections.filter((s) => s.title === '隔夜美股表现').map((s) => (
          <p key={s.title} className="small fg2 mt16" style={{ marginBottom: 0 }}>{s.content}</p>
        ))}
      </section>

      <section className="card">
        <h2 className="card-title-sm">核心美股个股 · A股映射方向</h2>
        <table className="table">
          <thead>
            <tr>
              <th>个股</th>
              <th className="num">收盘价</th>
              <th className="num">涨跌幅</th>
              <th>A股映射方向</th>
            </tr>
          </thead>
          <tbody>
            {report.core_stocks.map((s) => (
              <tr key={s.ticker}>
                <td>{s.name} <span className="muted mono">{s.ticker}</span></td>
                <td className="num mono">{s.close}</td>
                <td className="num"><Pct v={s.change_pct} /></td>
                <td className="small">{s.a_share_mapping}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <div className="grid2">
        <section className="card" style={{ margin: 0 }}>
          <h2 className="card-title-sm">涨跌幅榜（核心样本）</h2>
          <div className="grid2">
            <div>
              <p className="section-label">领涨</p>
              <div className="rank-list">
                {report.movers?.gainers?.map((m) => (
                  <div className="rank-item" key={m.ticker}>
                    <span>{m.name} <span className="muted mono">{m.ticker}</span></span>
                    <Pct v={m.change_pct} />
                  </div>
                ))}
              </div>
            </div>
            <div>
              <p className="section-label">领跌</p>
              <div className="rank-list">
                {report.movers?.losers?.map((m) => (
                  <div className="rank-item" key={m.ticker}>
                    <span>{m.name} <span className="muted mono">{m.ticker}</span></span>
                    <Pct v={m.change_pct} />
                  </div>
                ))}
              </div>
            </div>
          </div>
        </section>

        <section className="card" style={{ margin: 0 }}>
          <h2 className="card-title-sm">美债收益率</h2>
          <div className="grid3">
            {([['2年期', report.bond_yields?.y2], ['10年期', report.bond_yields?.y10], ['30年期', report.bond_yields?.y30]] as const).map(([label, v]) => (
              <div className="kpi" key={label}>
                <div className="k-label">{label}</div>
                <div className="mono" style={{ font: '500 24px/1 var(--font-body)' }}>{v}%</div>
              </div>
            ))}
          </div>
          <h3 className="card-title-sm mt24">板块样本（ETF）</h3>
          <div className="flex wrap" style={{ gap: 8 }}>
            {report.sector_samples?.map((s) => (
              <span key={s.ticker} className={`badge ${s.change_pct > 0 ? 'up' : s.change_pct < 0 ? 'down' : ''}`}>
                {s.name} {s.change_pct > 0 ? '+' : ''}{s.change_pct?.toFixed(2)}%
              </span>
            ))}
          </div>
        </section>
      </div>

      {report.sections.filter((s) => !['核心结论', '隔夜美股表现'].includes(s.title)).map((s) => (
        <section key={s.title} className="card">
          <h3 className="card-title-sm">{s.title}</h3>
          <p className="small fg2" style={{ margin: 0, lineHeight: 1.7 }}>{s.content}</p>
        </section>
      ))}

      <section className="card">
        <h2 className="card-title-sm">重要新闻（英文原文）</h2>
        {report.important_news.map((n, i) => (
          <div className="rowline" key={i}>
            <a href={n.url} target="_blank" rel="noreferrer" className="small">{n.title}</a>
            <span className="caption" style={{ flex: '0 0 auto' }}>{n.source}</span>
          </div>
        ))}
      </section>

      <p className="disclaimer">本研报由 AI 基于公开数据生成，仅供研究参考，不构成任何投资建议。市场有风险，投资需谨慎。</p>
    </>
  )
}
