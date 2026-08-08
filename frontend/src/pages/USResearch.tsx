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
  const color = v > 0 ? 'text-red-600' : v < 0 ? 'text-green-600' : 'text-gray-500'
  return <span className={color}>{v > 0 ? '+' : ''}{v?.toFixed(2)}%</span>
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

  if (loading) return <div className="text-center py-12 text-gray-400">加载中…</div>

  if (!report) {
    return (
      <div className="text-center py-16 space-y-4">
        <p className="text-gray-500">还没有美股隔夜研报</p>
        <button onClick={generate} disabled={generating}
          className="px-6 py-2 bg-brand-600 text-white rounded-lg hover:bg-brand-700 disabled:opacity-50">
          {generating ? '生成中…' : '立即生成'}
        </button>
        {msg && <p className="text-sm text-red-500">{msg}</p>}
      </div>
    )
  }

  const { cards } = report
  const cardItems = [
    { label: '美股情绪', value: cards.us_sentiment },
    { label: 'A股影响', value: cards.a_share_impact },
    { label: '风险等级', value: cards.risk_level },
    { label: '关注方向', value: cards.focus_directions?.join(' / ') },
  ]

  return (
    <div className="space-y-6">
      {/* 研报头 */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold text-gray-900">美股隔夜研报 · {report.trade_date}</h2>
          <p className="text-xs text-gray-400 mt-1">
            生成于 {report.created_at ? new Date(report.created_at).toLocaleString('zh-CN') : '-'}
            ，总结前一交易日美股、美债与新闻变化，研判对 A 股市场的潜在影响
          </p>
        </div>
        <button onClick={generate} disabled={generating}
          className="px-4 py-1.5 text-sm bg-brand-600 text-white rounded-lg hover:bg-brand-700 disabled:opacity-50">
          {generating ? '生成中…' : '重新生成'}
        </button>
      </div>
      {msg && <p className="text-sm text-red-500">{msg}</p>}

      {/* 数据源状态 */}
      {report.data_status && Object.values(report.data_status).some((s) => s !== 'ok') && (
        <div className="text-xs text-amber-600 bg-amber-50 border border-amber-200 rounded-lg px-3 py-2">
          部分数据源使用了备用数据：
          {Object.entries(report.data_status).filter(([, s]) => s !== 'ok').map(([k]) => k).join('、')}
        </div>
      )}

      {/* 四个判断卡片 */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        {cardItems.map((c) => (
          <div key={c.label} className="bg-white rounded-lg border border-gray-100 p-4">
            <div className="text-xs text-gray-400">{c.label}</div>
            <div className="mt-1 text-sm font-semibold text-gray-900">{c.value}</div>
          </div>
        ))}
      </div>

      {/* 正文章节：核心结论等 */}
      {report.sections.filter((s) => s.title === '核心结论').map((s) => (
        <div key={s.title} className="bg-brand-50 border border-brand-100 rounded-lg p-4">
          <h3 className="text-sm font-semibold text-brand-700">{s.title}</h3>
          <p className="mt-2 text-sm text-gray-700 leading-relaxed">{s.content}</p>
        </div>
      ))}

      {/* 三大指数 */}
      <section className="bg-white rounded-lg border border-gray-100 p-4">
        <h3 className="text-sm font-semibold text-gray-900 mb-3">隔夜美股市场表现</h3>
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
          {report.indices.map((idx) => (
            <div key={idx.ticker} className="border border-gray-100 rounded-lg p-3">
              <div className="text-xs text-gray-400">{idx.name}</div>
              <div className="mt-1 flex items-baseline gap-2">
                <span className="text-base font-semibold text-gray-900">{idx.close?.toLocaleString()}</span>
                <Pct v={idx.change_pct} />
              </div>
            </div>
          ))}
        </div>
        {report.sections.filter((s) => s.title === '隔夜美股表现').map((s) => (
          <p key={s.title} className="mt-3 text-sm text-gray-600">{s.content}</p>
        ))}
      </section>

      {/* 核心美股 + A股映射 */}
      <section className="bg-white rounded-lg border border-gray-100 p-4 overflow-x-auto">
        <h3 className="text-sm font-semibold text-gray-900 mb-3">核心美股个股 · A股映射方向</h3>
        <table className="w-full text-sm min-w-[560px]">
          <thead>
            <tr className="text-left text-xs text-gray-400 border-b border-gray-100">
              <th className="py-2 pr-3">个股</th>
              <th className="py-2 pr-3">收盘价</th>
              <th className="py-2 pr-3">涨跌幅</th>
              <th className="py-2">A股映射方向</th>
            </tr>
          </thead>
          <tbody>
            {report.core_stocks.map((s) => (
              <tr key={s.ticker} className="border-b border-gray-50">
                <td className="py-2 pr-3">
                  <span className="font-medium text-gray-900">{s.name}</span>
                  <span className="ml-1 text-xs text-gray-400">{s.ticker}</span>
                </td>
                <td className="py-2 pr-3 text-gray-700">{s.close}</td>
                <td className="py-2 pr-3"><Pct v={s.change_pct} /></td>
                <td className="py-2 text-gray-600">{s.a_share_mapping}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {/* 涨跌幅榜 */}
        <section className="bg-white rounded-lg border border-gray-100 p-4">
          <h3 className="text-sm font-semibold text-gray-900 mb-3">涨跌幅榜（核心样本）</h3>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <div className="text-xs text-gray-400 mb-2">领涨</div>
              {report.movers?.gainers?.map((m) => (
                <div key={m.ticker} className="flex justify-between py-1 text-sm">
                  <span className="text-gray-700">{m.name}</span>
                  <Pct v={m.change_pct} />
                </div>
              ))}
            </div>
            <div>
              <div className="text-xs text-gray-400 mb-2">领跌</div>
              {report.movers?.losers?.map((m) => (
                <div key={m.ticker} className="flex justify-between py-1 text-sm">
                  <span className="text-gray-700">{m.name}</span>
                  <Pct v={m.change_pct} />
                </div>
              ))}
            </div>
          </div>
        </section>

        {/* 美债收益率 */}
        <section className="bg-white rounded-lg border border-gray-100 p-4">
          <h3 className="text-sm font-semibold text-gray-900 mb-3">美债收益率</h3>
          <div className="grid grid-cols-3 gap-3">
            {([['2年期', report.bond_yields?.y2], ['10年期', report.bond_yields?.y10], ['30年期', report.bond_yields?.y30]] as const).map(([label, v]) => (
              <div key={label} className="border border-gray-100 rounded-lg p-3 text-center">
                <div className="text-xs text-gray-400">{label}</div>
                <div className="mt-1 text-base font-semibold text-gray-900">{v}%</div>
              </div>
            ))}
          </div>
          {/* 板块样本 */}
          <h4 className="text-xs text-gray-400 mt-4 mb-2">板块样本（ETF）</h4>
          <div className="flex flex-wrap gap-2">
            {report.sector_samples?.map((s) => (
              <span key={s.ticker} className="px-2 py-1 text-xs border border-gray-100 rounded">
                {s.name} <Pct v={s.change_pct} />
              </span>
            ))}
          </div>
        </section>
      </div>

      {/* 其余章节 */}
      {report.sections.filter((s) => !['核心结论', '隔夜美股表现'].includes(s.title)).map((s) => (
        <section key={s.title} className="bg-white rounded-lg border border-gray-100 p-4">
          <h3 className="text-sm font-semibold text-gray-900">{s.title}</h3>
          <p className="mt-2 text-sm text-gray-600 leading-relaxed">{s.content}</p>
        </section>
      ))}

      {/* 重要新闻 */}
      <section className="bg-white rounded-lg border border-gray-100 p-4">
        <h3 className="text-sm font-semibold text-gray-900 mb-3">重要新闻（英文原文）</h3>
        <ul className="space-y-2">
          {report.important_news.map((n, i) => (
            <li key={i} className="text-sm">
              <a href={n.url} target="_blank" rel="noreferrer" className="text-gray-700 hover:text-brand-600">
                {n.title}
              </a>
              <span className="ml-2 text-xs text-gray-400">{n.source}</span>
            </li>
          ))}
        </ul>
      </section>
    </div>
  )
}
