import { useState, useCallback, useEffect } from "react"
import client from '../api/client'
import { errMsg } from '../utils/errors'
import { isTaskFailure } from '../utils/taskStatus'

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

const LEVEL_BADGE: Record<string, string> = {
  info: 'badge info',
  warning: 'badge hold',
  danger: 'badge danger',
  critical: 'badge critical',
}

const LEVEL_LABELS: Record<string, string> = {
  info: '提示',
  warning: '警告',
  danger: '危险',
  critical: '严重',
}

function LevelBadge({ level }: { level: string }) {
  return <span className={LEVEL_BADGE[level] || 'badge'}>{LEVEL_LABELS[level] || level}</span>
}

function WarningRow({ w, showStock = false }: { w: WarningItem; showStock?: boolean }) {
  return (
    <div className="rowline">
      <div>
        <div className="flex" style={{ gap: 10 }}>
          <LevelBadge level={w.level} />
          <strong className="small">
            {showStock && <>{w.stock_name || w.stock_code} · </>}{w.message}
          </strong>
        </div>
        <p className="caption" style={{ margin: '6px 0 0' }}>
          {w.category}{w.value ? ` · value ${w.value}` : ''}{w.created_at ? ` · ${new Date(w.created_at).toLocaleString('zh-CN')}` : ''}
        </p>
      </div>
    </div>
  )
}

