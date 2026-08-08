import { useState, useCallback, useEffect } from "react"
import client from '../api/client'
import { errMsg } from '../utils/errors'

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
    <div className="space-y-6">
      <div className="flex gap-2 border-b border-gray-200">
        {(['analysis', 'history'] as Tab[]).map((t) => (
          <button key={t} onClick={() => setTab(t)}
            className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors ${tab === t ? 'border-brand-600 text-brand-600' : 'border-transparent text-gray-500 hover:text-gray-700'}`}>
            {t === 'analysis' ? '选股分析' : '历史记录'}
          </button>
        ))}
      </div>
      {tab === 'analysis' && <AnalysisView />}
      {tab === 'history' && <HistoryView />}
    </div>
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
        if (d.status === 'failed') {
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
    <div className="space-y-4">
      <div className="flex items-center gap-4">
        <p className="text-sm text-gray-500">5 位 AI 分析师 + 资深研究员根据主力资金流向精选标的</p>
        <button onClick={submit} disabled={loading}
          className="px-6 py-2 bg-brand-600 text-white rounded-lg font-medium hover:bg-brand-700 disabled:opacity-50">
          {loading ? '选股中...' : '开始选股'}
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

      {runData && analysis && (
        <>
          {/* Funnel */}
          <div className="bg-white rounded-lg shadow p-6">
            <h3 className="text-lg font-semibold mb-4">选股漏斗</h3>
            <div className="flex items-center gap-4 text-sm">
              <div className="text-center"><p className="text-2xl font-bold text-brand-600">{runData.candidates_count}</p><p className="text-gray-400">候选股票</p></div>
              <span className="text-gray-300 text-xl">→</span>
              <div className="text-center"><p className="text-2xl font-bold text-brand-600">{runData.filtered_count}</p><p className="text-gray-400">筛选通过</p></div>
              <span className="text-gray-300 text-xl">→</span>
              <div className="text-center"><p className="text-2xl font-bold text-red-500">{rec.companies?.length || 0}</p><p className="text-gray-400">最终推荐</p></div>
            </div>
            <div className="mt-4 flex gap-6 text-xs text-gray-400">
              <span>策略: 流通市值{'>'}{analysis.strategy?.min_market_cap}亿 | 20日涨幅{'<'}{analysis.strategy?.max_20d_change_pct}% | 60日净流入{'>'}0 | 股东户数下降</span>
            </div>
          </div>

          {/* 5 Analyst cards */}
          <div className="space-y-3">
            <h3 className="text-lg font-semibold">5 位 AI 分析师</h3>
            {Object.entries(analysis.analysts || {}).map(([key, data]: [string, any]) => (
              <div key={key} className="bg-white rounded-lg shadow p-4">
                <div className="flex items-center justify-between mb-2">
                  <span className="font-medium">{ANALYST_LABELS[key] || key}</span>
                  {data?.score != null && <span className="text-sm text-brand-600">评分: {data.score}</span>}
                </div>
                <p className="text-sm text-gray-600">{data?.analysis || data?.error || ''}</p>
                <details className="mt-2">
                  <summary className="text-xs text-brand-600 cursor-pointer">展开完整报告</summary>
                  <pre className="mt-2 text-xs text-gray-500 overflow-x-auto bg-gray-50 p-3 rounded">{JSON.stringify(data, null, 2)}</pre>
                </details>
              </div>
            ))}
          </div>

          {/* Researcher recommendation */}
          <div className="bg-white rounded-lg shadow p-6 border-l-4 border-brand-500">
            <h3 className="text-lg font-semibold mb-4">资深研究员 · 精选推荐</h3>
            {rec.meeting_summary && <p className="text-sm text-gray-600 mb-4 bg-gray-50 p-3 rounded">{rec.meeting_summary}</p>}
            {rec.companies && (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead className="bg-gray-50"><tr>
                    <th className="px-3 py-2 text-left">股票</th>
                    <th className="px-3 py-2 text-left">买入区间</th>
                    <th className="px-3 py-2 text-left">卖出区间</th>
                    <th className="px-3 py-2 text-left">置信度</th>
                    <th className="px-3 py-2 text-left">仓位</th>
                    <th className="px-3 py-2 text-left">推荐逻辑</th>
                  </tr></thead>
                  <tbody>
                    {rec.companies.map((c: any, i: number) => (
                      <tr key={i} className="border-t border-gray-100">
                        <td className="px-3 py-2 font-medium">{c.name} <span className="text-gray-400 font-mono">{c.code}</span></td>
                        <td className="px-3 py-2">{c.buy_range}</td>
                        <td className="px-3 py-2">{c.sell_range}</td>
                        <td className="px-3 py-2">{c.confidence}%</td>
                        <td className="px-3 py-2">{c.position}</td>
                        <td className="px-3 py-2 text-gray-600">{c.logic}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            {rec.excluded && rec.excluded.length > 0 && (
              <div className="mt-4">
                <p className="text-sm text-gray-400 mb-1">被排除标的:</p>
                {rec.excluded.map((e: any, i: number) => (
                  <p key={i} className="text-sm text-gray-500">{e.name}({e.code}) - {e.reason}</p>
                ))}
              </div>
            )}
          </div>
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
      <div className="space-y-4">
        <button onClick={() => setDetail(null)} className="text-sm text-brand-600 hover:underline">← 返回列表</button>
        <div className="bg-white rounded-lg shadow p-6">
          <h3 className="text-lg font-semibold mb-4">选股漏斗 · {detail.run_date}</h3>
          <div className="flex items-center gap-4 text-sm">
            <div className="text-center"><p className="text-2xl font-bold text-brand-600">{detail.candidates_count}</p><p className="text-gray-400">候选</p></div>
            <span className="text-gray-300 text-xl">→</span>
            <div className="text-center"><p className="text-2xl font-bold text-brand-600">{detail.filtered_count}</p><p className="text-gray-400">筛选</p></div>
            <span className="text-gray-300 text-xl">→</span>
            <div className="text-center"><p className="text-2xl font-bold text-red-500">{rec.companies?.length || 0}</p><p className="text-gray-400">推荐</p></div>
          </div>
        </div>
        {rec.companies && (
          <div className="bg-white rounded-lg shadow p-6">
            <h3 className="text-lg font-semibold mb-3">精选推荐</h3>
            <table className="w-full text-sm">
              <thead className="bg-gray-50"><tr>
                <th className="px-3 py-2 text-left">股票</th><th className="px-3 py-2 text-left">买入区间</th>
                <th className="px-3 py-2 text-left">卖出区间</th><th className="px-3 py-2 text-left">置信度</th>
                <th className="px-3 py-2 text-left">仓位</th><th className="px-3 py-2 text-left">逻辑</th>
              </tr></thead>
              <tbody>
                {rec.companies.map((c: any, i: number) => (
                  <tr key={i} className="border-t border-gray-100">
                    <td className="px-3 py-2 font-medium">{c.name} <span className="text-gray-400 font-mono">{c.code}</span></td>
                    <td className="px-3 py-2">{c.buy_range}</td><td className="px-3 py-2">{c.sell_range}</td>
                    <td className="px-3 py-2">{c.confidence}%</td><td className="px-3 py-2">{c.position}</td>
                    <td className="px-3 py-2 text-gray-600">{c.logic}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    )
  }

  return (
    <div className="bg-white rounded-lg shadow overflow-hidden">
      {loading ? <p className="p-8 text-center text-gray-400">加载中...</p> :
       items.length === 0 ? <p className="p-8 text-center text-gray-400">暂无选股记录</p> :
       <table className="w-full text-sm">
         <thead className="bg-gray-50"><tr>
           <th className="px-4 py-2 text-left">选股日期</th><th className="px-4 py-2 text-left">候选数</th>
           <th className="px-4 py-2 text-left">筛选数</th><th className="px-4 py-2 text-left">操作</th>
         </tr></thead>
         <tbody>
           {items.map((item) => (
             <tr key={item.id} className="border-t border-gray-100 hover:bg-gray-50">
               <td className="px-4 py-2">{item.run_date}</td>
               <td className="px-4 py-2">{item.candidates_count}</td>
               <td className="px-4 py-2">{item.filtered_count}</td>
               <td className="px-4 py-2"><button onClick={() => viewDetail(item.id)} className="text-brand-600 hover:underline">查看详情</button></td>
             </tr>
           ))}
         </tbody>
       </table>}
    </div>
  )
}
