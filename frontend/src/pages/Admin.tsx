import { useState, useEffect } from 'react'
import client from '../api/client'
import { errMsg } from '../utils/errors'

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
      <div className="flex items-center justify-center py-20">
        <div className="text-center">
          <p className="text-gray-500 text-lg mb-2">需要管理员权限</p>
          <p className="text-gray-400 text-sm">当前账号无权访问系统配置页面</p>
        </div>
      </div>
    )
  }

  return (
    <div className="space-y-6">
      <div className="flex gap-2 border-b border-gray-200 overflow-x-auto">
        {(Object.keys(TAB_LABELS) as Tab[]).map((t) => (
          <button key={t} onClick={() => setTab(t)}
            className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors whitespace-nowrap ${tab === t ? 'border-brand-600 text-brand-600' : 'border-transparent text-gray-500 hover:text-gray-700'}`}>
            {TAB_LABELS[t]}
          </button>
        ))}
      </div>
      {tab === 'stats' && <StatsView />}
      {tab === 'users' && <UsersView />}
      {tab === 'llm' && <LlmConfigView />}
      {tab === 'datasource' && <DatasourceView />}
      {tab === 'agent' && <AgentConfigView />}
    </div>
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

  if (loading) return <p className="text-gray-400 text-sm">加载中…</p>

  const cards = [
    { label: '总用户数', value: stats?.total_users ?? 0, color: 'text-blue-600' },
    { label: '活跃用户', value: stats?.active_users ?? 0, color: 'text-green-600' },
    { label: '管理员数', value: stats?.admin_count ?? 0, color: 'text-purple-600' },
    { label: '活跃套餐数', value: stats?.active_plans ?? 0, color: 'text-orange-600' },
    { label: '累计功能调用', value: stats?.total_usage_count ?? 0, color: 'text-brand-600' },
  ]

  return (
    <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-4">
      {cards.map((c) => (
        <div key={c.label} className="bg-white rounded-lg border border-gray-200 p-4">
          <div className="text-xs text-gray-400 mb-1">{c.label}</div>
          <div className={`text-2xl font-bold ${c.color}`}>{c.value}</div>
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
    <div className="space-y-4">
      {error && <p className="text-red-500 text-sm">{error}</p>}
      {msg && <p className="text-brand-600 text-sm">{msg}</p>}
      <div className="bg-white rounded-lg border border-gray-200 overflow-x-auto -mx-3 sm:mx-0">
        <table className="w-full text-sm min-w-[640px]">
          <thead>
            <tr className="border-b border-gray-100 text-left text-gray-400">
              <th className="px-4 py-3 font-medium">ID</th>
              <th className="px-4 py-3 font-medium">用户名</th>
              <th className="px-4 py-3 font-medium">邮箱</th>
              <th className="px-4 py-3 font-medium">角色</th>
              <th className="px-4 py-3 font-medium">会员档</th>
              <th className="px-4 py-3 font-medium">状态</th>
              <th className="px-4 py-3 font-medium">操作</th>
            </tr>
          </thead>
          <tbody>
            {data.items.map((u: any) => (
              <tr key={u.id} className="border-b border-gray-50">
                <td className="px-4 py-3 text-gray-400">{u.id}</td>
                <td className="px-4 py-3 font-medium text-gray-700">{u.username}</td>
                <td className="px-4 py-3 text-gray-500">{u.email || '-'}</td>
                <td className="px-4 py-3">
                  <span className={`px-2 py-0.5 rounded text-xs ${u.role === 'admin' ? 'bg-purple-50 text-purple-600' : 'bg-gray-50 text-gray-500'}`}>
                    {u.role === 'admin' ? '管理员' : '用户'}
                  </span>
                </td>
                <td className="px-4 py-3">
                  <select value={u.tier} onChange={(e) => patch(u.id, { tier: e.target.value })}
                    className="border border-gray-200 rounded px-2 py-1 text-xs">
                    {['free', 'D', 'C', 'B', 'A'].map((t) => <option key={t} value={t}>{t}</option>)}
                  </select>
                </td>
                <td className="px-4 py-3">
                  <span className={`px-2 py-0.5 rounded text-xs ${u.is_active ? 'bg-green-50 text-green-600' : 'bg-red-50 text-red-500'}`}>
                    {u.is_active ? '正常' : '已停用'}
                  </span>
                </td>
                <td className="px-4 py-3 space-x-2 whitespace-nowrap">
                  <button onClick={() => patch(u.id, { is_active: !u.is_active })}
                    className="text-xs text-brand-600 hover:underline">
                    {u.is_active ? '停用' : '启用'}
                  </button>
                  <button onClick={() => patch(u.id, { role: u.role === 'admin' ? 'user' : 'admin' })}
                    className="text-xs text-gray-500 hover:underline">
                    {u.role === 'admin' ? '降为用户' : '升为管理员'}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {loading && <p className="px-4 py-3 text-gray-400 text-sm">加载中…</p>}
      </div>
      <div className="flex items-center justify-between text-sm text-gray-500">
        <span>共 {data.total} 个用户</span>
        <div className="space-x-3">
          <button disabled={page <= 1} onClick={() => load(page - 1)}
            className="disabled:text-gray-300 text-brand-600">上一页</button>
          <span>{page} / {totalPages}</span>
          <button disabled={page >= totalPages} onClick={() => load(page + 1)}
            className="disabled:text-gray-300 text-brand-600">下一页</button>
        </div>
      </div>
    </div>
  )
}


function LlmConfigView() {
  const [config, setConfig] = useState<any>(null)
  const [usage, setUsage] = useState<any>(null)
  const [form, setForm] = useState<any>({})
  const [msg, setMsg] = useState('')
  const [error, setError] = useState('')

  const load = () => {
    client.get('/admin/llm-config')
      .then((r) => { setConfig(r.data.data); setForm({}) })
      .catch((err) => setError(errMsg(err, '加载大模型配置失败')))
    client.get('/admin/llm-usage', { params: { days: 7 } })
      .then((r) => setUsage(r.data.data))
      .catch(() => {})
  }

  useEffect(() => { load() }, [])

  const save = () => {
    setMsg('')
    const body: any = {}
    if (form.llm_model) body.llm_model = form.llm_model
    if (form.llm_base_url) body.llm_base_url = form.llm_base_url
    if (form.deepseek_api_key) body.deepseek_api_key = form.deepseek_api_key
    if (form.daily_token_limit) body.daily_token_limit = Number(form.daily_token_limit)
    if (form.llm_mock !== undefined) body.llm_mock = form.llm_mock
    client.put('/admin/llm-config', body)
      .then((r) => { setMsg(r.data.message || '已保存'); load() })
      .catch((err) => setMsg(errMsg(err, '保存失败')))
  }

  if (!config) return <p className="text-gray-400 text-sm">{error || '加载中…'}</p>

  return (
    <div className="space-y-6">
      <div className="bg-white rounded-lg border border-gray-200 p-5 space-y-4 max-w-xl">
        <h3 className="font-medium text-gray-700">模型配置</h3>
        <label className="flex items-center gap-2 text-sm text-gray-600">
          <input type="checkbox"
            checked={form.llm_mock !== undefined ? form.llm_mock : config.llm_mock}
            onChange={(e) => setForm({ ...form, llm_mock: e.target.checked })} />
          Mock 模式（不调用真实大模型，用于本地开发）
        </label>
        <div>
          <label className="text-xs text-gray-400">模型名称</label>
          <input value={form.llm_model ?? config.llm_model}
            onChange={(e) => setForm({ ...form, llm_model: e.target.value })}
            className="mt-1 w-full border border-gray-200 rounded px-3 py-2 text-sm" />
        </div>
        <div>
          <label className="text-xs text-gray-400">API Base URL</label>
          <input value={form.llm_base_url ?? config.llm_base_url}
            onChange={(e) => setForm({ ...form, llm_base_url: e.target.value })}
            className="mt-1 w-full border border-gray-200 rounded px-3 py-2 text-sm" />
        </div>
        <div>
          <label className="text-xs text-gray-400">API Key（当前：{config.deepseek_api_key_masked || '未配置'}）</label>
          <input type="password" placeholder="留空则不修改"
            value={form.deepseek_api_key ?? ''}
            onChange={(e) => setForm({ ...form, deepseek_api_key: e.target.value })}
            className="mt-1 w-full border border-gray-200 rounded px-3 py-2 text-sm" />
        </div>
        <div>
          <label className="text-xs text-gray-400">每日 Token 限额</label>
          <input type="number" value={form.daily_token_limit ?? config.daily_token_limit}
            onChange={(e) => setForm({ ...form, daily_token_limit: e.target.value })}
            className="mt-1 w-full border border-gray-200 rounded px-3 py-2 text-sm" />
        </div>
        <div className="flex items-center gap-3">
          <button onClick={save}
            className="bg-brand-600 text-white px-4 py-2 rounded text-sm hover:bg-brand-700">
            保存配置
          </button>
          {msg && <span className="text-sm text-gray-500">{msg}</span>}
        </div>
        <p className="text-xs text-gray-400">注意：此处修改仅对当前进程生效，重启后恢复 .env 中的值。</p>
      </div>

      <div className="bg-white rounded-lg border border-gray-200 p-5">
        <h3 className="font-medium text-gray-700 mb-3">近 {usage?.days ?? 7} 天用量（总成本 ¥{usage?.total_cost_yuan?.toFixed(2) ?? '0.00'}）</h3>
        <div className="overflow-x-auto">
          <table className="w-full text-sm min-w-[560px]">
            <thead>
              <tr className="border-b border-gray-100 text-left text-gray-400">
                <th className="py-2 pr-4 font-medium">模块</th>
                <th className="py-2 pr-4 font-medium">调用次数</th>
                <th className="py-2 pr-4 font-medium">Prompt Tokens</th>
                <th className="py-2 pr-4 font-medium">Completion Tokens</th>
                <th className="py-2 font-medium">成本（元）</th>
              </tr>
            </thead>
            <tbody>
              {(usage?.modules ?? []).map((m: any) => (
                <tr key={m.module} className="border-b border-gray-50">
                  <td className="py-2 pr-4 text-gray-700">{m.module}</td>
                  <td className="py-2 pr-4 text-gray-500">{m.calls}</td>
                  <td className="py-2 pr-4 text-gray-500">{m.prompt_tokens}</td>
                  <td className="py-2 pr-4 text-gray-500">{m.completion_tokens}</td>
                  <td className="py-2 text-gray-500">{m.cost_yuan.toFixed(4)}</td>
                </tr>
              ))}
              {(usage?.modules ?? []).length === 0 && (
                <tr><td colSpan={5} className="py-4 text-center text-gray-400">暂无用量记录</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}


function DatasourceView() {
  const [config, setConfig] = useState<any>(null)
  const [testing, setTesting] = useState(false)
  const [result, setResult] = useState<any>(null)
  const [error, setError] = useState('')

  useEffect(() => {
    client.get('/admin/datasource-config')
      .then((r) => setConfig(r.data.data))
      .catch((err) => setError(errMsg(err, '加载数据源配置失败')))
  }, [])

  const test = () => {
    setTesting(true)
    setResult(null)
    client.post('/admin/datasource-test')
      .then((r) => { setResult(r.data.data); setTesting(false) })
      .catch((err) => { setResult({ status: 'error', error: errMsg(err, '测试失败') }); setTesting(false) })
  }

  if (!config) return <p className="text-gray-400 text-sm">{error || '加载中…'}</p>

  const rows: [string, any][] = [
    ['主数据源', config.primary_source],
    ['akshare 版本', config.akshare_version],
    ['Redis', config.redis_url],
    ['数据库', config.database_url_masked],
    ['新闻源', (config.news_sources ?? []).join('、')],
    ['美股数据源', config.us_market_source],
  ]

  return (
    <div className="space-y-6">
      <div className="bg-white rounded-lg border border-gray-200 p-5 max-w-xl">
        <h3 className="font-medium text-gray-700 mb-3">数据源配置</h3>
        <dl className="space-y-2 text-sm">
          {rows.map(([k, v]) => (
            <div key={k} className="flex gap-4">
              <dt className="w-24 shrink-0 text-gray-400">{k}</dt>
              <dd className="text-gray-700 break-all">{String(v)}</dd>
            </div>
          ))}
        </dl>
        <div className="mt-4 flex items-center gap-3">
          <button onClick={test} disabled={testing}
            className="bg-brand-600 text-white px-4 py-2 rounded text-sm hover:bg-brand-700 disabled:opacity-50">
            {testing ? '测试中…' : '测试连接'}
          </button>
          {result && (
            <span className={`text-sm ${result.status === 'ok' ? 'text-green-600' : 'text-red-500'}`}>
              {result.status === 'ok' ? `连接正常，获取 ${result.rows} 行行情数据` : `连接失败：${result.error}`}
            </span>
          )}
        </div>
      </div>
    </div>
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

  if (!config) return <p className="text-gray-400 text-sm">{error || '加载中…'}</p>

  const fields: [string, string, string][] = [
    ['analysis_max_analysts', '单股分析 Agent 数量', '每个分析任务并行的分析师 Agent 上限'],
    ['task_timeout', '任务超时时间（秒）', '单个 Agent 任务的最长执行时间'],
    ['max_concurrent_tasks', '最大并发任务数', '系统同时运行的分析任务上限'],
  ]

  return (
    <div className="bg-white rounded-lg border border-gray-200 p-5 space-y-4 max-w-xl">
      <h3 className="font-medium text-gray-700">Agent 运行配置</h3>
      {fields.map(([key, label, hint]) => (
        <div key={key}>
          <label className="text-sm text-gray-600">{label}</label>
          <input type="number" value={form[key] ?? ''}
            onChange={(e) => setForm({ ...form, [key]: e.target.value })}
            className="mt-1 w-full border border-gray-200 rounded px-3 py-2 text-sm" />
          <p className="text-xs text-gray-400 mt-1">{hint}</p>
        </div>
      ))}
      <div className="flex items-center gap-3">
        <button onClick={save}
          className="bg-brand-600 text-white px-4 py-2 rounded text-sm hover:bg-brand-700">
          保存配置
        </button>
        {msg && <span className="text-sm text-gray-500">{msg}</span>}
      </div>
    </div>
  )
}
