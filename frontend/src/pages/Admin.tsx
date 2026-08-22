import { useState, useEffect } from 'react'
import client from '../api/client'
import { errMsg } from '../utils/errors'
import LlmModelsView from './admin/LlmModelsView'
import DataSourcesView from './admin/DataSourcesView'

type Tab = 'stats' | 'users' | 'llm' | 'datasource' | 'agent'

const TAB_LABELS: Record<Tab, string> = {
  stats: '系统概览',
  users: '用户管理',
  llm: '大模型配置',
  datasource: '数据源配置',
  agent: 'Agent 配置',
}

export default function Admin() {
  const [tab, setTab] = useState<Tab>('stats')
  const [forbidden, setForbidden] = useState(false)

  useEffect(() => {
    client.get('/admin/stats').catch((err) => {
      if (err.response?.status === 403) setForbidden(true)
    })
  }, [])

  if (forbidden) {
    return (
      <div className="empty">
        <p style={{ margin: 0 }}>需要管理员权限</p>
        <p className="caption mt8" style={{ margin: 0 }}>当前账号无权访问系统配置页面</p>
      </div>
    )
  }

  return (
    <>
      <div className="tabs">
        {(Object.keys(TAB_LABELS) as Tab[]).map((t) => (
          <button key={t} className={`tab${tab === t ? ' active' : ''}`} onClick={() => setTab(t)}>{TAB_LABELS[t]}</button>
        ))}
      </div>
      {tab === 'stats' && <StatsView />}
      {tab === 'users' && <UsersView />}
      {tab === 'llm' && <LlmModelsView />}
      {tab === 'datasource' && <DataSourcesView />}
      {tab === 'agent' && <AgentConfigView />}
    </>
  )
}

function StatsView() {
  const [stats, setStats] = useState<any>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    client.get('/admin/stats').then((r) => {
      setStats(r.data.data)
      setLoading(false)
    }).catch(() => setLoading(false))
  }, [])

  if (loading) return <div className="empty">加载中…</div>

  const cards = [
    { label: '总用户数', value: stats?.total_users ?? 0, sub: '全部注册用户' },
    { label: '活跃用户', value: stats?.active_users ?? 0, sub: '近 7 日有登录' },
    { label: '管理员数', value: stats?.admin_count ?? 0, sub: '含超级管理员' },
    { label: '活跃套餐数', value: stats?.active_plans ?? 0, sub: '付费会员在订数' },
    { label: '累计功能调用', value: stats?.total_usage_count ?? 0, sub: '上线以来全部模块' },
  ]

  return (
    <div className="kpi-grid" style={{ gridTemplateColumns: 'repeat(auto-fit,minmax(180px,1fr))' }}>
      {cards.map((c) => (
        <div key={c.label} className="kpi">
          <div className="k-label">{c.label}</div>
          <div className="k-value mono">{Number(c.value).toLocaleString()}</div>
          <div className="k-sub muted">{c.sub}</div>
        </div>
      ))}
    </div>
  )
}


