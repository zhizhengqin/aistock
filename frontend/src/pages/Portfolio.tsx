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

// A 股配色: 上涨/健康=红(up), 下跌/风险=绿(down), 中性=hold
function healthVar(score: number) {
  if (score >= 80) return 'var(--up)'
  if (score >= 60) return 'var(--hold)'
  return 'var(--down)'
}

function plCls(v: number) { return v > 0 ? 'up' : v < 0 ? 'down' : '' }
function plText(v: number) { return (v > 0 ? '+' : '') + fmtMoney(v) }

export default function Portfolio() {
  const [tab, setTab] = useState<Tab>('holdings')
  return (
    <>
      <div className="tabs">
        <button className={`tab${tab === 'holdings' ? ' active' : ''}`} onClick={() => setTab('holdings')}>持仓管理</button>
        <button className={`tab${tab === 'diagnosis' ? ' active' : ''}`} onClick={() => setTab('diagnosis')}>AI 诊断</button>
        <button className={`tab${tab === 'history' ? ' active' : ''}`} onClick={() => setTab('history')}>历史报告</button>
      </div>
      {tab === 'holdings' && <HoldingsView />}
      {tab === 'diagnosis' && <DiagnosisView />}
      {tab === 'history' && <HistoryView />}
    </>
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

  return (
    <div className="panel-stack">
      {summary && (
        <div className="kpi-grid" style={{ gridTemplateColumns: 'repeat(auto-fit,minmax(180px,1fr))' }}>
          <div className="kpi">
            <div className="k-label">持仓股票</div>
            <div className="k-value mono">{summary.total_stocks}</div>
            <div className="k-sub muted">只</div>
          </div>
          <div className="kpi">
            <div className="k-label">总成本</div>
            <div className="k-value mono">¥{fmtMoney(summary.total_cost)}</div>
            <div className="k-sub muted">人民币</div>
          </div>
          <div className="kpi">
            <div className="k-label">总市值</div>
            <div className="k-value mono">¥{fmtMoney(summary.total_market_value)}</div>
            <div className="k-sub muted">人民币</div>
          </div>
          <div className="kpi">
            <div className="k-label">总盈亏</div>
            <div className={`k-value mono ${plCls(summary.total_profit_loss)}`}>{plText(summary.total_profit_loss)}</div>
            <div className={`k-sub mono ${plCls(summary.total_profit_loss)}`}>{summary.total_profit_pct > 0 ? '+' : ''}{summary.total_profit_pct}%</div>
          </div>
          <div className="kpi">
            <div className="k-label">自动监测</div>
            <div className="k-value mono">{summary.monitoring_count}</div>
            <div className="k-sub muted">只已开启</div>
          </div>
        </div>
      )}

      <section className="card">
        <h2 className="card-title-sm">添加持仓</h2>
        <div className="form-row">
          <div className="field">
            <label>股票代码</label>
            <input className="input mono" type="text" placeholder="如 600519" value={form.stock_code}
              onChange={(e) => setForm({ ...form, stock_code: e.target.value })} />
          </div>
          <div className="field">
            <label>股票名称(可选)</label>
            <input className="input" type="text" placeholder="自动识别" value={form.stock_name}
              onChange={(e) => setForm({ ...form, stock_name: e.target.value })} />
          </div>
          <div className="field">
            <label>持股数量</label>
            <input className="input mono" type="number" placeholder="如 200" value={form.shares}
              onChange={(e) => setForm({ ...form, shares: e.target.value })} />
          </div>
          <div className="field">
            <label>成本价</label>
            <input className="input mono" type="number" step="0.01" placeholder="如 1420.00" value={form.cost_price}
              onChange={(e) => setForm({ ...form, cost_price: e.target.value })} />
          </div>
        </div>
        <div className="between wrap mt16">
          <label className="flex" style={{ gap: 8, fontSize: 14 }}>
            <input type="checkbox" checked={form.auto_monitor}
              onChange={(e) => setForm({ ...form, auto_monitor: e.target.checked })} />
            自动监测(开启后按实时监测配置检查触发条件)
          </label>
          <button className="btn btn-primary" onClick={add} disabled={adding}>{adding ? '添加中...' : '添加'}</button>
        </div>
        {error && <p className="small up mt8" style={{ margin: 0 }}>{error}</p>}
      </section>

      <section className="card">
        <h2 className="card-title-sm">我的持仓</h2>
        {stocks.length === 0 ? (
          <div className="empty">暂无持仓，请先添加股票</div>
        ) : (
          <table className="table">
            <thead>
              <tr>
                <th>股票</th>
                <th className="num">持股</th>
                <th className="num">成本价</th>
                <th className="num">现价</th>
                <th className="num">市值</th>
                <th className="num">盈亏</th>
                <th>自动监测</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {stocks.map((s) => (
                <tr key={s.id}>
                  <td>{s.stock_name || '-'} <span className="muted mono">{s.stock_code}</span></td>
                  <td className="num mono">{s.shares.toLocaleString()}</td>
                  <td className="num mono">{s.cost_price.toFixed(2)}</td>
                  <td className="num mono">{s.current_price != null ? s.current_price.toFixed(2) : '-'}</td>
                  <td className="num mono">{fmtMoney(s.market_value)}</td>
                  <td className={`num mono ${plCls(s.profit_loss)}`}>{plText(s.profit_loss)} ({s.profit_pct > 0 ? '+' : ''}{s.profit_pct}%)</td>
                  <td>
                    <button className={`pill${s.auto_monitor ? ' active' : ''}`} onClick={() => toggleMonitor(s)}>
                      {s.auto_monitor ? '已开启' : '已关闭'}
                    </button>
                  </td>
                  <td><button className="btn-text" onClick={() => remove(s)}>删除</button></td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
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
      const resp = await client.post('/stocks/portfolio/diagnose')
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
    <div className="panel-stack">
      <div className="between wrap">
        <p className="fg2" style={{ margin: 0 }}>AI 对当前持仓组合进行整体健康度诊断，覆盖风险、配置、敞口与策略一致性四个维度。</p>
        <button className="btn btn-primary" onClick={submit} disabled={loading}>{loading ? '诊断中...' : '开始诊断'}</button>
      </div>

      {error && <p className="small up" style={{ margin: 0 }}>{error}</p>}

      {loading && (
        <section className="card">
          <p className="small fg2">进度: <span className="mono">{progress}%</span></p>
          <div className="progress mt8"><i style={{ width: `${progress}%` }} /></div>
        </section>
      )}

      {diag && (
        <>
          <section className="card score-hero">
            <div className="score-num mono" style={{ color: healthVar(diag.health_score) }}>{diag.health_score}</div>
            <div className="fg2 mt8" style={{ font: '600 18px/1.2 var(--font-body)' }}>组合健康分</div>
            <p className="caption mt8">满分 100 · 60-80 为中性区间，建议关注调仓建议</p>
          </section>

          {diag.summary && (
            <section className="card">
              <p className="fg2" style={{ margin: 0 }}>{diag.summary}</p>
            </section>
          )}

          <div className="grid2">
            {sections.map(([label, key]) => (
              <section className="card" key={key}>
                <h3 className="card-title-sm">{label}</h3>
                <p className="small fg2" style={{ margin: 0 }}>{diag[key] as string}</p>
              </section>
            ))}
          </div>

          {diag.suggestions && diag.suggestions.length > 0 && (
            <section className="card card-accent">
              <h3 className="card-title-sm">调仓建议</h3>
              {diag.suggestions.map((s, i) => (
                <div className="rowline" key={i}>
                  <span className="small">{s}</span>
                  <span className="badge hold">建议</span>
                </div>
              ))}
              <p className="caption mt8">以上为 AI 诊断结果，不构成投资建议</p>
            </section>
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

  if (loading) return <div className="empty">加载中...</div>

  return (
    <div className="panel-stack">
      <section className="card">
        <h2 className="card-title-sm">诊断历史</h2>
        {items.length === 0 ? (
          <div className="empty">暂无诊断报告，点击「AI 诊断」页生成第一份组合健康报告</div>
        ) : (
          items.map((r) => (
            <div className="rowline" key={r.id}>
              <span className="mono small">{r.created_at ? new Date(r.created_at).toLocaleString('zh-CN') : '-'}</span>
              <span className="flex" style={{ gap: 12 }}>
                <span className={`badge ${r.health_score >= 80 ? 'up' : 'hold'}`}>健康分 {r.health_score}</span>
                <a className="btn-text" onClick={() => openDetail(r.id)}>查看</a>
              </span>
            </div>
          ))
        )}
        <p className="caption mt16">每周自动诊断一次，也可在「AI 诊断」页手动触发</p>
      </section>

      {detail && (
        <section className="card">
          <div className="between">
            <h3 className="card-title-sm" style={{ margin: 0 }}>报告详情</h3>
            <button className="btn-text" onClick={() => setDetail(null)}>关闭</button>
          </div>
          <div className="score-num mono mt8" style={{ color: healthVar(detail.health_score) }}>{detail.health_score} <span className="small fg2">分</span></div>
          <p className="small fg2 mt8">{detail.summary}</p>
          <div className="grid2 mt16">
            <div className="mini-card"><h3>风险评估</h3><p className="small fg2 mt8" style={{ margin: 0 }}>{detail.risk_assessment}</p></div>
            <div className="mini-card"><h3>资产配置</h3><p className="small fg2 mt8" style={{ margin: 0 }}>{detail.asset_allocation}</p></div>
            <div className="mini-card"><h3>风险敞口</h3><p className="small fg2 mt8" style={{ margin: 0 }}>{detail.risk_exposure}</p></div>
            <div className="mini-card"><h3>策略一致性</h3><p className="small fg2 mt8" style={{ margin: 0 }}>{detail.strategy_consistency}</p></div>
          </div>
          {detail.suggestions && detail.suggestions.length > 0 && (
            <ul className="track-list mt16">
              {detail.suggestions.map((s, i) => <li key={i}>{s}</li>)}
            </ul>
          )}
        </section>
      )}
    </div>
  )
}
