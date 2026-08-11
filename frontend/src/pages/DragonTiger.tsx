import { useState, useCallback, useEffect } from 'react'
import client from '../api/client'
import { errMsg } from '../utils/errors'

type Tab = 'analysis' | 'history' | 'stats'

const PERIOD_OPTIONS = [3, 5, 10, 15, 20, 30]

interface TaskStatus {
  status: string
  progress: number
  error: string | null
  result: { report_id?: number } | null
}

interface ReportData {
  period_days: number
  stats: any
  data_summary: any
  top_stocks: { code: string; name: string; score: number; grade: string; buy_amount: number; sell_amount: number; appearances: number; dates: string[]; reasons: string[] }[]
  institutions: { name: string; appearances: number; success_rate: number }[]
  analysis: { summary: string; confidence_score: number; strategy_advice: string; risk_level: string }
  analyzed_at: string
}

const GRADE_BADGE: Record<string, string> = {
  A: 'badge up',
  B: 'badge accent',
  C: 'badge hold',
  D: 'badge',
}

function SignedNum({ v, suffix = '' }: { v: number; suffix?: string }) {
  const cls = v > 0 ? 'up' : v < 0 ? 'down' : ''
  const sign = v > 0 ? '+' : ''
  return <span className={`mono ${cls}`}>{sign}{v.toFixed(2)}{suffix}</span>
}

export default function DragonTiger() {
  const [tab, setTab] = useState<Tab>('analysis')
  const [report, setReport] = useState<ReportData | null>(null)

  const viewReport = async (id: number) => {
    try {
      const resp = await client.get(`/stocks/dragon-tiger/reports/${id}`)
      setReport(resp.data.data)
      setTab('analysis')
    } catch {}
  }

  return (
    <>
      <div className="tabs">
        <button className={`tab${tab === 'analysis' ? ' active' : ''}`} onClick={() => setTab('analysis')}>龙虎榜分析</button>
        <button className={`tab${tab === 'history' ? ' active' : ''}`} onClick={() => setTab('history')}>历史报告</button>
        <button className={`tab${tab === 'stats' ? ' active' : ''}`} onClick={() => setTab('stats')}>数据统计</button>
      </div>
      {tab === 'analysis' && <AnalysisView report={report} setReport={setReport} />}
      {tab === 'history' && <HistoryView onView={viewReport} />}
      {tab === 'stats' && <StatsView />}
    </>
  )
}

