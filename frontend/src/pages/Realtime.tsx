import { useState, useCallback, useEffect } from "react"
import client from '../api/client'
import { errMsg } from '../utils/errors'

type Tab = 'configs' | 'notifications' | 'plans' | 'decisions'

interface MonitorConfig {
  id: number
  stock_code: string
  stock_name: string
  entry_price: number
  target_price: number
  stop_price: number
  profit_pct: number
  loss_pct: number
  interval_min: number
  channels: string
  ai_enabled: boolean
  status: string
  last_checked_at: string | null
}

interface Notification {
  id: number
  config_id: number
  stock_code: string
  stock_name: string
  ntype: string
  title: string
  content: string
  status: string
  created_at: string | null
}

const NTYPE_LABELS: Record<string, string> = {
  target: '到达目标价',
  stop: '触及止损价',
  profit: '止盈提醒',
  loss: '止损提醒',
  ai: 'AI 决策',
}

const NTYPE_BADGE: Record<string, string> = {
  target: 'badge up',
  stop: 'badge down',
  profit: 'badge hold',
  loss: 'badge down',
  ai: 'badge info',
}

export default function Realtime() {
  const [tab, setTab] = useState<Tab>('configs')
  return (
    <>
      <div className="tabs">
        <button className={`tab${tab === 'configs' ? ' active' : ''}`} onClick={() => setTab('configs')}>监测配置</button>
        <button className={`tab${tab === 'notifications' ? ' active' : ''}`} onClick={() => setTab('notifications')}>消息通知</button>
        <button className={`tab${tab === 'plans' ? ' active' : ''}`} onClick={() => setTab('plans')}>AI 交易计划</button>
        <button className={`tab${tab === 'decisions' ? ' active' : ''}`} onClick={() => setTab('decisions')}>AI 决策记录</button>
      </div>
      {tab === 'configs' && <ConfigsView />}
      {tab === 'notifications' && <NotificationsView />}
      {tab === 'plans' && <TradePlansView />}
      {tab === 'decisions' && <DecisionsView />}
    </>
  )
}

