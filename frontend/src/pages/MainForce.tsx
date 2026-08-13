import { useState, useCallback, useEffect } from "react"
import client from '../api/client'
import { errMsg } from '../utils/errors'
import { isTaskFailure } from '../utils/taskStatus'

type Tab = 'analysis' | 'history'

const ANALYST_LABELS: Record<string, string> = {
  capital: '资金流向分析师',
  industry: '行业板块分析师',
  fundamental: '财务基本面分析师',
  technical: '技术形态分析师',
  quant: '量化分析师',
}

interface TaskStatus {
  status: string
  progress: number
  error: string | null
  result: { run_id?: number } | null
}

interface RunData {
  id: number
  run_date: string
  candidates_count: number
  filtered_count: number
  recommended: any
  excluded: any[]
  analysis: any
  token_total: number
}

export default function MainForce() {
  const [tab, setTab] = useState<Tab>('analysis')
  return (
    <>
      <div className="tabs">
        {(['analysis', 'history'] as Tab[]).map((t) => (
          <button key={t} onClick={() => setTab(t)} className={`tab${tab === t ? ' active' : ''}`}>
            {t === 'analysis' ? '选股分析' : '历史记录'}
          </button>
        ))}
      </div>
      {tab === 'analysis' && <AnalysisView />}
      {tab === 'history' && <HistoryView />}
    </>
  )
}

function Funnel({ candidates, filtered, recommended }: { candidates: number; filtered: number; recommended: number }) {
  return (
    <div className="funnel">
      <div className="funnel-step"><div className="n mono">{candidates}</div><div className="t">候选股票</div></div>
      <span className="funnel-arrow">→</span>
      <div className="funnel-step"><div className="n mono">{filtered}</div><div className="t">筛选通过</div></div>
      <span className="funnel-arrow">→</span>
      <div className="funnel-step"><div className="n mono">{recommended}</div><div className="t">最终推荐</div></div>
    </div>
  )
}