export default function RiskWarning() {
  const [tab, setTab] = useState<Tab>('stock')
  return (
    <>
      <div className="tabs">
        <button className={`tab${tab === 'stock' ? ' active' : ''}`} onClick={() => setTab('stock')}>个股风险</button>
        <button className={`tab${tab === 'portfolio' ? ' active' : ''}`} onClick={() => setTab('portfolio')}>组合风险</button>
        <button className={`tab${tab === 'active' ? ' active' : ''}`} onClick={() => setTab('active')}>全市场预警</button>
      </div>
      {tab === 'stock' && <StockRiskView />}
      {tab === 'portfolio' && <PortfolioRiskView />}
      {tab === 'active' && <ActiveWarningsView />}
    </>
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
        if (isTaskFailure(d.status)) {
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
      setError(errMsg(err, '提交失败'))
      setLoading(false)
    }
  }

  const stockName = ai?.stock_name || warnings[0]?.stock_name || ''

  return (
    <div className="panel-stack">
      <section className="card">
        <div className="form-row" style={{ alignItems: 'flex-end' }}>
          <div className="field">
            <label>股票代码</label>
            <input className="input mono" type="text" placeholder="如 300750" value={code} onChange={(e) => setCode(e.target.value)} />
          </div>
          <div className="field" style={{ maxWidth: 180 }}>
            <label>分析天数</label>
            <input className="input mono" type="number" value={days} onChange={(e) => setDays(e.target.value)} />
          </div>
          <div className="field" style={{ flex: '0 0 auto', minWidth: 0 }}>
            <button className="btn btn-primary" onClick={submit} disabled={loading}>{loading ? '分析中...' : '开始分析'}</button>
          </div>
        </div>
        {error && <p className="small up mt8" style={{ margin: 0 }}>{error}</p>}
        {loading && (
          <div className="mt16">
            <p className="small fg2">进度: <span className="mono">{progress}%</span></p>
            <div className="progress mt8"><i style={{ width: `${progress}%` }} /></div>
          </div>
        )}
      </section>

      {ai && (
        <section className="card card-accent">
          <div className="between wrap">
            <h2 className="card-title" style={{ margin: 0 }}>AI 风险评估{stockName ? ` · ${stockName}` : ''} <span className="muted mono small">{code}</span></h2>
            {ai.risk_level && <span className={LEVEL_BADGE[ai.risk_level] || 'badge hold'}>{LEVEL_LABELS[ai.risk_level] || ai.risk_level}</span>}
          </div>
          {ai.risk_score != null && (
            <div className="flex mt16" style={{ gap: 20 }}>
              <div className="score-num mono">{ai.risk_score}<span className="muted" style={{ fontSize: 20 }}>/100</span></div>
              <div className="flex" style={{ flex: 1, flexDirection: 'column', alignItems: 'stretch', gap: 6 }}>
                <span className="caption">风险评分(越高越危险)</span>
                <div className="progress"><i style={{ width: `${Math.min(100, ai.risk_score)}%` }} /></div>
              </div>
            </div>
          )}
          <p className="small fg2 mt16">{ai.analysis}</p>
          {ai.advice && (
            <div className="reason-block mt16">
              <strong className="small">操作建议:</strong>
              <span className="small">{ai.advice}</span>
            </div>
          )}
          <p className="caption mt8">分析窗口:近 {days} 个交易日 · 不构成投资建议</p>
        </section>
      )}

      {warnings.length > 0 && (
        <section className="card">
          <h2 className="card-title-sm">触发的预警 ({warnings.length})</h2>
          {warnings.map((w) => <WarningRow key={w.id} w={w} />)}
        </section>
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
          if (isTaskFailure(t.data.data.status)) {
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
      await load()
    } catch (err: any) {
      setError(errMsg(err, '扫描失败'))
      setLoading(false)
    }
  }

  return (
    <div className="panel-stack">
      <div className="between wrap">
        <p className="fg2" style={{ margin: 0 }}>对全部持仓股票进行风险扫描（波动率 / RSI / 高位回落）。</p>
        <button className="btn btn-primary" onClick={scan} disabled={loading}>{loading ? '扫描中...' : '重新扫描'}</button>
      </div>

      {error && <p className="small up" style={{ margin: 0 }}>{error}</p>}

      {loading && (
        <section className="card">
          <p className="small fg2">进度: <span className="mono">{progress}%</span></p>
          <div className="progress mt8"><i style={{ width: `${progress}%` }} /></div>
        </section>
      )}

      {data && !loading && (
        <>
          <div className="grid2">
            <div className="kpi">
              <div className="k-label">预警总数</div>
              <div className="k-value mono">{data.total_warnings}</div>
              <div className="k-sub muted">覆盖全部持仓</div>
            </div>
            <div className="kpi">
              <div className="k-label">最高等级</div>
              <div className="mt8"><LevelBadge level={data.max_level} /></div>
              <div className="k-sub muted">{data.max_level === 'critical' || data.max_level === 'danger' ? '存在高危预警，请尽快处理' : '暂无危险及以上预警'}</div>
            </div>
          </div>

          <div className="grid4">
            {(['info', 'warning', 'danger', 'critical'] as const).map((lv) => (
              <div className="kpi" key={lv}>
                <div className="k-label"><LevelBadge level={lv} /></div>
                <div className="k-value mono">{(data.level_stats || {})[lv] ?? 0}</div>
              </div>
            ))}
          </div>

          {(data.warnings_detail || []).length > 0 ? (
            <section className="card">
              <h2 className="card-title-sm">预警明细</h2>
              {data.warnings_detail.map((w: WarningItem) => <WarningRow key={w.id} w={w} showStock />)}
            </section>
          ) : (
            <div className="empty">暂无风险预警，组合状态良好</div>
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
      .catch((err) => setError(errMsg(err, '加载失败')))
      .finally(() => setLoading(false))
  }, [])

  return (
    <div className="panel-stack">
      <p className="fg2" style={{ margin: 0 }}>全市场范围内的活跃风险预警（管理员可见）。</p>
      {loading ? <div className="empty">加载中...</div> :
       error ? <div className="empty">{error}</div> :
       items.length === 0 ? <div className="empty">暂无全市场活跃预警</div> : (
        <section className="card">
          <h2 className="card-title-sm">活跃预警</h2>
          {items.map((w) => <WarningRow key={w.id} w={w} showStock />)}
        </section>
      )}
    </div>
  )
}