function ConfigsView() {
  const [configs, setConfigs] = useState<MonitorConfig[]>([])
  const [filter, setFilter] = useState('all')
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [checking, setChecking] = useState(false)
  const [adding, setAdding] = useState(false)
  const [form, setForm] = useState({
    stock_code: '', stock_name: '', entry_price: '', target_price: '', stop_price: '',
    profit_pct: '10', loss_pct: '5', interval_min: '10', channels: 'in_app', ai_enabled: false,
  })

  const load = useCallback(async () => {
    try {
      const r = await client.get('/stocks/ai-monitoring/configurations', { params: { status: filter } })
      setConfigs(r.data.data)
    } catch {
      setError('加载监测配置失败')
    }
  }, [filter])

  useEffect(() => { load() }, [load])

  const runCheck = async () => {
    setChecking(true); setNotice(''); setError('')
    try {
      const r = await client.post('/stocks/ai-monitoring/check')
      setNotice(`检查完成，触发 ${r.data.data.triggered} 条提醒`)
      await load()
    } catch (err: any) {
      setError(errMsg(err, '检查失败'))
    } finally {
      setChecking(false)
    }
  }

  const add = async () => {
    setError('')
    if (!form.stock_code) { setError('请填写股票代码'); return }
    setAdding(true)
    try {
      await client.post('/stocks/ai-monitoring/configurations', {
        stock_code: form.stock_code.trim(),
        stock_name: form.stock_name.trim(),
        entry_price: parseFloat(form.entry_price) || 0,
        target_price: parseFloat(form.target_price) || 0,
        stop_price: parseFloat(form.stop_price) || 0,
        profit_pct: parseFloat(form.profit_pct) || 10,
        loss_pct: parseFloat(form.loss_pct) || 5,
        interval_min: parseInt(form.interval_min, 10) || 10,
        channels: form.channels,
        ai_enabled: form.ai_enabled,
      })
      setForm({ stock_code: '', stock_name: '', entry_price: '', target_price: '', stop_price: '', profit_pct: '10', loss_pct: '5', interval_min: '10', channels: 'in_app', ai_enabled: false })
      await load()
    } catch (err: any) {
      setError(errMsg(err, '添加失败'))
    } finally {
      setAdding(false)
    }
  }

  const toggleStatus = async (c: MonitorConfig) => {
    try {
      await client.patch(`/stocks/ai-monitoring/configurations/${c.id}`, { status: c.status === 'active' ? 'paused' : 'active' })
      await load()
    } catch {
      setError('更新失败')
    }
  }

  const remove = async (c: MonitorConfig) => {
    try {
      await client.delete(`/stocks/ai-monitoring/configurations/${c.id}`)
      await load()
    } catch {
      setError('删除失败')
    }
  }

  return (
    <div className="panel-stack">
      <div>
        <div className="between wrap">
          <p className="fg2" style={{ margin: 0 }}>交易时段内按设定间隔自动检查价格触发条件，触发后生成消息通知。</p>
          <button className="btn btn-primary" onClick={runCheck} disabled={checking}>{checking ? '检查中...' : '立即检查'}</button>
        </div>
        {notice && <p className="up small mt8" style={{ margin: 0 }}>{notice}</p>}
        {error && <p className="up small mt8" style={{ margin: 0 }}>{error}</p>}
      </div>

      <div className="flex wrap" style={{ gap: 8 }}>
        {[['all', '全部'], ['active', '监测中'], ['paused', '已暂停']].map(([v, l]) => (
          <button key={v} className={`pill${filter === v ? ' active' : ''}`} onClick={() => setFilter(v)}>{l}</button>
        ))}
      </div>

      <section className="card">
        <h2 className="card-title-sm">添加监测项</h2>
        <div className="form-row">
          <div className="field">
            <label>股票代码</label>
            <input className="input mono" type="text" placeholder="如 600519" value={form.stock_code} onChange={(e) => setForm({ ...form, stock_code: e.target.value })} />
          </div>
          <div className="field">
            <label>名称(可选)</label>
            <input className="input" type="text" placeholder="自动识别" value={form.stock_name} onChange={(e) => setForm({ ...form, stock_name: e.target.value })} />
          </div>
          <div className="field">
            <label>目标价</label>
            <input className="input mono" type="number" step="0.01" placeholder="如 1550.00" value={form.target_price} onChange={(e) => setForm({ ...form, target_price: e.target.value })} />
          </div>
          <div className="field">
            <label>止损价</label>
            <input className="input mono" type="number" step="0.01" placeholder="如 1380.00" value={form.stop_price} onChange={(e) => setForm({ ...form, stop_price: e.target.value })} />
          </div>
          <div className="field">
            <label>止盈 %</label>
            <input className="input mono" type="number" value={form.profit_pct} onChange={(e) => setForm({ ...form, profit_pct: e.target.value })} />
          </div>
          <div className="field">
            <label>止损 %</label>
            <input className="input mono" type="number" value={form.loss_pct} onChange={(e) => setForm({ ...form, loss_pct: e.target.value })} />
          </div>
        </div>
        <div className="between wrap mt16">
          <label className="flex" style={{ gap: 8, fontSize: 14 }}>
            <input type="checkbox" checked={form.ai_enabled} onChange={(e) => setForm({ ...form, ai_enabled: e.target.checked })} />
            AI 决策(触发时由 AI 生成交易计划)
          </label>
          <button className="btn btn-dark" onClick={add} disabled={adding}>{adding ? '添加中...' : '添加'}</button>
        </div>
      </section>

      <section className="card">
        <h2 className="card-title-sm">监测列表</h2>
        {configs.length === 0 ? (
          <div className="empty">暂无监测项</div>
        ) : (
          <table className="table">
            <thead>
              <tr>
                <th>股票</th>
                <th className="num">目标价</th>
                <th className="num">止损价</th>
                <th className="num">止盈 / 止损</th>
                <th className="num">间隔</th>
                <th>AI</th>
                <th>状态</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {configs.map((c) => (
                <tr key={c.id}>
                  <td>{c.stock_name || '-'} <span className="muted mono">{c.stock_code}</span></td>
                  <td className="num mono">{c.target_price || '-'}</td>
                  <td className="num mono">{c.stop_price || '-'}</td>
                  <td className="num mono">{c.profit_pct}% / {c.loss_pct}%</td>
                  <td className="num mono">{c.interval_min} 分钟</td>
                  <td>{c.ai_enabled ? '开' : '关'}</td>
                  <td><span className={`badge ${c.status === 'active' ? 'up' : ''}`}>{c.status === 'active' ? '监测中' : '已暂停'}</span></td>
                  <td>
                    <button className="btn-text" onClick={() => toggleStatus(c)}>{c.status === 'active' ? '暂停' : '恢复'}</button>{' '}
                    <button className="btn-text" onClick={() => remove(c)}>删除</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </div>
  )
}

function NotificationsView() {
  const [items, setItems] = useState<Notification[]>([])
  const [status, setStatus] = useState('pending')
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    try {
      const r = await client.get('/stocks/ai-monitoring/notifications', { params: { status } })
      setItems(r.data.data)
    } catch {
      setError('加载通知失败')
    }
  }, [status])

  useEffect(() => { load() }, [load])

  const markProcessed = async (n: Notification) => {
    try {
      await client.patch(`/stocks/ai-monitoring/notifications/${n.id}`, { status: 'processed' })
      await load()
    } catch {
      setError('操作失败')
    }
  }

  return (
    <div className="panel-stack">
      <div className="flex wrap" style={{ gap: 8 }}>
        {[['pending', '待处理'], ['processed', '已处理']].map(([v, l]) => (
          <button key={v} className={`pill${status === v ? ' active' : ''}`} onClick={() => setStatus(v)}>{l}</button>
        ))}
      </div>
      {error && <p className="small up" style={{ margin: 0 }}>{error}</p>}
      {items.length === 0 ? (
        <div className="empty">{status === 'pending' ? '暂无待处理通知' : '暂无已处理通知'}</div>
      ) : (
        <div className="grid3">
          {items.map((n) => (
            <section key={n.id} className="card notif-card">
              <div className="between">
                <span className={NTYPE_BADGE[n.ntype] || 'badge info'}>{NTYPE_LABELS[n.ntype] || n.ntype}</span>
                <span className="caption">{n.created_at ? new Date(n.created_at).toLocaleString('zh-CN') : ''}</span>
              </div>
              <strong>{n.title}</strong>
              <p className="small fg2" style={{ margin: 0 }}>{n.content}</p>
              {n.status === 'pending' && (
                <div><button className="btn btn-ghost" onClick={() => markProcessed(n)}>标记已处理</button></div>
              )}
              <p className="caption" style={{ margin: 0 }}>{n.stock_name} <span className="mono">{n.stock_code}</span></p>
            </section>
          ))}
        </div>
      )}
    </div>
  )
}

const ACTION_LABEL: Record<string, string> = {
  buy: '买入', sell: '卖出', hold: '持有', watch: '观望',
}
const ACTION_BADGE: Record<string, string> = {
  buy: 'badge up', sell: 'badge down', hold: 'badge hold', watch: 'badge info',
}

function TradePlansView() {
  const [items, setItems] = useState<any[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    client.get('/stocks/ai-monitoring/trade-plans').then((r) => {
      setItems(r.data.data.items || [])
      setLoading(false)
    }).catch(() => setLoading(false))
  }, [])

  if (loading) return <div className="empty">加载中...</div>

  if (items.length === 0) {
    return <div className="empty">暂无 AI 交易计划。开启带 AI 分析的监测配置后，AI 会自动生成交易建议。</div>
  }

  return (
    <div className="grid2">
      {items.map((p) => {
        const confPct = Math.round((p.confidence || 0) * 100)
        return (
          <section key={p.id} className="card">
            <div className="between">
              <h2 className="card-title-sm" style={{ margin: 0 }}>{p.stock_name || p.stock_code} <span className="muted mono small">{p.stock_code}</span></h2>
              <span className={ACTION_BADGE[p.action] || 'badge'}>{ACTION_LABEL[p.action] || p.action}</span>
            </div>
            <div className="mt16">
              <span className="caption">置信度</span>
              <div className="conf mono">{confPct}%</div>
              <div className="progress mt8"><i style={{ width: `${confPct}%` }} /></div>
            </div>
            <div className="grid3 mt16">
              <div className="kpi"><div className="k-label">建议价</div><div className="mono" style={{ font: '500 20px/1 var(--font-body)' }}>{p.suggested_price ?? '—'}</div></div>
              <div className="kpi"><div className="k-label">目标价</div><div className="mono up" style={{ font: '500 20px/1 var(--font-body)' }}>{p.target_price ?? '—'}</div></div>
              <div className="kpi"><div className="k-label">止损价</div><div className="mono down" style={{ font: '500 20px/1 var(--font-body)' }}>{p.stop_loss ?? '—'}</div></div>
            </div>
            {p.reasoning && <div className="reason-block mt16">{p.reasoning}</div>}
            <p className="caption mt8">生成于 {(p.created_at || '').slice(0, 19).replace('T', ' ')} · 不构成投资建议</p>
          </section>
        )
      })}
    </div>
  )
}

function DecisionsView() {
  const [items, setItems] = useState<any[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    client.get('/stocks/ai-monitoring/decisions').then((r) => {
      setItems(r.data.data.items || [])
      setLoading(false)
    }).catch(() => setLoading(false))
  }, [])

  if (loading) return <div className="empty">加载中...</div>

  if (items.length === 0) {
    return <div className="empty">暂无 AI 决策记录。监测运行时 AI 的每次判断会记录在这里。</div>
  }

  return (
    <section className="card">
      <h2 className="card-title-sm">决策记录</h2>
      {items.map((d) => (
        <div className="rowline" key={d.id}>
          <div>
            <div className="flex" style={{ gap: 10 }}>
              <strong>{d.stock_name || d.stock_code}</strong>
              <span className="badge">{d.decision_type}</span>
            </div>
            <p className="small fg2" style={{ margin: '6px 0 0' }}>{d.summary}</p>
          </div>
          <span className="caption mono" style={{ flex: '0 0 auto' }}>{(d.created_at || '').slice(5, 16).replace('T', ' ')}</span>
        </div>
      ))}
    </section>
  )
}
