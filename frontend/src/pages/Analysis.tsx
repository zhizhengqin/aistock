import { useState, useEffect, useCallback } from 'react'
import client from '../api/client'
import { errMsg } from '../utils/errors'
import { isTaskFailure } from '../utils/taskStatus'

type Tab = 'single' | 'batch' | 'history'

const ANALYST_LABELS: Record<string, string> = {
  technical: '技术面分析师',
  fundamental: '基本面分析师',
  capital: '资金面分析师',
  news: '新闻舆情分析师',
  sentiment: '市场情绪分析师',
}

interface TaskInfo {
  task_id: number
  stock_code: string
}

interface TaskStatus {
  id: number
  status: string
  progress: number
  error: string | null
  result: { report_id?: number; stock_code?: string } | null
}

interface ReportData {
  _id?: number
  stock_code: string
  stock_name: string
  stock_info: { price: number; change_pct: number; pe_ttm: number; pb: number; market_cap: number; industry: string }
  indicators: { ma: Record<string, number|null>; macd: Record<string, number|null>; rsi: Record<string, number|null>; kdj: Record<string, number|null>; boll: Record<string, number|null> }
  analysts: Record<string, any>
  decision: { rating: string; target_price: number; stop_loss: number; confidence: number; entry_range: string; take_profit: string; holding_period: string; position_size: string; risk_warning: string; key_watchpoints: string[]; meeting_summary: string }
  disclaimer: string
  analyzed_at: string
}

const RATING_BADGE: Record<string, string> = {
  '买入': 'badge up',
  '持有': 'badge hold',
  '卖出': 'badge down',
}

export default function Analysis() {
  const [tab, setTab] = useState<Tab>('single')
  return (
    <>
      <div className="tabs">
        {(['single', 'batch', 'history'] as Tab[]).map((t) => (
          <button key={t} onClick={() => setTab(t)} className={`tab${tab === t ? ' active' : ''}`}>
            {t === 'single' ? '单股分析' : t === 'batch' ? '批量分析' : '历史记录'}
          </button>
        ))}
      </div>
      {tab === 'single' && <SingleAnalysis />}
      {tab === 'batch' && <BatchAnalysis />}
      {tab === 'history' && <HistoryView />}
    </>
  )
}

function SingleAnalysis() {
  const [code, setCode] = useState('')
  const [loading, setLoading] = useState(false)
  const [, setTaskId] = useState<number | null>(null)
  const [progress, setProgress] = useState(0)
  const [status, setStatus] = useState('')
  const [report, setReport] = useState<ReportData | null>(null)
  const [error, setError] = useState('')

  const pollTask = useCallback(async (id: number) => {
    for (let i = 0; i < 100; i++) {
      await new Promise((r) => setTimeout(r, 2000))
      try {
        const resp = await client.get(`/tasks/${id}`)
        const data: TaskStatus = resp.data.data
        setProgress(data.progress)
        setStatus(data.status)
        if (data.status === 'success' && data.result?.report_id) {
          const r2 = await client.get(`/stocks/user/results/${data.result.report_id}`)
          setReport({ ...r2.data.data.report, _id: r2.data.data.id })
          setLoading(false)
          return
        }
        if (isTaskFailure(data.status)) {
          setError(data.error || '分析失败')
          setLoading(false)
          return
        }
      } catch { /* retry */ }
    }
    setError('轮询超时')
    setLoading(false)
  }, [])

  const submit = async () => {
    if (!code.trim()) { setError('请输入股票代码'); return }
    setError('')
    setReport(null)
    setLoading(true)
    setProgress(0)
    try {
      const resp = await client.post('/stocks/analyze', { stock_codes: [code.trim()] })
      const task: TaskInfo = resp.data.data.tasks[0]
      setTaskId(task.task_id)
      pollTask(task.task_id)
    } catch (err: any) {
      setError(errMsg(err, '提交失败'))
      setLoading(false)
    }
  }

  return (
    <div className="panel-stack">
      <section className="card">
        <div className="flex wrap">
          <input className="input" style={{ flex: 1, minWidth: 240 }} value={code}
            onChange={(e) => setCode(e.target.value)} placeholder="输入股票代码，如 600519" />
          <button className="btn btn-primary" onClick={submit} disabled={loading}>
            {loading ? '分析中...' : '开始分析'}
          </button>
        </div>
        <p className="caption mt8">支持沪深 A 股 6 位代码 · 5 位 AI 分析师协同生成完整投研报告</p>
      </section>

      {error && <p className="small" style={{ color: 'var(--up)' }}>{error}</p>}

      {loading && (
        <section className="card">
          <div className="between wrap">
            <span className="fg2 small">分析进度: <span className="mono">{progress}%</span>（{status}）</span>
          </div>
          <div className="progress mt8"><i style={{ width: `${progress}%` }}></i></div>
        </section>
      )}

      {report && <ReportView report={report} />}
    </div>
  )
}

