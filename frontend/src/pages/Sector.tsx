import { useState, useCallback, useEffect } from 'react'
import client from '../api/client'
import { errMsg } from '../utils/errors'
import { isTaskFailure } from '../utils/taskStatus'

type Tab = 'analysis' | 'history'

const AGENT_LABELS: Record<string, string> = {
  macro: '宏观策略师',
  diagnosis: '板块诊断师',
  capital: '资金流向分析师',
  sentiment: '市场情绪解码员',
}

interface TaskStatus {
  status: string
  progress: number
  error: string | null
  result: { report_id?: number } | null
}

interface ReportData {
  agents: Record<string, any>
  bull_sectors: { name: string; confidence: number; logic: string; risk?: string }[]
  bear_sectors: { name: string; confidence: number; logic: string; risk?: string }[]
  neutral_sectors: { name: string; confidence: number; logic: string; risk?: string }[]
  operation_advice: string
  risk_triggers?: string
  key_indicators?: string[]
  report_date: string
}

export default function Sector() {
  const [tab, setTab] = useState<Tab>('analysis')
  return (
    <>
      <div className="tabs">
        <button className={`tab${tab === 'analysis' ? ' active' : ''}`} onClick={() => setTab('analysis')}>板块分析</button>
        <button className={`tab${tab === 'history' ? ' active' : ''}`} onClick={() => setTab('history')}>分析历史</button>
      </div>
      {tab === 'analysis' ? <AnalysisView /> : <HistoryView />}
    </>
  )
}

function AnalysisView() {
  const [loading, setLoading] = useState(false)
  const [progress, setProgress] = useState(0)
  const [status, setStatus] = useState('')
  const [report, setReport] = useState<ReportData | null>(null)
  const [error, setError] = useState('')

  const pollTask = useCallback(async (taskId: number) => {
    for (let i = 0; i < 100; i++) {
      await new Promise((r) => setTimeout(r, 2000))
      try {
        const resp = await client.get(`/tasks/${taskId}`)
        const d: TaskStatus = resp.data.data
        setProgress(d.progress); setStatus(d.status)
        if (d.status === 'success' && d.result?.report_id) {
          const r2 = await client.get(`/stocks/sectors/reports/latest`)
          setReport(r2.data.data)
          setLoading(false); return
        }
        if (isTaskFailure(d.status)) { setError(d.error || '分析失败'); setLoading(false); return }
      } catch {}
    }
    setError('轮询超时'); setLoading(false)
  }, [])

  const submit = async () => {
    setError(''); setReport(null); setLoading(true); setProgress(0)
    try {
      const resp = await client.post('/stocks/sectors/analyze')
      pollTask(resp.data.data.task_id)
    } catch (err: any) {
      setError(errMsg(err, '提交失败'))
      setLoading(false)
    }
  }

  useEffect(() => {
    ;(async () => {
      try {
        const resp = await client.get('/stocks/sectors/reports/latest')
        if (resp.data.data) setReport(resp.data.data)
      } catch {}
    })()
  }, [])

  return (
    <div className="panel-stack">
      <section className="card">
        <div className="between wrap">
          <p className="fg2" style={{ margin: 0 }}>4 位 AI 智能体协同分析行业板块多空趋势</p>
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
          <section className="card">
            <h2 className="card-title">AI 智能体</h2>
            <div className="mini-grid">
              {Object.entries(report.agents || {}).map(([key, data]: [string, any]) => (
                <div className="mini-card" key={key}>
                  <div className="between">
                    <h3>{AGENT_LABELS[key] || key}</h3>
                    {data?.score != null && <span className="fg2 small">评分: <span className="mono">{data.score}</span></span>}
                  </div>
                  <div className="small fg2 mt8">
                    {data?.report && <p style={{ margin: 0 }}>{data.report}</p>}
                    {data?.assessment && <p style={{ margin: 0 }}>{data.assessment}</p>}
                    {data?.sectors && data.sectors.map((s: any, i: number) => (
                      <p key={i} style={{ margin: 0 }}>{s.name}: {s.health} / {s.trend}</p>
                    ))}
                    {data?.inflow_sectors && <p style={{ margin: 0 }}>资金流入: {data.inflow_sectors.join(', ')}</p>}
                    {data?.outflow_sectors && <p style={{ margin: 0 }}>资金流出: {data.outflow_sectors.join(', ')}</p>}
                    {data?.error && <p className="up" style={{ margin: 0 }}>分析失败: {data.error}</p>}
                  </div>
                  <details className="mt8">
                    <summary className="small" style={{ cursor: 'pointer', color: 'var(--accent)' }}>展开完整报告</summary>
                    <pre className="caption mt8" style={{ overflowX: 'auto', background: 'var(--surface)', padding: 12, borderRadius: 4 }}>{JSON.stringify(data, null, 2)}</pre>
                  </details>
                </div>
              ))}
            </div>
          </section>

          <section className="card card-accent">
            <h2 className="card-title">板块多空预测 · {report.report_date}</h2>
            <div className="grid3">
              <SectorCol sectors={report.bull_sectors} label="看多" badgeCls="badge up" />
              <SectorCol sectors={report.neutral_sectors} label="中性" badgeCls="badge" />
              <SectorCol sectors={report.bear_sectors} label="看空" badgeCls="badge down" />
            </div>

            <div className="grid2 mt24">
              <div className="kpi">
                <div className="k-label">操作节奏建议</div>
                <p className="small fg2" style={{ margin: 0 }}>{report.operation_advice || '暂无'}</p>
              </div>
              <div className="kpi">
                <div className="k-label up">风险触发条件</div>
                <p className="small fg2" style={{ margin: 0 }}>{report.risk_triggers || '暂无'}</p>
              </div>
            </div>

            {report.key_indicators && report.key_indicators.length > 0 && (
              <div className="mt16">
                <span className="section-label">核心跟踪指标:</span>
                <ul className="track-list">
                  {report.key_indicators.map((k: string, i: number) => <li key={i}>{k}</li>)}
                </ul>
              </div>
            )}
            <p className="caption mt16">本预测由 AI 生成，仅供研究参考，不构成投资建议</p>
          </section>
        </>
      )}
    </div>
  )
}

function SectorCol({ sectors, label, badgeCls }: { sectors?: any[]; label: string; badgeCls: string }) {
  return (
    <div className="sector-col">
      <span className={badgeCls}>{label}</span>
      {sectors && sectors.length > 0 ? sectors.map((s, i) => (
        <div className="sector-item" key={i}>
          <div className="between"><strong>{s.name}</strong><span className="mono small fg2">置信度 {s.confidence}/10</span></div>
          <p className="caption mt8">{s.logic}</p>
        </div>
      )) : <p className="small muted">暂无</p>}
    </div>
  )
}

function HistoryView() {
  const [items, setItems] = useState<any[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    ;(async () => {
      try {
        const resp = await client.get('/stocks/sectors/reports/history')
        setItems(resp.data.data.items)
      } catch {} finally { setLoading(false) }
    })()
  }, [])

  return (
    <section className="card">
      <h2 className="card-title-sm">分析历史</h2>
      {loading ? <div className="empty">加载中...</div> :
       items.length === 0 ? <div className="empty">暂无板块分析历史</div> : (
        <table className="table">
          <thead><tr><th>报告日期</th></tr></thead>
          <tbody>
            {items.map((item) => (
              <tr key={item.id}><td className="mono">{item.report_date}</td></tr>
            ))}
          </tbody>
        </table>
      )}
      <p className="caption mt16">每周一收盘后生成一次板块多空预测报告</p>
    </section>
  )
}