function RecommendTable({ rec }: { rec: any }) {
  return (
    <>
      {rec.meeting_summary && <p className="small fg2" style={{ background: 'var(--surface)', padding: 12, borderRadius: 'var(--r-card)' }}>{rec.meeting_summary}</p>}
      {rec.companies && (
        <div style={{ overflowX: 'auto' }}>
          <table className="table">
            <thead>
              <tr><th>股票</th><th>买入区间</th><th>卖出区间</th><th className="num">置信度</th><th>仓位</th><th>推荐逻辑</th></tr>
            </thead>
            <tbody>
              {rec.companies.map((c: any, i: number) => (
                <tr key={i}>
                  <td>{c.name} <span className="muted mono">{c.code}</span></td>
                  <td className="mono">{c.buy_range}</td>
                  <td className="mono">{c.sell_range}</td>
                  <td className="num mono">{c.confidence}%</td>
                  <td>{c.position}</td>
                  <td className="small">{c.logic}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {rec.excluded && rec.excluded.length > 0 && (
        <div className="mt16">
          <span className="section-label">被排除标的:</span>
          <ul className="excluded-list">
            {rec.excluded.map((e: any, i: number) => (
              <li key={i}>{e.name}({e.code}) - {e.reason}</li>
            ))}
          </ul>
        </div>
      )}
    </>
  )
}

function AnalysisView() {
  const [loading, setLoading] = useState(false)
  const [progress, setProgress] = useState(0)
  const [status, setStatus] = useState('')
  const [runData, setRunData] = useState<RunData | null>(null)
  const [error, setError] = useState('')

  const pollTask = useCallback(async (taskId: number) => {
    for (let i = 0; i < 100; i++) {
      await new Promise((r) => setTimeout(r, 2000))
      try {
        const resp = await client.get(`/tasks/${taskId}`)
        const d: TaskStatus = resp.data.data
        setProgress(d.progress)
        setStatus(d.status)
        if (d.status === 'success' && d.result?.run_id) {
          const r2 = await client.get(`/stocks/main-force/${d.result.run_id}`)
          setRunData(r2.data.data)
          setLoading(false)
          return
        }
        if (isTaskFailure(d.status)) {
          setError(d.error || '选股失败')
          setLoading(false)
          return
        }
      } catch {}
    }
    setError('轮询超时')
    setLoading(false)
  }, [])

  const submit = async () => {
    setError(''); setRunData(null); setLoading(true); setProgress(0)
    try {
      const resp = await client.post('/stocks/main-force/run')
      pollTask(resp.data.data.task_id)
    } catch (err: any) {
      setError(errMsg(err, '提交失败'))
      setLoading(false)
    }
  }

  const analysis = runData?.analysis
  const rec = runData?.recommended || {}

  return (
    <div className="panel-stack">
      <section className="card">
        <div className="between wrap">
          <p className="fg2" style={{ margin: 0 }}>5 位 AI 分析师 + 资深研究员根据主力资金流向精选标的</p>
          <button className="btn btn-primary" onClick={submit} disabled={loading}>
            {loading ? '选股中...' : '开始选股'}
          </button>
        </div>
      </section>

      {error && <p className="small" style={{ color: 'var(--up)' }}>{error}</p>}

      {loading && (
        <section className="card">
          <div className="between wrap">
            <span className="fg2 small">选股进度: <span className="mono">{progress}%</span>（{status}）</span>
          </div>
          <div className="progress mt8"><i style={{ width: `${progress}%` }}></i></div>
        </section>
      )}

      {runData && analysis && (
        <>
          <section className="card">
            <h2 className="card-title">选股漏斗</h2>
            <Funnel candidates={runData.candidates_count} filtered={runData.filtered_count} recommended={rec.companies?.length || 0} />
            <p className="caption mt16">策略: 流通市值{'>'}{analysis.strategy?.min_market_cap}亿 | 20日涨幅{'<'}{analysis.strategy?.max_20d_change_pct}% | 60日净流入{'>'}0 | 股东户数下降</p>
          </section>

          <section className="card">
            <h2 className="card-title">AI 分析师评分</h2>
            <div className="mini-grid">
              {Object.entries(analysis.analysts || {}).map(([key, data]: [string, any]) => (
                <div className="mini-card" key={key}>
                  <div className="between">
                    <h3>{ANALYST_LABELS[key] || key}</h3>
                    {data?.score != null && <span className="fg2 small">评分: <span className="mono">{data.score}</span></span>}
                  </div>
                  <p className="small fg2 mt8">{data?.analysis || data?.error || ''}</p>
                  <details className="mt8">
                    <summary>展开完整报告</summary>
                    <p className="full-report">{JSON.stringify(data, null, 2)}</p>
                  </details>
                </div>
              ))}
            </div>
          </section>

          <section className="card card-accent">
            <h2 className="card-title">资深研究员 · 精选推荐</h2>
            <RecommendTable rec={rec} />
            <p className="caption mt16">推荐基于 {runData.run_date} 收盘数据生成，仅供研究参考，不构成投资建议</p>
          </section>
        </>
      )}
    </div>
  )
}

function HistoryView() {
  const [items, setItems] = useState<any[]>([])
  const [loading, setLoading] = useState(true)
  const [detail, setDetail] = useState<RunData | null>(null)

  useEffect(() => { loadHistory() }, [])
  const loadHistory = async () => {
    try {
      const resp = await client.get('/stocks/main-force/history')
      setItems(resp.data.data.items)
    } catch {} finally { setLoading(false) }
  }

  const viewDetail = async (runId: number) => {
    try {
      const resp = await client.get(`/stocks/main-force/${runId}`)
      setDetail(resp.data.data)
    } catch {}
  }

  if (detail) {
    const rec = detail.recommended || {}
    return (
      <div className="panel-stack">
        <button className="btn-text" style={{ alignSelf: 'start' }} onClick={() => setDetail(null)}>← 返回列表</button>
        <section className="card">
          <h2 className="card-title">选股漏斗 · {detail.run_date}</h2>
          <Funnel candidates={detail.candidates_count} filtered={detail.filtered_count} recommended={rec.companies?.length || 0} />
        </section>
        {rec.companies && (
          <section className="card card-accent">
            <h2 className="card-title">精选推荐</h2>
            <RecommendTable rec={rec} />
          </section>
        )}
      </div>
    )
  }

  return (
    <section className="card" style={{ padding: 0 }}>
      {loading ? (
        <div className="empty" style={{ margin: 24 }}>加载中...</div>
      ) : items.length === 0 ? (
        <div className="empty" style={{ margin: 24 }}>暂无选股记录</div>
      ) : (
        <div style={{ overflowX: 'auto' }}>
          <table className="table">
            <thead>
              <tr><th>选股日期</th><th className="num">候选数</th><th className="num">筛选数</th><th>操作</th></tr>
            </thead>
            <tbody>
              {items.map((item) => (
                <tr key={item.id}>
                  <td>{item.run_date}</td>
                  <td className="num mono">{item.candidates_count}</td>
                  <td className="num mono">{item.filtered_count}</td>
                  <td><button className="btn-text" onClick={() => viewDetail(item.id)}>查看详情</button></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}
