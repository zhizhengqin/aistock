import { useState, useCallback, useEffect } from 'react'
import client from '../api/client'
import { errMsg } from '../utils/errors'

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
  decision: {
    bull_sectors: { name: string; confidence: number; logic: string; risk: string }[]
    bear_sectors: { name: string; confidence: number; logic: string; risk: string }[]
    neutral_sectors: { name: string; confidence: number; logic: string; risk: string }[]
    operation_advice: string
    risk_triggers: string
    key_indicators: string[]
  }
  report_date: string
  market_snapshot: any
}

export default function Sector() {
  const [tab, setTab] = useState<Tab>('analysis')
  return (
    <div className="space-y-6">
      <div className="flex gap-2 border-b border-gray-200">
        {(['analysis', 'history'] as Tab[]).map((t) => (
          <button key={t} onClick={() => setTab(t)}
            className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors ${tab === t ? 'border-brand-600 text-brand-600' : 'border-transparent text-gray-500 hover:text-gray-700'}`}>
            {t === 'analysis' ? '板块分析' : '分析历史'}
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
        if (d.status === 'failed') { setError(d.error || '分析失败'); setLoading(false); return }
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

  // Load latest report on mount
  useEffect(() => {
    ;(async () => {
      try {
        const resp = await client.get('/stocks/sectors/reports/latest')
        if (resp.data.data) setReport(resp.data.data)
      } catch {}
    })()
  }, [])

  const d: any = report?.decision || {}

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-4">
        <p className="text-sm text-gray-500">4 位 AI 智能体协同分析行业板块多空趋势</p>
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
          {/* 4 Agent cards */}
          <div className="space-y-3">
            <h3 className="text-lg font-semibold">4 位 AI 智能体报告</h3>
            {Object.entries(report.agents || {}).map(([key, data]: [string, any]) => (
              <div key={key} className="bg-white rounded-lg shadow p-4">
                <div className="flex items-center justify-between mb-2">
                  <span className="font-medium">{AGENT_LABELS[key] || key}</span>
                  {data?.score != null && <span className="text-sm text-brand-600">评分: {data.score}</span>}
                </div>
                <div className="text-sm text-gray-600 space-y-1">
                  {data?.report && <p>{data.report}</p>}
                  {data?.assessment && <p>{data.assessment}</p>}
                  {data?.sectors && data.sectors.map((s: any, i: number) => (
                    <p key={i}>{s.name}: {s.health} / {s.trend}</p>
                  ))}
                  {data?.inflow_sectors && <p>资金流入: {data.inflow_sectors.join(', ')}</p>}
                  {data?.outflow_sectors && <p>资金流出: {data.outflow_sectors.join(', ')}</p>}
                  {data?.error && <p className="text-red-400">分析失败: {data.error}</p>}
                </div>
                <details className="mt-2">
                  <summary className="text-xs text-brand-600 cursor-pointer">展开完整报告</summary>
                  <pre className="mt-2 text-xs text-gray-500 overflow-x-auto bg-gray-50 p-3 rounded">{JSON.stringify(data, null, 2)}</pre>
                </details>
              </div>
            ))}
          </div>

          {/* Multi-direction prediction */}
          <div className="bg-white rounded-lg shadow p-6 border-l-4 border-brand-500">
            <h3 className="text-lg font-semibold mb-4">板块多空预测 · {report.report_date}</h3>
            <div className="grid grid-cols-3 gap-4">
              <BullCard sectors={d.bull_sectors} label="看多" color="red" />
              <NeutralCard sectors={d.neutral_sectors} label="中性" color="gray" />
              <BearCard sectors={d.bear_sectors} label="看空" color="green" />
            </div>
            <div className="mt-4 grid grid-cols-2 gap-4 text-sm">
              <div><span className="text-gray-400">操作节奏建议</span><p className="mt-1">{d.operation_advice}</p></div>
              <div><span className="text-gray-400">风险触发条件</span><p className="mt-1 text-red-500">{d.risk_triggers}</p></div>
            </div>
            {d.key_indicators && d.key_indicators.length > 0 && (
              <div className="mt-3">
                <span className="text-gray-400 text-sm">核心跟踪指标:</span>
                <ul className="mt-1 text-sm text-gray-600 list-disc list-inside">
                  {d.key_indicators.map((k: string, i: number) => <li key={i}>{k}</li>)}
                </ul>
              </div>
            )}
          </div>
        </>
      )}
    </div>
  )
}

function BullCard({ sectors, label, color }: { sectors: any[]; label: string; color: string }) {
  const textCls = { red: 'text-red-600', green: 'text-green-600', gray: 'text-gray-600' }[color]
  const bgCls = { red: 'bg-red-50', green: 'bg-green-50', gray: 'bg-gray-50' }[color]
  return (
    <div className={`rounded-lg p-4 ${bgCls}`}>
      <p className={`font-medium mb-2 ${textCls}`}>{label}板块</p>
      {sectors?.map((s, i) => (
        <div key={i} className="mb-2 text-sm">
          <span className="font-medium">{s.name}</span> <span className="text-xs text-gray-400">置信度 {s.confidence}/10</span>
          <p className="text-gray-600">{s.logic}</p>
        </div>
      )) || <p className="text-sm text-gray-400">暂无</p>}
    </div>
  )
}

const NeutralCard = BullCard
const BearCard = BullCard

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
    <div className="bg-white rounded-lg shadow overflow-hidden">
      {loading ? <p className="p-8 text-center text-gray-400">加载中...</p> :
       items.length === 0 ? <p className="p-8 text-center text-gray-400">暂无板块分析历史</p> :
       <table className="w-full text-sm">
         <thead className="bg-gray-50"><tr><th className="px-4 py-2 text-left">报告日期</th></tr></thead>
         <tbody>
           {items.map((item) => (
             <tr key={item.id} className="border-t border-gray-100 hover:bg-gray-50">
               <td className="px-4 py-2">{item.report_date}</td>
             </tr>
           ))}
         </tbody>
       </table>}
    </div>
  )
}