function BatchAnalysis() {
  const [codesText, setCodesText] = useState('')
  const [loading, setLoading] = useState(false)
  const [results, setResults] = useState<{ code: string; status: string; report_id?: number }[]>([])
  const [error, setError] = useState('')

  const submit = async () => {
    const codes = codesText.split(/[,\n\s]+/).filter(Boolean)
    if (codes.length === 0) { setError('请输入至少一个股票代码'); return }
    if (codes.length > 50) { setError('最多50只股票'); return }
    setError('')
    setLoading(true)
    setResults(codes.map((c) => ({ code: c, status: 'pending' })))
    try {
      const resp = await client.post('/stocks/analyze', { stock_codes: codes })
      const tasks: TaskInfo[] = resp.data.data.tasks
      const pollAll = async () => {
        const updated = [...results]
        for (let i = 0; i < tasks.length; i++) {
          for (let attempt = 0; attempt < 80; attempt++) {
            await new Promise((r) => setTimeout(r, 2000))
            try {
              const sr = await client.get(`/tasks/${tasks[i].task_id}`)
              const d: TaskStatus = sr.data.data
              if (d.status === 'success' && d.result?.report_id) {
                updated[i] = { code: tasks[i].stock_code, status: 'success', report_id: d.result.report_id }
                setResults([...updated])
                break
              }
              if (isTaskFailure(d.status)) {
                updated[i] = { code: tasks[i].stock_code, status: d.status, report_id: undefined }
                setResults([...updated])
                break
              }
            } catch {}
          }
        }
        setLoading(false)
      }
      pollAll()
    } catch (err: any) {
      setError(errMsg(err, '提交失败'))
      setLoading(false)
    }
  }

  return (
    <div className="panel-stack">
      <section className="card">
        <div className="field">
          <label>股票代码列表</label>
          <textarea className="textarea mono" value={codesText} onChange={(e) => setCodesText(e.target.value)}
            placeholder="输入股票代码，逗号或换行分隔，最多50只&#10;例如: 600519,000858,002714" rows={5} />
        </div>
        <button className="btn btn-primary mt16" onClick={submit} disabled={loading}>
          {loading ? '批量分析中...' : '开始批量分析'}
        </button>
        {error && <p className="small mt8" style={{ color: 'var(--up)' }}>{error}</p>}
      </section>

      {results.length > 0 && (
        <section className="card" style={{ padding: 0 }}>
          <div style={{ overflowX: 'auto' }}>
            <table className="table">
              <thead>
                <tr><th>股票代码</th><th>状态</th></tr>
              </thead>
              <tbody>
                {results.map((r, i) => (
                  <tr key={i}>
                    <td className="mono">{r.code}</td>
                    <td>
                      {r.status === 'success' && <span className="badge down">完成</span>}
                      {isTaskFailure(r.status) && <span className="badge up">失败</span>}
                      {r.status === 'pending' && <span className="badge">等待中...</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </div>
  )
}

function HistoryView() {
  const [items, setItems] = useState<any[]>([])
  const [selected, setSelected] = useState<ReportData | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    client.get('/stocks/user/results').then((r) => {
      setItems(r.data.data.items || [])
      setLoading(false)
    }).catch(() => setLoading(false))
  }, [])

  const viewDetail = async (id: number) => {
    const r = await client.get(`/stocks/user/results/${id}`)
    setSelected({ ...r.data.data.report, _id: r.data.data.id })
  }

  if (selected) return <ReportView report={selected} onBack={() => setSelected(null)} />

  return (
    <section className="card" style={{ padding: 0 }}>
      {loading ? (
        <div className="empty" style={{ margin: 24 }}>加载中...</div>
      ) : items.length === 0 ? (
        <div className="empty" style={{ margin: 24 }}>暂无分析记录</div>
      ) : (
        <div style={{ overflowX: 'auto' }}>
          <table className="table">
            <thead>
              <tr><th>股票</th><th>评级</th><th className="num">置信度</th><th>分析日期</th><th>操作</th></tr>
            </thead>
            <tbody>
              {items.map((item) => (
                <tr key={item.id}>
                  <td>{item.stock_name} <span className="muted mono">{item.stock_code}</span></td>
                  <td><span className={RATING_BADGE[item.rating] || 'badge'}>{item.rating || '-'}</span></td>
                  <td className="num mono">{item.confidence}%</td>
                  <td className="muted">{item.created_at?.slice(0, 10)}</td>
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

function ExportPdfButton({ reportId, stockCode }: { reportId: number; stockCode: string }) {
  const [downloading, setDownloading] = useState(false)
  const download = async () => {
    setDownloading(true)
    try {
      const resp = await client.get(`/stocks/user/results/${reportId}/pdf`, { responseType: 'blob' })
      const url = URL.createObjectURL(resp.data)
      const a = document.createElement('a')
      a.href = url
      a.download = `${stockCode}_report_${reportId}.pdf`
      a.click()
      URL.revokeObjectURL(url)
    } finally {
      setDownloading(false)
    }
  }
  return (
    <button className="btn btn-ghost" onClick={download} disabled={downloading}>
      {downloading ? '生成中...' : '导出PDF'}
    </button>
  )
}

function ReportView({ report, onBack }: { report: ReportData; onBack?: () => void }) {
  const d = report.decision || {}
  const fmt = (v: any) => (v != null ? v : 'N/A')
  const pct = report.stock_info?.change_pct
  return (
    <div className="panel-stack">
      {onBack && <button className="btn-text" style={{ alignSelf: 'start' }} onClick={onBack}>← 返回列表</button>}

      {/* 股票快照 */}
      <section className="card">
        <div className="between wrap" style={{ marginBottom: 16 }}>
          <h2 className="card-title" style={{ margin: 0 }}>
            {report.stock_name} <span className="muted mono" style={{ fontSize: 16 }}>{report.stock_code}</span>
          </h2>
          {pct != null && <span className={`badge ${pct >= 0 ? 'up' : 'down'}`}>{pct >= 0 ? '+' : ''}{pct}%</span>}
        </div>
        <div className="grid3">
          <div className="kpi"><div className="k-label">最新价</div><div className="k-value mono" style={{ fontSize: 24 }}>{fmt(report.stock_info?.price)}</div></div>
          <div className="kpi"><div className="k-label">涨跌幅</div><div className={`k-value mono ${pct >= 0 ? 'up' : 'down'}`} style={{ fontSize: 24 }}>{fmt(pct)}%</div></div>
          <div className="kpi"><div className="k-label">PE(TTM)</div><div className="k-value mono" style={{ fontSize: 24 }}>{fmt(report.stock_info?.pe_ttm)}</div></div>
          <div className="kpi"><div className="k-label">PB</div><div className="k-value mono" style={{ fontSize: 24 }}>{fmt(report.stock_info?.pb)}</div></div>
          <div className="kpi"><div className="k-label">市值(亿)</div><div className="k-value mono" style={{ fontSize: 24 }}>{fmt(report.stock_info?.market_cap)}</div></div>
          <div className="kpi"><div className="k-label">行业</div><div className="k-value" style={{ fontSize: 24 }}>{fmt(report.stock_info?.industry)}</div></div>
        </div>
        {report.analyzed_at && <p className="caption mt16">分析时间 {report.analyzed_at}</p>}
      </section>

      {/* 技术指标 */}
      <section className="card">
        <h2 className="card-title">技术指标</h2>
        <div className="mini-grid">
          <IndicatorCard title="MA" data={report.indicators?.ma} keys={['MA5', 'MA20', 'MA60']} />
          <IndicatorCard title="MACD" data={report.indicators?.macd} keys={['DIF', 'DEA', 'MACD']} />
          <IndicatorCard title="RSI(14)" data={report.indicators?.rsi} keys={['RSI']} />
          <IndicatorCard title="KDJ" data={report.indicators?.kdj} keys={['K', 'D', 'J']} />
          <IndicatorCard title="BOLL" data={report.indicators?.boll} keys={['UP', 'MID', 'LOW']} labels={['上轨', '中轨', '下轨']} />
        </div>
        <p className="caption mt16">基于近 60 个交易日计算</p>
      </section>

      {/* AI 分析师报告 */}
      <section className="card">
        <h2 className="card-title">AI 分析师报告</h2>
        <div className="mini-grid">
          {Object.entries(report.analysts || {}).map(([key, data]: [string, any]) => (
            <div className="mini-card" key={key}>
              <div className="between">
                <h3 style={{ margin: 0 }}>{ANALYST_LABELS[key] || key}</h3>
                {data?.score != null && <span className="fg2 small">评分: <span className="mono">{data.score}</span></span>}
              </div>
              <div className="small fg2 mt8">
                {data?.trend && <p style={{ margin: 0 }}>趋势: {data.trend}</p>}
                {data?.detail && <p style={{ margin: 0 }}>{data.detail}</p>}
                {data?.sentiment_rating && <p style={{ margin: 0 }}>评级: {data.sentiment_rating}</p>}
                {data?.main_flow && <p style={{ margin: 0 }}>{data.main_flow}</p>}
                {data?.assessment && <p style={{ margin: 0 }}>{data.assessment}</p>}
                {data?.financial_health && <p style={{ margin: 0 }}>财务健康度: {data.financial_health}</p>}
                {data?.error && <p style={{ margin: 0, color: 'var(--up)' }}>分析失败: {data.error}</p>}
              </div>
              <details className="mt8">
                <summary>展开完整报告</summary>
                <p className="full-report">{JSON.stringify(data, null, 2)}</p>
              </details>
            </div>
          ))}
        </div>
      </section>

      {/* 最终决策 */}
      <section className="card card-accent">
        <h2 className="card-title">AI 投研会议 · 最终决策</h2>
        {d.meeting_summary && <p className="small fg2" style={{ background: 'var(--surface)', padding: 12, borderRadius: 'var(--r-card)' }}>{d.meeting_summary}</p>}
        <div className="kpi-grid mt16">
          <div className="kpi"><div className="k-label">最终决策</div><div className="k-value" style={{ fontSize: 24 }}><span className={RATING_BADGE[d.rating] || 'badge'}>{d.rating || '-'}</span></div></div>
          <div className="kpi"><div className="k-label">目标价</div><div className="k-value mono" style={{ fontSize: 24 }}>¥{fmt(d.target_price)}</div></div>
          <div className="kpi"><div className="k-label">止损价</div><div className="k-value mono" style={{ fontSize: 24 }}>¥{fmt(d.stop_loss)}</div></div>
          <div className="kpi"><div className="k-label">置信度</div><div className="k-value mono" style={{ fontSize: 24 }}>{fmt(d.confidence)}%</div></div>
        </div>
        <ul className="kv-list mt16">
          <li><span className="k">入场区间</span><span>{fmt(d.entry_range)}</span></li>
          <li><span className="k">止盈目标</span><span>{fmt(d.take_profit)}</span></li>
          <li><span className="k">持有期限</span><span>{fmt(d.holding_period)}</span></li>
          <li><span className="k">仓位建议</span><span>{fmt(d.position_size)}</span></li>
          <li><span className="k">风险提示</span><span style={{ textAlign: 'right' }}>{fmt(d.risk_warning)}</span></li>
        </ul>
        {d.key_watchpoints && d.key_watchpoints.length > 0 && (
          <div className="mt16">
            <span className="section-label">关键观察指标</span>
            <ul className="obs-list">
              {d.key_watchpoints.map((p, i) => <li key={i}>{p}</li>)}
            </ul>
          </div>
        )}
        <div className="between wrap mt24">
          <p className="caption" style={{ margin: 0 }}>{report.disclaimer}</p>
          {report._id && <ExportPdfButton reportId={report._id} stockCode={report.stock_code} />}
        </div>
      </section>
    </div>
  )
}

function IndicatorCard({ title, data, keys, labels }: { title: string; data?: Record<string, number|null>; keys: string[]; labels?: string[] }) {
  return (
    <div className="mini-card">
      <h3>{title}</h3>
      <ul className="kv-list">
        {keys.map((k, i) => (
          <li key={k}>
            <span className="k">{labels?.[i] || k}</span>
            <span className="mono">{data?.[k] != null ? data[k] : 'N/A'}</span>
          </li>
        ))}
      </ul>
    </div>
  )
}
