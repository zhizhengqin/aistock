import { useState, useCallback, useEffect } from "react"
import client from '../api/client'
import { errMsg } from '../utils/errors'

type Tab = 'configs' | 'notifications'

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

export default function Realtime() {
  const [tab, setTab] = useState<Tab>('configs')
  return (
    <div className="space-y-6">
      <div className="flex gap-2 border-b border-gray-200">
        {(['configs', 'notifications'] as Tab[]).map((t) => (
          <button key={t} onClick={() => setTab(t)}
            className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors ${tab === t ? 'border-brand-600 text-brand-600' : 'border-transparent text-gray-500 hover:text-gray-700'}`}>
            {t === 'configs' ? '监测配置' : '消息通知'}
          </button>
        ))}
      </div>
      {tab === 'configs' && <ConfigsView />}
      {tab === 'notifications' && <NotificationsView />}
    </div>
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
    <div className="space-y-4">
      <div className="flex items-center gap-3 flex-wrap">
        <p className="text-sm text-gray-500">交易时段内按设定间隔自动检查价格触发条件</p>
        <button onClick={runCheck} disabled={checking}
          className="px-4 py-1.5 bg-brand-600 text-white rounded text-sm font-medium hover:bg-brand-700 disabled:opacity-50">
          {checking ? '检查中...' : '立即检查'}
        </button>
        <div className="flex gap-1 ml-auto">
          {[['all', '全部'], ['active', '监测中'], ['paused', '已暂停']].map(([v, l]) => (
            <button key={v} onClick={() => setFilter(v)}
              className={`px-3 py-1 rounded text-xs ${filter === v ? 'bg-brand-600 text-white' : 'bg-gray-100 text-gray-600'}`}>
              {l}
            </button>
          ))}
        </div>
      </div>
      {notice && <p className="text-sm text-green-600">{notice}</p>}
      {error && <p className="text-sm text-red-500">{error}</p>}

      <div className="bg-white rounded-lg shadow p-4">
        <h3 className="text-sm font-semibold mb-3">添加监测项</h3>
        <div className="flex flex-wrap items-center gap-2">
          <input placeholder="股票代码" value={form.stock_code} onChange={(e) => setForm({ ...form, stock_code: e.target.value })}
            className="border border-gray-300 rounded px-3 py-1.5 text-sm w-28" />
          <input placeholder="名称(可选)" value={form.stock_name} onChange={(e) => setForm({ ...form, stock_name: e.target.value })}
            className="border border-gray-300 rounded px-3 py-1.5 text-sm w-28" />
          <input placeholder="目标价" type="number" step="0.01" value={form.target_price} onChange={(e) => setForm({ ...form, target_price: e.target.value })}
            className="border border-gray-300 rounded px-3 py-1.5 text-sm w-24" />
          <input placeholder="止损价" type="number" step="0.01" value={form.stop_price} onChange={(e) => setForm({ ...form, stop_price: e.target.value })}
            className="border border-gray-300 rounded px-3 py-1.5 text-sm w-24" />
          <input placeholder="止盈%" type="number" value={form.profit_pct} onChange={(e) => setForm({ ...form, profit_pct: e.target.value })}
            className="border border-gray-300 rounded px-3 py-1.5 text-sm w-20" />
          <input placeholder="止损%" type="number" value={form.loss_pct} onChange={(e) => setForm({ ...form, loss_pct: e.target.value })}
            className="border border-gray-300 rounded px-3 py-1.5 text-sm w-20" />
          <label className="flex items-center gap-1 text-sm text-gray-600">
            <input type="checkbox" checked={form.ai_enabled} onChange={(e) => setForm({ ...form, ai_enabled: e.target.checked })} />
            AI 决策
          </label>
          <button onClick={add} disabled={adding}
            className="px-4 py-1.5 bg-brand-600 text-white rounded text-sm font-medium hover:bg-brand-700 disabled:opacity-50">
            {adding ? '添加中...' : '添加'}
          </button>
        </div>
      </div>

      <div className="bg-white rounded-lg shadow p-4">
        <h3 className="text-sm font-semibold mb-3">监测列表</h3>
        {configs.length === 0 ? (
          <p className="text-sm text-gray-400">暂无监测项</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-gray-50"><tr>
                <th className="px-3 py-2 text-left">股票</th>
                <th className="px-3 py-2 text-right">目标价</th>
                <th className="px-3 py-2 text-right">止损价</th>
                <th className="px-3 py-2 text-right">止盈/止损</th>
                <th className="px-3 py-2 text-center">间隔</th>
                <th className="px-3 py-2 text-center">AI</th>
                <th className="px-3 py-2 text-center">状态</th>
                <th className="px-3 py-2 text-center">操作</th>
              </tr></thead>
              <tbody>
                {configs.map((c) => (
                  <tr key={c.id} className="border-t border-gray-100">
                    <td className="px-3 py-2 font-medium">{c.stock_name || '-'} <span className="text-gray-400 font-mono text-xs">{c.stock_code}</span></td>
                    <td className="px-3 py-2 text-right">{c.target_price || '-'}</td>
                    <td className="px-3 py-2 text-right">{c.stop_price || '-'}</td>
                    <td className="px-3 py-2 text-right">{c.profit_pct}% / {c.loss_pct}%</td>
                    <td className="px-3 py-2 text-center">{c.interval_min} 分钟</td>
                    <td className="px-3 py-2 text-center">{c.ai_enabled ? '开' : '关'}</td>
                    <td className="px-3 py-2 text-center">
                      <button onClick={() => toggleStatus(c)}
                        className={`px-2 py-0.5 rounded text-xs ${c.status === 'active' ? 'bg-green-100 text-green-700' : 'bg-gray-100 text-gray-500'}`}>
                        {c.status === 'active' ? '监测中' : '已暂停'}
                      </button>
                    </td>
                    <td className="px-3 py-2 text-center">
                      <button onClick={() => remove(c)} className="text-xs text-red-500 hover:underline">删除</button>
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
    <div className="space-y-4">
      <div className="flex gap-1">
        {[['pending', '待处理'], ['processed', '已处理']].map(([v, l]) => (
          <button key={v} onClick={() => setStatus(v)}
            className={`px-3 py-1 rounded text-xs ${status === v ? 'bg-brand-600 text-white' : 'bg-gray-100 text-gray-600'}`}>
            {l}
          </button>
        ))}
      </div>
      {error && <p className="text-sm text-red-500">{error}</p>}
      {items.length === 0 ? (
        <p className="text-sm text-gray-400">暂无{status === 'pending' ? '待处理' : '已处理'}通知</p>
      ) : (
        <div className="space-y-2">
          {items.map((n) => (
            <div key={n.id} className="bg-white rounded-lg shadow p-4">
              <div className="flex items-center justify-between mb-1">
                <div className="flex items-center gap-2">
                  <span className="px-2 py-0.5 rounded text-xs bg-brand-50 text-brand-700">{NTYPE_LABELS[n.ntype] || n.ntype}</span>
                  <span className="text-sm font-medium">{n.title}</span>
                </div>
                <div className="flex items-center gap-3">
                  <span className="text-xs text-gray-400">{n.created_at ? new Date(n.created_at).toLocaleString('zh-CN') : ''}</span>
                  {n.status === 'pending' && (
                    <button onClick={() => markProcessed(n)} className="text-xs text-brand-600 hover:underline">标记已处理</button>
                  )}
                </div>
              </div>
              <p className="text-sm text-gray-600">{n.content}</p>
              <p className="text-xs text-gray-400 mt-1">{n.stock_name} <span className="font-mono">{n.stock_code}</span></p>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
