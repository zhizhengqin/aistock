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

const GRADE_COLORS: Record<string, string> = {
  A: 'text-red-600 bg-red-50',
  B: 'text-orange-600 bg-orange-50',
  C: 'text-yellow-600 bg-yellow-50',
  D: 'text-gray-500 bg-gray-50',
}

export default function DragonTiger() {
  const [tab, setTab] = useState<Tab>('analysis')
  return (
    <div className="space-y-6">
      <div className="flex gap-2 border-b border-gray-200">
        {(['analysis', 'history', 'stats'] as Tab[]).map((t) => (
          <button key={t} onClick={() => setTab(t)}
            className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors ${tab === t ? 'border-brand-600 text-brand-600' : 'border-transparent text-gray-500 hover:text-gray-700'}`}>
            {t === 'analysis' ? '龙虎榜分析' : t === 'history' ? '历史报告' : '数据统计'}
          </button>
        ))}
      </div>
      {tab === 'analysis' && <AnalysisView />}
      {tab === 'history' && <HistoryView />}
      {tab === 'stats' && <StatsView />}
    </div>
  )
}

function AnalysisView() {
  const [periodDays, setPeriodDays] = useState(5)
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
          const r2 = await client.get(`/stocks/dragon-tiger/reports/${d.result.report_id}`)
          setReport(r2.data.data)
          setLoading(false); return
        }
        if (d.status === 'failed') { setError(d.error || '分析失败'); setLoading(false); return }
      } catch {}
    }
    setError('轮询超时'); setLoading(false)
  }, [])

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
    <div className="space-y-4">
      <div className="flex items-center gap-4">
        <span className="text-sm text-gray-500">分析时间范围</span>
        <div className="flex gap-2">
          {PERIOD_OPTIONS.map((d) => (
            <button key={d} onClick={() => setPeriodDays(d)}
              className={`px-3 py-1.5 text-sm rounded-lg transition-colors ${periodDays === d ? 'bg-brand-600 text-white' : 'bg-gray-100 text-gray-600 hover:bg-gray-200'}`}>
              {d}天
            </button>
          ))}
        </div>
        <button onClick={submit} disabled={loading}
          className="px-6 py-2 bg-brand-600 text-white rounded-lg font-medium hover:bg-brand-700 disabled:opacity-50">
          {loading ? '分析中...' : '开始分析'}
        </button>
      </div>

      {error && <p className="text-sm text-red-500">{error}</p>}

      {loading && (
        <div className="bg-white rounded-lg shadow p-6">
          <p className="text-sm text-gray-600 mb-2">进度: {progress}% ({status})</p>
          <div className="w-full bg-gray-200 rounded-full h-2">
            <div className="bg-brand-600 h-2 rounded-full transition-all" style={{ width: `${progress}%` }} />
          </div>
        </div>
      )}

      {report && (
        <>
          {/* AI Summary */}
          <div className="bg-white rounded-lg shadow p-6 border-l-4 border-brand-500">
            <h3 className="text-lg font-semibold mb-3">AI 分析摘要</h3>
            <div className="grid grid-cols-4 gap-4 text-sm mb-4">
              <div><span className="text-gray-400">信心评分</span><p className="text-xl font-bold text-brand-600">{report.analysis?.confidence_score ?? 'N/A'}</p></div>
              <div><span className="text-gray-400">风险等级</span><p className="text-lg font-bold text-red-500">{report.analysis?.risk_level ?? 'N/A'}</p></div>
              <div><span className="text-gray-400">总记录数</span><p className="font-bold">{report.data_summary?.total_records ?? 0}</p></div>
              <div><span className="text-gray-400">上榜股票数</span><p className="font-bold">{report.data_summary?.unique_stocks ?? 0}</p></div>
            </div>
            <p className="text-sm text-gray-600 bg-gray-50 p-3 rounded">{report.analysis?.summary}</p>
            {report.data_summary?.date_range && (
              <p className="text-xs text-gray-400 mt-2">数据范围: {report.data_summary.date_range.join(' ~ ')}</p>
            )}
          </div>

          {/* TOP10 Table */}
          <div className="bg-white rounded-lg shadow overflow-hidden">
            <h3 className="text-lg font-semibold p-4">龙虎榜推荐 TOP10</h3>
            <table className="w-full text-sm">
              <thead className="bg-gray-50"><tr>
                <th className="px-3 py-2 text-left">排名</th>
                <th className="px-3 py-2 text-left">股票</th>
                <th className="px-3 py-2 text-left">综合评分</th>
                <th className="px-3 py-2 text-left">净流入(亿)</th>
                <th className="px-3 py-2 text-left">买入(亿)</th>
                <th className="px-3 py-2 text-left">卖出(亿)</th>
                <th className="px-3 py-2 text-left">上榜次数</th>
                <th className="px-3 py-2 text-left">上榜类型</th>
              </tr></thead>
              <tbody>
                {report.top_stocks?.map((s, i) => (
                  <tr key={i} className="border-t border-gray-100 hover:bg-gray-50">
                    <td className="px-3 py-2 font-bold text-gray-400">{i + 1}</td>
                    <td className="px-3 py-2 font-medium">{s.name} <span className="text-gray-400 font-mono">{s.code}</span></td>
                    <td className="px-3 py-2">
                      <span className={`px-2 py-0.5 rounded text-xs font-bold ${GRADE_COLORS[s.grade] || 'text-gray-500 bg-gray-50'}`}>
                        {s.score}分 {s.grade}
                      </span>
                    </td>
                    <td className="px-3 py-2 text-red-500">{(s.buy_amount - s.sell_amount).toFixed(2)}</td>
                    <td className="px-3 py-2 text-red-500">{s.buy_amount.toFixed(2)}</td>
                    <td className="px-3 py-2 text-green-500">{s.sell_amount.toFixed(2)}</td>
                    <td className="px-3 py-2">{s.appearances}</td>
                    <td className="px-3 py-2 text-gray-500">{s.reasons[0] || '-'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* Active institutions */}
          {report.institutions && report.institutions.length > 0 && (
            <div className="bg-white rounded-lg shadow overflow-hidden">
              <h3 className="text-lg font-semibold p-4">活跃游资画像</h3>
              <table className="w-full text-sm">
                <thead className="bg-gray-50"><tr>
                  <th className="px-3 py-2 text-left">营业部</th>
                  <th className="px-3 py-2 text-left">上榜次数</th>
                  <th className="px-3 py-2 text-left">成功率</th>
                </tr></thead>
                <tbody>
                  {report.institutions.map((inst, i) => (
                    <tr key={i} className="border-t border-gray-100">
                      <td className="px-3 py-2 font-medium">{inst.name}</td>
                      <td className="px-3 py-2">{inst.appearances}</td>
                      <td className="px-3 py-2">{inst.success_rate}%</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {/* AI Strategy advice */}
          {report.analysis?.strategy_advice && (
            <div className="bg-white rounded-lg shadow p-6 border-l-4 border-orange-400">
              <h3 className="text-lg font-semibold mb-2">AI 策略建议</h3>
              <p className="text-sm text-gray-600 whitespace-pre-line">{report.analysis.strategy_advice}</p>
            </div>
          )}
        </>
      )}
    </div>
  )
}

function HistoryView() {
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
    <div className="bg-white rounded-lg shadow overflow-hidden">
      {loading ? <p className="p-8 text-center text-gray-400">加载中...</p> :
       items.length === 0 ? <p className="p-8 text-center text-gray-400">暂无龙虎榜报告</p> :
       <table className="w-full text-sm">
         <thead className="bg-gray-50"><tr>
           <th className="px-4 py-2 text-left">分析天数</th>
           <th className="px-4 py-2 text-left">分析时间</th>
         </tr></thead>
         <tbody>
           {items.map((item) => (
             <tr key={item.id} className="border-t border-gray-100 hover:bg-gray-50">
               <td className="px-4 py-2">{item.period_days}天</td>
               <td className="px-4 py-2 text-gray-500">{item.created_at?.slice(0, 19)}</td>
             </tr>
           ))}
         </tbody>
       </table>}
    </div>
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

  if (loading) return <p className="text-center text-gray-400 p-8">加载中...</p>

  return (
    <div className="bg-white rounded-lg shadow p-6">
      <h3 className="text-lg font-semibold mb-4">数据统计</h3>
      <div className="grid grid-cols-3 gap-4 text-sm">
        <div><span className="text-gray-400">累计报告数</span><p className="text-2xl font-bold text-brand-600">{stats?.total_reports ?? 0}</p></div>
        <div><span className="text-gray-400">最近分析天数</span><p className="text-lg font-bold">{stats?.latest_period ? `${stats.latest_period}天` : 'N/A'}</p></div>
        <div><span className="text-gray-400">最近分析时间</span><p className="text-sm font-bold">{stats?.latest_created?.slice(0, 19) ?? 'N/A'}</p></div>
      </div>
    </div>
  )
}
