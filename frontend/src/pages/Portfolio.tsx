import { useState, useCallback, useEffect } from "react"
import client from '../api/client'
import { errMsg } from '../utils/errors'

type Tab = 'holdings' | 'diagnosis' | 'history'

interface Holding {
  id: number
  stock_code: string
  stock_name: string
  shares: number
  cost_price: number
  auto_monitor: boolean
  current_price: number | null
  market_value: number
  profit_loss: number
  profit_pct: number
  industry: string
}

interface Summary {
  total_stocks: number
  total_cost: number
  total_market_value: number
  total_profit_loss: number
  total_profit_pct: number
  monitoring_count: number
}

interface Diagnosis {
  health_score: number
  risk_assessment: string
  asset_allocation: string
  risk_exposure: string
  strategy_consistency: string
  suggestions: string[]
  summary: string
}

const fmtMoney = (v: number) => v.toLocaleString('zh-CN', { maximumFractionDigits: 2 })

function healthColor(score: number) {
  if (score >= 80) return 'text-green-600'
  if (score >= 60) return 'text-yellow-600'
  if (score >= 40) return 'text-orange-600'
  return 'text-red-600'
}

export default function Portfolio() {
  const [tab, setTab] = useState<Tab>('holdings')
  const labels: Record<Tab, string> = { holdings: '持仓管理', diagnosis: 'AI 诊断', history: '历史报告' }
  return (
    <div className="space-y-6">
      <div className="flex gap-2 border-b border-gray-200">
        {(['holdings', 'diagnosis', 'history'] as Tab[]).map((t) => (
          <button key={t} onClick={() => setTab(t)}
            className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors ${tab === t ? 'border-brand-600 text-brand-600' : 'border-transparent text-gray-500 hover:text-gray-700'}`}>
            {labels[t]}
          </button>
        ))}
      </div>
      {tab === 'holdings' && <HoldingsView />}
      {tab === 'diagnosis' && <DiagnosisView />}
      {tab === 'history' && <HistoryView />}
    </div>
  )
}

function HoldingsView() {
  const [stocks, setStocks] = useState<Holding[]>([])
  const [summary, setSummary] = useState<Summary | null>(null)
  const [error, setError] = useState('')
  const [form, setForm] = useState({ stock_code: '', stock_name: '', shares: '', cost_price: '', auto_monitor: false })
  const [adding, setAdding] = useState(false)

  const load = useCallback(async () => {
    try {
      const [s1, s2] = await Promise.all([
        client.get('/stocks/portfolio/stocks'),
        client.get('/stocks/portfolio/summary'),
      ])
      setStocks(s1.data.data)
      setSummary(s2.data.data)
    } catch {
      setError('加载持仓失败')
    }
  }, [])

  useEffect(() => { load() }, [load])

  const add = async () => {
    setError('')
    if (!form.stock_code || !form.shares || !form.cost_price) {
      setError('请填写股票代码、持股数量和成本价')
      return
    }
    setAdding(true)
    try {
      await client.post('/stocks/portfolio/stocks', {
        stock_code: form.stock_code.trim(),
        stock_name: form.stock_name.trim(),
        shares: parseInt(form.shares, 10),
        cost_price: parseFloat(form.cost_price),
        auto_monitor: form.auto_monitor,
      })
      setForm({ stock_code: '', stock_name: '', shares: '', cost_price: '', auto_monitor: false })
      await load()
    } catch (err: any) {
      setError(errMsg(err, '添加失败'))
    } finally {
      setAdding(false)
    }
  }

  const toggleMonitor = async (s: Holding) => {
    try {
      await client.put(`/stocks/portfolio/stocks/${s.id}`, { auto_monitor: !s.auto_monitor })
      await load()
    } catch {
      setError('更新失败')
    }
  }

  const remove = async (s: Holding) => {
    try {
      await client.delete(`/stocks/portfolio/stocks/${s.id}`)
      await load()
    } catch {
      setError('删除失败')
    }
  }

  const plColor = (v: number) => v > 0 ? 'text-red-600' : v < 0 ? 'text-green-600' : 'text-gray-500'

  return (
    <div className="space-y-4">
      {summary && (
        <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
          <div className="bg-white rounded-lg shadow p-4"><p className="text-xs text-gray-400">持仓股票</p><p className="text-xl font-bold">{summary.total_stocks}</p></div>
          <div className="bg-white rounded-lg shadow p-4"><p className="text-xs text-gray-400">总成本</p><p className="text-xl font-bold">{fmtMoney(summary.total_cost)}</p></div>
          <div className="bg-white rounded-lg shadow p-4"><p className="text-xs text-gray-400">总市值</p><p className="text-xl font-bold">{fmtMoney(summary.total_market_value)}</p></div>
          <div className="bg-white rounded-lg shadow p-4"><p className="text-xs text-gray-400">总盈亏</p><p className={`text-xl font-bold ${plColor(summary.total_profit_loss)}`}>{fmtMoney(summary.total_profit_loss)} ({summary.total_profit_pct}%)</p></div>
          <div className="bg-white rounded-lg shadow p-4"><p className="text-xs text-gray-400">自动监测</p><p className="text-xl font-bold text-brand-600">{summary.monitoring_count}</p></div>
        </div>
      )}

      <div className="bg-white rounded-lg shadow p-4">
        <h3 className="text-sm font-semibold mb-3">添加持仓</h3>
        <div className="flex flex-wrap items-center gap-2">
          <input placeholder="股票代码 如 600519" value={form.stock_code}
            onChange={(e) => setForm({ ...form, stock_code: e.target.value })}
            className="border border-gray-300 rounded px-3 py-1.5 text-sm w-40" />
          <input placeholder="股票名称(可选)" value={form.stock_name}
            onChange={(e) => setForm({ ...form, stock_name: e.target.value })}
            className="border border-gray-300 rounded px-3 py-1.5 text-sm w-32" />
          <input placeholder="持股数量" type="number" value={form.shares}
            onChange={(e) => setForm({ ...form, shares: e.target.value })}
            className="border border-gray-300 rounded px-3 py-1.5 text-sm w-28" />
          <input placeholder="成本价" type="number" step="0.01" value={form.cost_price}
            onChange={(e) => setForm({ ...form, cost_price: e.target.value })}
            className="border border-gray-300 rounded px-3 py-1.5 text-sm w-28" />
          <label className="flex items-center gap-1 text-sm text-gray-600">
            <input type="checkbox" checked={form.auto_monitor}
              onChange={(e) => setForm({ ...form, auto_monitor: e.target.checked })} />
            自动监测
          </label>
          <button onClick={add} disabled={adding}
            className="px-4 py-1.5 bg-brand-600 text-white rounded text-sm font-medium hover:bg-brand-700 disabled:opacity-50">
            {adding ? '添加中...' : '添加'}
          </button>
        </div>
        {error && <p className="text-sm text-red-500 mt-2">{error}</p>}
      </div>

      <div className="bg-white rounded-lg shadow p-4">
        <h3 className="text-sm font-semibold mb-3">我的持仓</h3>
        {stocks.length === 0 ? (
          <p className="text-sm text-gray-400">暂无持仓，请先添加股票</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-gray-50"><tr>
                <th className="px-3 py-2 text-left">股票</th>
                <th className="px-3 py-2 text-right">持股</th>
                <th className="px-3 py-2 text-right">成本价</th>
                <th className="px-3 py-2 text-right">现价</th>
                <th className="px-3 py-2 text-right">市值</th>
                <th className="px-3 py-2 text-right">盈亏</th>
                <th className="px-3 py-2 text-center">自动监测</th>
                <th className="px-3 py-2 text-center">操作</th>
              </tr></thead>
              <tbody>
                {stocks.map((s) => (
                  <tr key={s.id} className="border-t border-gray-100">
                    <td className="px-3 py-2 font-medium">{s.stock_name || '-'} <span className="text-gray-400 font-mono text-xs">{s.stock_code}</span></td>
                    <td className="px-3 py-2 text-right">{s.shares.toLocaleString()}</td>
                    <td className="px-3 py-2 text-right">{s.cost_price.toFixed(2)}</td>
                    <td className="px-3 py-2 text-right">{s.current_price != null ? s.current_price.toFixed(2) : '-'}</td>
                    <td className="px-3 py-2 text-right">{fmtMoney(s.market_value)}</td>
                    <td className={`px-3 py-2 text-right font-medium ${plColor(s.profit_loss)}`}>{fmtMoney(s.profit_loss)} ({s.profit_pct}%)</td>
                    <td className="px-3 py-2 text-center">
                      <button onClick={() => toggleMonitor(s)}
                        className={`px-2 py-0.5 rounded text-xs ${s.auto_monitor ? 'bg-brand-50 text-brand-700' : 'bg-gray-100 text-gray-500'}`}>
                        {s.auto_monitor ? '已开启' : '已关闭'}
                      </button>
                    </td>
                    <td className="px-3 py-2 text-center">
                      <button onClick={() => remove(s)} className="text-xs text-red-500 hover:underline">删除</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  )
}

function DiagnosisView() {
  const [loading, setLoading] = useState(false)
  const [progress, setProgress] = useState(0)
  const [diag, setDiag] = useState<Diagnosis | null>(null)
  const [error, setError] = useState('')

  const pollTask = useCallback(async (taskId: number) => {
    for (let i = 0; i < 100; i++) {
      await new Promise((r) => setTimeout(r, 2000))
      try {
        const resp = await client.get(`/tasks/${taskId}`)
        const d = resp.data.data
        setProgress(d.progress)
        if (d.status === 'success' && d.result?.report_id) {
          const r2 = await client.get(`/stocks/portfolio/reports/${d.result.report_id}`)
          setDiag(r2.data.data)
          setLoading(false)
          return
        }
        if (d.status === 'failed') {
          setError(d.error || '诊断失败')
          setLoading(false)
          return
        }
      } catch {}
    }
    setError('轮询超时')
    setLoading(false)
  }, [])

  const submit = async () => {
    setError(''); setDiag(null); setLoading(true); setProgress(0)
    try {
      const resp = await client.post('/stocks/portfolio/analyze')
      pollTask(resp.data.data.task_id)
    } catch (err: any) {
      setError(errMsg(err, '提交失败'))
      setLoading(false)
    }
  }

  const sections: [string, keyof Diagnosis][] = [
    ['风险评估', 'risk_assessment'],
    ['资产配置', 'asset_allocation'],
    ['风险敞口', 'risk_exposure'],
    ['策略一致性', 'strategy_consistency'],
  ]

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-4">
        <p className="text-sm text-gray-500">AI 对当前持仓组合进行整体健康度诊断</p>
        <button onClick={submit} disabled={loading}
          className="px-6 py-2 bg-brand-600 text-white rounded-lg font-medium hover:bg-brand-700 disabled:opacity-50">
          {loading ? '诊断中...' : '开始诊断'}
        </button>
      </div>

      {error && <p className="text-sm text-red-500">{error}</p>}

      {loading && (
        <div className="bg-white rounded-lg shadow p-6">
          <p className="text-sm text-gray-600 mb-2">进度: {progress}%</p>
          <div className="w-full bg-gray-200 rounded-full h-2">
            <div className="bg-brand-600 h-2 rounded-full transition-all" style={{ width: `${progress}%` }} />
          </div>
        </div>
      )}

      {diag && (
        <>
          <div className="bg-white rounded-lg shadow p-6 flex items-center gap-6">
            <div className="text-center">
              <p className={`text-4xl font-bold ${healthColor(diag.health_score)}`}>{diag.health_score}</p>
              <p className="text-xs text-gray-400 mt-1">组合健康分</p>
            </div>
            <p className="text-sm text-gray-600 flex-1">{diag.summary}</p>
          </div>

          <div className="grid md:grid-cols-2 gap-4">
            {sections.map(([label, key]) => (
              <div key={key} className="bg-white rounded-lg shadow p-4">
                <h4 className="text-sm font-semibold mb-2">{label}</h4>
                <p className="text-sm text-gray-600">{diag[key] as string}</p>
              </div>
            ))}
          </div>

          {diag.suggestions && diag.suggestions.length > 0 && (
            <div className="bg-white rounded-lg shadow p-6 border-l-4 border-brand-500">
              <h4 className="text-sm font-semibold mb-3">调仓建议</h4>
              <ul className="space-y-2">
                {diag.suggestions.map((s, i) => (
                  <li key={i} className="text-sm text-gray-600">{s}</li>
                ))}
              </ul>
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
  const [detail, setDetail] = useState<Diagnosis | null>(null)

  useEffect(() => {
    client.get('/stocks/portfolio/history')
      .then((r) => setItems(r.data.data.items))
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [])

  const openDetail = async (id: number) => {
    try {
      const r = await client.get(`/stocks/portfolio/reports/${id}`)
      setDetail(r.data.data)
    } catch {}
  }

  if (loading) return <p className="text-sm text-gray-400">加载中...</p>

  return (
    <div className="space-y-4">
      {items.length === 0 ? (
        <p className="text-sm text-gray-400">暂无历史诊断报告</p>
      ) : (
        <div className="bg-white rounded-lg shadow divide-y divide-gray-100">
          {items.map((r) => (
            <button key={r.id} onClick={() => openDetail(r.id)}
              className="w-full flex items-center justify-between px-4 py-3 hover:bg-gray-50 text-left">
              <span className="text-sm text-gray-600">{r.created_at ? new Date(r.created_at).toLocaleString('zh-CN') : '-'}</span>
              <span className={`text-sm font-bold ${healthColor(r.health_score)}`}>健康分 {r.health_score}</span>
            </button>
          ))}
        </div>
      )}

      {detail && (
        <div className="bg-white rounded-lg shadow p-6 space-y-3">
          <div className="flex items-center justify-between">
            <h4 className="text-sm font-semibold">报告详情</h4>
            <button onClick={() => setDetail(null)} className="text-xs text-gray-400 hover:text-gray-600">关闭</button>
          </div>
          <p className={`text-3xl font-bold ${healthColor(detail.health_score)}`}>{detail.health_score} 分</p>
          <p className="text-sm text-gray-600">{detail.summary}</p>
          <div className="grid md:grid-cols-2 gap-3 text-sm">
            <div className="bg-gray-50 rounded p-3"><p className="font-medium mb-1">风险评估</p><p className="text-gray-600">{detail.risk_assessment}</p></div>
            <div className="bg-gray-50 rounded p-3"><p className="font-medium mb-1">资产配置</p><p className="text-gray-600">{detail.asset_allocation}</p></div>
            <div className="bg-gray-50 rounded p-3"><p className="font-medium mb-1">风险敞口</p><p className="text-gray-600">{detail.risk_exposure}</p></div>
            <div className="bg-gray-50 rounded p-3"><p className="font-medium mb-1">策略一致性</p><p className="text-gray-600">{detail.strategy_consistency}</p></div>
          </div>
          {detail.suggestions && (
            <ul className="space-y-1 text-sm text-gray-600">
              {detail.suggestions.map((s, i) => <li key={i}>{s}</li>)}
            </ul>
          )}
        </div>
      )}
    </div>
  )
}