function UsersView() {
  const [data, setData] = useState<any>({ items: [], total: 0, page: 1, page_size: 20 })
  const [page, setPage] = useState(1)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [msg, setMsg] = useState('')

  const load = (p: number) => {
    setLoading(true)
    client.get('/admin/users', { params: { page: p, page_size: 20 } })
      .then((r) => { setData(r.data.data); setPage(p); setLoading(false) })
      .catch((err) => { setError(errMsg(err, '加载用户列表失败')); setLoading(false) })
  }

  useEffect(() => { load(1) }, [])

  const patch = (id: number, body: any) => {
    setMsg('')
    client.patch(`/admin/users/${id}`, body)
      .then((r) => { setMsg(r.data.message || '已更新'); load(page) })
      .catch((err) => setMsg(errMsg(err, '操作失败')))
  }

  const totalPages = Math.max(1, Math.ceil(data.total / data.page_size))

  return (
    <section className="card">
      <h2 className="card-title">用户管理</h2>
      {error && <p className="small up" style={{ margin: 0 }}>{error}</p>}
      {msg && <p className="small" style={{ margin: 0, color: 'var(--accent)' }}>{msg}</p>}
      <table className="table">
        <thead>
          <tr>
            <th>ID</th>
            <th>用户名</th>
            <th>邮箱</th>
            <th>角色</th>
            <th>会员档</th>
            <th>状态</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          {data.items.map((u: any) => (
            <tr key={u.id}>
              <td className="mono">{u.id}</td>
              <td>{u.username}</td>
              <td className="mono">{u.email || '-'}</td>
              <td><span className={u.role === 'admin' ? 'badge info' : 'badge'}>{u.role === 'admin' ? '管理员' : '用户'}</span></td>
              <td>
                <select className="select" style={{ minHeight: 36, padding: '6px 10px' }}
                  value={u.tier} onChange={(e) => patch(u.id, { tier: e.target.value })}>
                  {['free', 'D', 'C', 'B', 'A'].map((t) => <option key={t} value={t}>{t}</option>)}
                </select>
              </td>
              <td><span className={u.is_active ? 'badge up' : 'badge'}>{u.is_active ? '正常' : '已停用'}</span></td>
              <td>
                <button className="btn-text" onClick={() => patch(u.id, { is_active: !u.is_active })}>
                  {u.is_active ? '停用' : '启用'}
                </button>
                <button className="btn-text" onClick={() => patch(u.id, { role: u.role === 'admin' ? 'user' : 'admin' })}>
                  {u.role === 'admin' ? '降为用户' : '升为管理员'}
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {loading && <p className="caption mt8">加载中…</p>}
      <div className="between mt16 wrap">
        <span className="caption">共 {data.total} 个用户</span>
        <div className="flex">
          <button className="btn btn-ghost" disabled={page <= 1} onClick={() => load(page - 1)}>上一页</button>
          <span className="small mono">{page} / {totalPages}</span>
          <button className="btn btn-ghost" disabled={page >= totalPages} onClick={() => load(page + 1)}>下一页</button>
        </div>
      </div>
    </section>
  )
}


function AgentConfigView() {
  const [config, setConfig] = useState<any>(null)
  const [form, setForm] = useState<any>({})
  const [msg, setMsg] = useState('')
  const [error, setError] = useState('')

  const load = () => {
    client.get('/admin/agent-config')
      .then((r) => { setConfig(r.data.data); setForm(r.data.data) })
      .catch((err) => setError(errMsg(err, '加载 Agent 配置失败')))
  }

  useEffect(() => { load() }, [])

  const save = () => {
    setMsg('')
    client.put('/admin/agent-config', {
      analysis_max_analysts: Number(form.analysis_max_analysts),
      task_timeout: Number(form.task_timeout),
      max_concurrent_tasks: Number(form.max_concurrent_tasks),
    })
      .then((r) => { setMsg(r.data.message || '已保存'); load() })
      .catch((err) => setMsg(errMsg(err, '保存失败')))
  }

  if (!config) return <div className="empty">{error || '加载中…'}</div>

  const fields: [string, string, string][] = [
    ['analysis_max_analysts', '单股分析 Agent 数量', '每个分析任务并行的分析师 Agent 上限'],
    ['task_timeout', '任务超时时间(秒)', '单个 Agent 任务的最长执行时间'],
    ['max_concurrent_tasks', '最大并发任务数', '系统同时运行的分析任务上限'],
  ]

  return (
    <section className="card">
      <h2 className="card-title">Agent 配置</h2>
      {fields.map(([key, label, hint]) => (
        <div className="form-row mt16" key={key}>
          <div className="field">
            <label>{label}</label>
            <input className="input mono" type="number" value={form[key] ?? ''}
              onChange={(e) => setForm({ ...form, [key]: e.target.value })} />
            <span className="caption">{hint}</span>
          </div>
        </div>
      ))}
      <div className="flex mt16 wrap">
        <button className="btn btn-dark" onClick={save}>保存配置</button>
        {msg && <span className="test-result" style={{ color: 'var(--accent)' }}>{msg}</span>}
      </div>
    </section>
  )
}