function AnalysisView({ report, setReport }: { report: ReportData | null; setReport: (r: ReportData | null) => void }) {
  const [periodDays, setPeriodDays] = useState(5)
  const [loading, setLoading] = useState(false)
  const [progress, setProgress] = useState(0)
  const [status, setStatus] = useState('')
  const [error, setError] = useState('')

  const pollTask = useCallback(async (taskId: number) => {
    for (let i = 0; i < 100; i++) {
      await new Promise((r) => setTimeout(r, 2000))
      try {
        const resp = await client.get(`/tasks/${taskId}`)
        const d: TaskStatus = resp.data.data
        setProgress(d.progress); setStatus(d.status)
        if (d.status === 'success' && d.result?.report_id) {
          const r2 = await client.get(`/stocks/dragon-tiger/reports/${d.result.report_id}`)
          setReport(r2.data.data)
          setLoading(false); return
        }
        if (d.status === 'failed') { setError(d.error || '分析失败'); setLoading(false); return }
      } catch {}
    }
    setError('轮询超时'); setLoading(false)
  }, [setReport])

  const submit = async () => {
    setError(''); setReport(null); setLoading(true); setProgress(0)
    try {
      const resp = await client.post('/stocks/dragon-tiger/analyze', { period_days: periodDays })
      pollTask(resp.data.data.task_id)
    } catch (err: any) {
      setError(errMsg(err, '提交失败'))
      setLoading(false)
    }
  }

  return (
    <div className="panel-stack">
      <section className="card">
        <div className="between wrap">
          <div className="flex wrap">
            <span className="fg2 small">分析时间范围</span>
            <div className="flex" style={{ gap: 8 }}>
              {PERIOD_OPTIONS.map((d) => (
                <button key={d} className={`pill${periodDays === d ? ' active' : ''}`} onClick={() => setPeriodDays(d)}>{d}天</button>
              ))}
            </div>
          </div>
          <button className="btn btn-primary" onClick={submit} disabled={loading}>
            {loading ? '分析中...' : '开始分析'}
          </button>
        </div>
        {error && <p className="small up mt8" style={{ margin: 0 }}>{error}</p>}
        {loading && (
          <div className="mt16">
            <p className="small fg2">进度: <span className="mono">{progress}%</span>（{status}）</p>
            <div className="progress mt8"><i style={{ width: `${progress}%` }} /></div>
          </div>
        )}
      </section>

      {report && (
        <>
          <section className="card card-accent">
            <h2 className="card-title">AI 分析摘要</h2>
            <div className="grid4">
              <div className="kpi"><div className="k-label">信心评分</div><div className="k-value mono">{report.analysis?.confidence_score ?? 'N/A'}</div></div>
              <div className="kpi"><div className="k-label">风险等级</div><div className="k-value up" style={{ fontSize: 28 }}>{report.analysis?.risk_level ?? 'N/A'}</div></div>
              <div className="kpi"><div className="k-label">总记录数</div><div className="k-value mono">{report.stats?.total_records ?? '-'}</div></div>
              <div className="kpi"><div className="k-label">上榜股票数</div><div className="k-value mono">{report.stats?.unique_stocks ?? '-'}</div></div>
            </div>
            <p className="small fg2 mt16">{report.analysis?.summary}</p>
            <p className="caption mt8">分析范围: 近 {report.period_days} 天 · {report.analyzed_at?.slice(0, 19)}</p>
          </section>

          {report.top_stocks && report.top_stocks.length > 0 && (
            <section className="card">
              <h2 className="card-title">龙虎榜推荐 TOP{report.top_stocks.length}</h2>
              <table className="table">
                <thead>
                  <tr>
                    <th>排名</th><th>股票</th><th>综合评分</th>
                    <th className="num">净流入(亿)</th><th className="num">买入(亿)</th><th className="num">卖出(亿)</th>
                    <th>上榜次数</th><th>上榜类型</th>
                  </tr>
                </thead>
                <tbody>
                  {report.top_stocks.map((s, i) => (
                    <tr key={s.code}>
                      <td className="mono">{i + 1}</td>
                      <td>{s.name} <span className="muted mono">{s.code}</span></td>
                      <td><span className={GRADE_BADGE[s.grade] || 'badge'}>{s.score}分 {s.grade}</span></td>
                      <td className="num"><SignedNum v={s.buy_amount - s.sell_amount} /></td>
                      <td className="num mono up">{s.buy_amount.toFixed(2)}</td>
                      <td className="num mono down">{s.sell_amount.toFixed(2)}</td>
                      <td className="mono">{s.appearances}</td>
                      <td className="small">{s.reasons[0] || '-'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </section>
          )}

          {report.institutions && report.institutions.length > 0 && (
            <section className="card">
              <h2 className="card-title">活跃游资画像</h2>
              <table className="table">
                <thead><tr><th>营业部</th><th>上榜次数</th><th>成功率</th></tr></thead>
                <tbody>
                  {report.institutions.map((inst, i) => (
                    <tr key={i}>
                      <td className="small">{inst.name}</td>
                      <td className="mono">{inst.appearances}</td>
                      <td className={`mono ${inst.success_rate >= 55 ? 'up' : 'down'}`}>{inst.success_rate}%</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <p className="caption mt16">成功率指上榜后 5 个交易日内股价上涨的比例</p>
            </section>
          )}

          {report.analysis?.strategy_advice && (
            <section className="card card-accent">
              <h2 className="card-title">AI 策略建议</h2>
              <p className="fg2" style={{ margin: 0, whiteSpace: 'pre-line' }}>{report.analysis.strategy_advice}</p>
            </section>
          )}
        </>
      )}
    </div>
  )
}

function HistoryView({ onView }: { onView: (id: number) => void }) {
  const [items, setItems] = useState<any[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    ;(async () => {
      try {
        const resp = await client.get('/stocks/dragon-tiger/reports')
        setItems(resp.data.data.items)
      } catch {} finally { setLoading(false) }
    })()
  }, [])

  return (
    <section className="card">
      <h2 className="card-title-sm">历史报告</h2>
      {loading ? <div className="empty">加载中...</div> :
       items.length === 0 ? <div className="empty">暂无龙虎榜报告</div> : (
        <table className="table">
          <thead><tr><th>分析天数</th><th>分析时间</th><th>操作</th></tr></thead>
          <tbody>
            {items.map((item) => (
              <tr key={item.id}>
                <td className="mono">{item.period_days}天</td>
                <td className="mono">{item.created_at?.slice(0, 19)}</td>
                <td><a className="btn-text" onClick={() => onView(item.id)}>查看详情</a></td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <p className="caption mt16">报告保留最近 90 天</p>
    </section>
  )
}

function StatsView() {
  const [stats, setStats] = useState<any>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    ;(async () => {
      try {
        const resp = await client.get('/stocks/dragon-tiger/stats')
        setStats(resp.data.data)
      } catch {} finally { setLoading(false) }
    })()
  }, [])

  return (
    <section className="card">
      <h2 className="card-title-sm">数据统计</h2>
      {loading ? <div className="empty">加载中...</div> : (
        <div className="grid3">
          <div className="kpi"><div className="k-label">累计报告数</div><div className="k-value mono">{stats?.total_reports ?? 0}</div></div>
          <div className="kpi"><div className="k-label">最近分析天数</div><div className="k-value mono">{stats?.latest_period ? `${stats.latest_period}天` : 'N/A'}</div></div>
          <div className="kpi"><div className="k-label">最近分析时间</div><div className="k-value mono" style={{ fontSize: 24 }}>{stats?.latest_created?.slice(0, 19) ?? 'N/A'}</div></div>
        </div>
      )}
    </section>
  )
}
