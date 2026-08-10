import { useState, useEffect, useCallback } from 'react'
import client from '../api/client'
import { errMsg } from '../utils/errors'

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

const RATING_COLORS: Record<string, string> = {
  '买入': 'text-red-600 bg-red-50',
  '持有': 'text-yellow-600 bg-yellow-50',
  '卖出': 'text-green-600 bg-green-50',
}

export default function Analysis() {
  const [tab, setTab] = useState<Tab>('single')
  return (
    <div className="space-y-6">
      <div className="flex gap-2 border-b border-gray-200">
        {(['single', 'batch', 'history'] as Tab[]).map((t) => (
          <button key={t} onClick={() => setTab(t)}
            className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors ${
              tab === t ? 'border-brand-600 text-brand-600' : 'border-transparent text-gray-500 hover:text-gray-700'
            }`}>
            {t === 'single' ? '单股分析' : t === 'batch' ? '批量分析' : '历史记录'}
          </button>
        ))}
      </div>
      {tab === 'single' && <SingleAnalysis />}
      {tab === 'batch' && <BatchAnalysis />}
      {tab === 'history' && <HistoryView />}
    </div>
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
        if (data.status === 'failed') {
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
    <div className="space-y-4">
      <div className="flex gap-3">
        <input value={code} onChange={(e) => setCode(e.target.value)}
          placeholder="输入股票代码，如 600519"
          className="flex-1 px-4 py-2 border border-gray-300 rounded-lg focus:outline-none focus:border-brand-500" />
        <button onClick={submit} disabled={loading}
          className="px-6 py-2 bg-brand-600 text-white rounded-lg font-medium hover:bg-brand-700 disabled:opacity-50">
          {loading ? '分析中...' : '开始分析'}
        </button>
      </div>

      {error && <p className="text-sm text-red-500">{error}</p>}

      {loading && (
        <div className="bg-white rounded-lg shadow p-6">
          <p className="text-sm text-gray-600 mb-2">分析进度: {progress}% ({status})</p>
          <div className="w-full bg-gray-200 rounded-full h-2">
            <div className="bg-brand-600 h-2 rounded-full transition-all" style={{ width: `${progress}%` }} />
          </div>
        </div>
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
      // poll all tasks
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
              if (d.status === 'failed') {
                updated[i] = { code: tasks[i].stock_code, status: 'failed' }
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
    <div className="space-y-4">
      <textarea value={codesText} onChange={(e) => setCodesText(e.target.value)}
        placeholder="输入股票代码，逗号或换行分隔，最多50只&#10;例如: 600519,000858,002714"
        rows={5}
        className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:outline-none focus:border-brand-500 font-mono text-sm" />
      <button onClick={submit} disabled={loading}
        className="px-6 py-2 bg-brand-600 text-white rounded-lg font-medium hover:bg-brand-700 disabled:opacity-50">
        {loading ? '批量分析中...' : '开始批量分析'}
      </button>
      {error && <p className="text-sm text-red-500">{error}</p>}
      {results.length > 0 && (
        <div className="bg-white rounded-lg shadow overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-gray-50">
              <tr>
                <th className="px-4 py-2 text-left">股票代码</th>
                <th className="px-4 py-2 text-left">状态</th>
              </tr>
            </thead>
            <tbody>
              {results.map((r, i) => (
                <tr key={i} className="border-t border-gray-100">
                  <td className="px-4 py-2 font-mono">{r.code}</td>
                  <td className="px-4 py-2">
                    {r.status === 'success' && <span className="text-green-600">完成</span>}
                    {r.status === 'failed' && <span className="text-red-500">失败</span>}
                    {r.status === 'pending' && <span className="text-gray-400">等待中...</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
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
    <div className="bg-white rounded-lg shadow overflow-hidden">
      {loading ? (
        <p className="p-8 text-center text-gray-400">加载中...</p>
      ) : items.length === 0 ? (
        <p className="p-8 text-center text-gray-400">暂无分析记录</p>
      ) : (
        <table className="w-full text-sm">
          <thead className="bg-gray-50">
            <tr>
              <th className="px-4 py-2 text-left">股票</th>
              <th className="px-4 py-2 text-left">评级</th>
              <th className="px-4 py-2 text-left">置信度</th>
              <th className="px-4 py-2 text-left">分析日期</th>
              <th className="px-4 py-2 text-left">操作</th>
            </tr>
          </thead>
          <tbody>
            {items.map((item) => (
              <tr key={item.id} className="border-t border-gray-100 hover:bg-gray-50">
                <td className="px-4 py-2">{item.stock_name} <span className="text-gray-400 font-mono">{item.stock_code}</span></td>
                <td className="px-4 py-2">
                  <span className={`px-2 py-0.5 rounded text-xs ${RATING_COLORS[item.rating] || 'text-gray-500 bg-gray-50'}`}>
                    {item.rating || '-'}
                  </span>
                </td>
                <td className="px-4 py-2">{item.confidence}%</td>
                <td className="px-4 py-2 text-gray-500">{item.created_at?.slice(0, 10)}</td>
                <td className="px-4 py-2">
                  <button onClick={() => viewDetail(item.id)} className="text-brand-600 hover:underline">查看详情</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
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
    <button onClick={download} disabled={downloading}
      className="px-4 py-1.5 text-sm border border-brand-500 text-brand-600 rounded-lg hover:bg-brand-50 disabled:opacity-50">
      {downloading ? '生成中...' : '导出PDF'}
    </button>
  )
}

function ReportView({ report, onBack }: { report: ReportData; onBack?: () => void }) {
  const d = report.decision || {}
  const fmt = (v: any) => (v != null ? v : 'N/A')
  return (
    <div className="space-y-4">
      {onBack && (
        <button onClick={onBack} className="text-sm text-brand-600 hover:underline">← 返回列表</button>
      )}

      {/* Stock snapshot */}
      <div className="bg-white rounded-lg shadow p-6">
        <div className="flex items-baseline gap-4 mb-3">
          <span className="text-xl font-bold">{report.stock_name}</span>
          <span className="text-sm text-gray-400 font-mono">{report.stock_code}</span>
        </div>
        <div className="grid grid-cols-6 gap-4 text-sm">
          <div><span className="text-gray-400">最新价</span><br /><span className="font-bold">{fmt(report.stock_info?.price)}</span></div>
          <div><span className="text-gray-400">涨跌幅</span><br /><span className={`font-bold ${report.stock_info?.change_pct >= 0 ? 'text-red-500' : 'text-green-500'}`}>{fmt(report.stock_info?.change_pct)}%</span></div>
          <div><span className="text-gray-400">PE(TTM)</span><br /><span className="font-bold">{fmt(report.stock_info?.pe_ttm)}</span></div>
          <div><span className="text-gray-400">PB</span><br /><span className="font-bold">{fmt(report.stock_info?.pb)}</span></div>
          <div><span className="text-gray-400">市值(亿)</span><br /><span className="font-bold">{fmt(report.stock_info?.market_cap)}</span></div>
          <div><span className="text-gray-400">行业</span><br /><span className="font-bold">{fmt(report.stock_info?.industry)}</span></div>
        </div>
      </div>

      {/* Technical indicators */}
      <div className="bg-white rounded-lg shadow p-6">
        <h3 className="text-lg font-semibold mb-4">技术指标面板</h3>
        <div className="grid grid-cols-5 gap-4 text-sm">
          <IndicatorCard title="MA" data={report.indicators?.ma} keys={['MA5', 'MA20', 'MA60']} />
          <IndicatorCard title="MACD" data={report.indicators?.macd} keys={['DIF', 'DEA', 'MACD']} />
          <IndicatorCard title="RSI(14)" data={report.indicators?.rsi} keys={['RSI']} />
          <IndicatorCard title="KDJ" data={report.indicators?.kdj} keys={['K', 'D', 'J']} />
          <IndicatorCard title="BOLL" data={report.indicators?.boll} keys={['UP', 'MID', 'LOW']} labels={['上轨', '中轨', '下轨']} />
        </div>
      </div>

      {/* AI Analysts */}
      <div className="space-y-3">
        <h3 className="text-lg font-semibold">AI 分析师报告</h3>
        {Object.entries(report.analysts || {}).map(([key, data]: [string, any]) => (
          <div key={key} className="bg-white rounded-lg shadow p-4">
            <div className="flex items-center justify-between mb-2">
              <span className="font-medium">{ANALYST_LABELS[key] || key}</span>
              {data?.score != null && (
                <span className="text-sm text-brand-600">评分: {data.score}</span>
              )}
            </div>
            <div className="text-sm text-gray-600 space-y-1">
              {data?.trend && <p>趋势: {data.trend}</p>}
              {data?.detail && <p>{data.detail}</p>}
              {data?.sentiment_rating && <p>评级: {data.sentiment_rating}</p>}
              {data?.main_flow && <p>{data.main_flow}</p>}
              {data?.assessment && <p>{data.assessment}</p>}
              {data?.financial_health && <p>财务健康度: {data.financial_health}</p>}
              {data?.error && <p className="text-red-400">分析失败: {data.error}</p>}
            </div>
            <details className="mt-2">
              <summary className="text-xs text-brand-600 cursor-pointer">展开完整报告</summary>
              <pre className="mt-2 text-xs text-gray-500 overflow-x-auto bg-gray-50 p-3 rounded">{JSON.stringify(data, null, 2)}</pre>
            </details>
          </div>
        ))}
      </div>

      {/* Decision card */}
      <div className="bg-white rounded-lg shadow p-6 border-l-4 border-brand-500">
        <h3 className="text-lg font-semibold mb-4">AI 投研会议 · 最终决策</h3>
        {d.meeting_summary && (
          <p className="text-sm text-gray-600 mb-4 bg-gray-50 p-3 rounded">{d.meeting_summary}</p>
        )}
        <div className="grid grid-cols-4 gap-4 text-sm mb-4">
          <div>
            <span className="text-gray-400">最终决策</span>
            <p className={`text-lg font-bold px-2 py-0.5 rounded inline-block ${RATING_COLORS[d.rating] || ''}`}>{d.rating || '-'}</p>
          </div>
          <div><span className="text-gray-400">目标价</span><p className="font-bold">¥{fmt(d.target_price)}</p></div>
          <div><span className="text-gray-400">止损价</span><p className="font-bold">¥{fmt(d.stop_loss)}</p></div>
          <div><span className="text-gray-400">置信度</span><p className="font-bold">{fmt(d.confidence)}%</p></div>
        </div>
        <div className="grid grid-cols-3 gap-4 text-sm mb-4">
          <div><span className="text-gray-400">入场区间</span><p>{fmt(d.entry_range)}</p></div>
          <div><span className="text-gray-400">止盈目标</span><p>{fmt(d.take_profit)}</p></div>
          <div><span className="text-gray-400">持有期限</span><p>{fmt(d.holding_period)}</p></div>
        </div>
        <div className="grid grid-cols-2 gap-4 text-sm mb-4">
          <div><span className="text-gray-400">仓位建议</span><p>{fmt(d.position_size)}</p></div>
          <div><span className="text-gray-400">风险提示</span><p>{fmt(d.risk_warning)}</p></div>
        </div>
        {d.key_watchpoints && d.key_watchpoints.length > 0 && (
          <div>
            <span className="text-gray-400 text-sm">关键观察指标</span>
            <ul className="mt-1 text-sm text-gray-600 list-disc list-inside">
              {d.key_watchpoints.map((p, i) => <li key={i}>{p}</li>)}
            </ul>
          </div>
        )}
        <div className="mt-6 flex items-center justify-between">
          <p className="text-xs text-gray-400">{report.disclaimer}</p>
          {report._id && <ExportPdfButton reportId={report._id} stockCode={report.stock_code} />}
        </div>
      </div>
    </div>
  )
}

function IndicatorCard({ title, data, keys, labels }: { title: string; data?: Record<string, number|null>; keys: string[]; labels?: string[] }) {
  return (
    <div className="border border-gray-200 rounded-lg p-3">
      <p className="text-xs text-gray-400 mb-2">{title}</p>
      {keys.map((k, i) => (
        <div key={k} className="flex justify-between text-sm">
          <span className="text-gray-500">{labels?.[i] || k}</span>
          <span className="font-mono font-medium">{data?.[k] != null ? data[k] : 'N/A'}</span>
        </div>
      ))}
    </div>
  )
}
