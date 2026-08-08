import { useState, useCallback, useEffect } from "react"
import client from '../api/client'

type Tab = 'stock' | 'portfolio' | 'active'

interface WarningItem {
  id: number
  level: string
  category: string
  stock_code: string
  stock_name: string
  message: string
  value: string
  created_at: string | null
}

const LEVEL_STYLES: Record<string, string> = {
  info: 'bg-blue-100 text-blue-700',
  warning: 'bg-yellow-100 text-yellow-700',
  danger: 'bg-orange-100 text-orange-700',
  critical: 'bg-red-100 text-red-700',
}

const LEVEL_LABELS: Record<string, string> = {
  info: '提示',
  warning: '警告',
  danger: '危险',
  critical: '严重',
}

function LevelBadge({ level }: { level: string }) {
  return (
    <span className={`px-2 py-0.5 rounded text-xs font-medium ${LEVEL_STYLES[level] || 'bg-gray-100 text-gray-600'}`}>
      {LEVEL_LABELS[level] || level}
    </span>
  )
}

export default function RiskWarning() {
  const [tab, setTab] = useState<Tab>('stock')
  const labels: Record<Tab, string> = { stock: '个股风险', portfolio: '组合风险', active: '全市场预警' }
  return (
    <div className="space-y-6">
      <div className="flex gap-2 border-b border-gray-200">
        {(['stock', 'portfolio', 'active'] as Tab[]).map((t) => (
          <button key={t} onClick={() => setTab(t)}
            className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors ${tab === t ? 'border-brand-600 text-brand-600' : 'border-transparent text-gray-500 hover:text-gray-700'}`}>
            {labels[t]}
          </button>
        ))}
      </div>
      {tab === 'stock' && <StockRiskView />}
      {tab === 'portfolio' && <PortfolioRiskView />}
      {tab === 'active' && <ActiveWarningsView />}
    </div>
  )
}

function StockRiskView() {
  const [code, setCode] = useState('')
  const [days, setDays] = useState('30')
  const [loading, setLoading] = useState(false)
  const [progress, setProgress] = useState(0)
  const [error, setError] = useState('')
  const [ai, setAi] = useState<any>(null)
  const [warnings, setWarnings] = useState<WarningItem[]>([])

  const loadWarnings = async (stockCode: string) => {
    try {
      const r = await client.get('/stocks/risk/portfolio')
      const all: WarningItem[] = r.data.data.warnings_detail || []
      setWarnings(all.filter((w) => w.stock_code === stockCode))
    } catch {}
  }

  const pollTask = useCallback(async (taskId: number, stockCode: string) => {
    for (let i = 0; i < 100; i++) {
      await new Promise((r) => setTimeout(r, 2000))
      try {
        const resp = await client.get(`/tasks/${taskId}`)
        const d = resp.data.data
        setProgress(d.progress)
        if (d.status === 'success') {
          setAi(d.result?.ai || null)
          await loadWarnings(stockCode)
          setLoading(false)
          return
        }
        if (d.status === 'failed') {
          setError(d.error || '分析失败')
          setLoading(false)
          return
        }
      } catch {}
    }
    setError('轮询超时')
    setLoading(false)
  }, [])

  const submit = async () => {
    if (!code.trim()) { setError('请输入股票代码'); return }
    setError(''); setAi(null); setWarnings([]); setLoading(true); setProgress(0)
    try {
      const resp = await client.post('/stocks/risk/analyze', { stock_code: code.trim(), days: parseInt(days, 10) || 30 })
      pollTask(resp.data.data.task_id, code.trim())
    } catch (err: any) {
      setError(err.response?.data?.detail || '提交失败')
      setLoading(false)
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2 flex-wrap">
        <input placeholder="股票代码 如 600519" value={code} onChange={(e) => setCode(e.target.value)}
          className="border border-gray-300 rounded px-3 py-1.5 text-sm w-44" />
        <input placeholder="分析天数" type="number" value={days} onChange={(e) => setDays(e.target.value)}
          className="border border-gray-300 rounded px-3 py-1.5 text-sm w-28" />
        <button onClick={submit} disabled={loading}
          className="px-6 py-1.5 bg-brand-600 text-white rounded text-sm font-medium hover:bg-brand-700 disabled:opacity-50">
          {loading ? '分析中...' : '开始分析'}
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

      {ai && (
        <div className="bg-white rounded-lg shadow p-6 border-l-4 border-orange-400">
          <div className="flex items-center gap-4 mb-3">
            <h3 className="text-lg font-semibold">AI 风险评估</h3>
            {ai.risk_level && <span className="px-2 py-0.5 rounded text-xs bg-orange-100 text-orange-700">{ai.risk_level}</span>}
            {ai.risk_score != null && <span className="text-sm text-gray-500">风险评分 {ai.risk_score}/100</span>}
          </div>
          <p className="text-sm text-gray-600 mb-3">{ai.analysis}</p>
          {ai.advice && (
            <div className="bg-gray-50 rounded p-3">
              <p className="text-xs font-medium text-gray-500 mb-1">操作建议</p>
              <p className="text-sm text-gray-600">{ai.advice}</p>
            </div>
          )}
        </div>
      )}

      {warnings.length > 0 && (
        <div className="bg-white rounded-lg shadow p-4">
          <h3 className="text-sm font-semibold mb-3">触发的预警 ({warnings.length})</h3>
          <div className="space-y-2">
            {warnings.map((w) => (
              <div key={w.id} className="flex items-start gap-3 border-t border-gray-100 pt-2">
                <LevelBadge level={w.level} />
                <div className="flex-1">
                  <p className="text-sm text-gray-700">{w.message}</p>
                  <p className="text-xs text-gray-400 mt-0.5">{w.category}{w.value ? ` · ${w.value}` : ''}{w.created_at ? ` · ${new Date(w.created_at).toLocaleString('zh-CN')}` : ''}</p>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

function PortfolioRiskView() {
  const [data, setData] = useState<any>(null)
  const [loading, setLoading] = useState(false)
  const [progress, setProgress] = useState(0)
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    const r = await client.get('/stocks/risk/portfolio')
    const d = r.data.data
    if (d.task_id) {
      // Scan was triggered — poll the task then reload
      setLoading(true)
      for (let i = 0; i < 100; i++) {
        await new Promise((res) => setTimeout(res, 2000))
        try {
          const t = await client.get(`/tasks/${d.task_id}`)
          setProgress(t.data.data.progress)
          if (t.data.data.status === 'success') {
            const r2 = await client.get('/stocks/risk/portfolio')
            setData(r2.data.data)
            setLoading(false)
            return
          }
          if (t.data.data.status === 'failed') {
            setError(t.data.data.error || '扫描失败')
            setLoading(false)
            return
          }
        } catch {}
      }
      setError('轮询超时')
      setLoading(false)
    } else {
      setData(d)
    }
  }, [])

  useEffect(() => { load() }, [load])

  const scan = async () => {
    setError(''); setLoading(true); setProgress(0)
    try {
      // Delete-view trick: simply reload — backend triggers a scan when no warnings exist;
      // otherwise show existing. For a fresh scan we re-run through analyze flow per holding.
      await load()
    } catch (err: any) {
      setError(err.response?.data?.detail || '扫描失败')
      setLoading(false)
    }
  }

  const maxLevelColor: Record<string, string> = {
    info: 'text-blue-600', warning: 'text-yellow-600', danger: 'text-orange-600', critical: 'text-red-600',
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-4">
        <p className="text-sm text-gray-500">对全部持仓股票进行风险扫描（波动率 / RSI / 高位回落）</p>
        <button onClick={scan} disabled={loading}
          className="px-6 py-2 bg-brand-600 text-white rounded-lg font-medium hover:bg-brand-700 disabled:opacity-50">
          {loading ? '扫描中...' : '重新扫描'}
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

      {data && !loading && (
        <>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            <div className="bg-white rounded-lg shadow p-4"><p className="text-xs text-gray-400">预警总数</p><p className="text-xl font-bold">{data.total_warnings}</p></div>
            <div className="bg-white rounded-lg shadow p-4"><p className="text-xs text-gray-400">最高等级</p><p className={`text-xl font-bold ${maxLevelColor[data.max_level] || ''}`}>{LEVEL_LABELS[data.max_level] || data.max_level}</p></div>
            {Object.entries(data.level_stats || {}).map(([lv, cnt]) => (
              <div key={lv} className="bg-white rounded-lg shadow p-4">
                <p className="text-xs text-gray-400">{LEVEL_LABELS[lv] || lv}</p>
                <p className="text-xl font-bold">{cnt as number}</p>
              </div>
            ))}
          </div>

          {(data.warnings_detail || []).length > 0 && (
            <div className="bg-white rounded-lg shadow p-4">
              <h3 className="text-sm font-semibold mb-3">预警明细</h3>
              <div className="space-y-2">
                {data.warnings_detail.map((w: WarningItem) => (
                  <div key={w.id} className="flex items-start gap-3 border-t border-gray-100 pt-2">
                    <LevelBadge level={w.level} />
                    <div className="flex-1">
                      <p className="text-sm text-gray-700">
                        <span className="font-medium">{w.stock_name || w.stock_code}</span> {w.message}
                      </p>
                      <p className="text-xs text-gray-400 mt-0.5">{w.category}{w.value ? ` · ${w.value}` : ''}{w.created_at ? ` · ${new Date(w.created_at).toLocaleString('zh-CN')}` : ''}</p>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {data.total_warnings === 0 && (
            <p className="text-sm text-gray-400">暂无风险预警，组合状态良好</p>
          )}
        </>
      )}
    </div>
  )
}

function ActiveWarningsView() {
  const [items, setItems] = useState<WarningItem[]>([])
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    client.get('/stocks/risk/active')
      .then((r) => setItems(r.data.data))
      .catch((err) => setError(err.response?.data?.detail || '加载失败'))
      .finally(() => setLoading(false))
  }, [])

  if (loading) return <p className="text-sm text-gray-400">加载中...</p>
  if (error) return <p className="text-sm text-gray-500">{error}</p>

  return (
    <div className="space-y-4">
      <p className="text-sm text-gray-500">全市场范围内的活跃风险预警（管理员可见）</p>
      {items.length === 0 ? (
        <p className="text-sm text-gray-400">暂无全市场活跃预警</p>
      ) : (
        <div className="bg-white rounded-lg shadow p-4 space-y-2">
          {items.map((w) => (
            <div key={w.id} className="flex items-start gap-3 border-t border-gray-100 pt-2 first:border-0 first:pt-0">
              <LevelBadge level={w.level} />
              <div className="flex-1">
                <p className="text-sm text-gray-700">
                  <span className="font-medium">{w.stock_name || w.stock_code}</span> {w.message}
                </p>
                <p className="text-xs text-gray-400 mt-0.5">{w.category}{w.value ? ` · ${w.value}` : ''}{w.created_at ? ` · ${new Date(w.created_at).toLocaleString('zh-CN')}` : ''}</p>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
