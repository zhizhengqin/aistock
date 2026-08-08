import { useState, useEffect, useCallback, useRef } from 'react'
import client from '../api/client'

interface NewsItem {
  id: number
  title: string
  url: string
  source: string
  summary: string
  published_at: string | null
  sentiment: string
  category: string
  industries: string[]
}

interface SourceOption {
  name: string
  count: number
}

const TIME_RANGES = [
  { label: '6小时', hours: 6 },
  { label: '24小时', hours: 24 },
  { label: '3天', hours: 72 },
  { label: '全部', hours: 0 },
]

const SENTIMENT_STYLE: Record<string, string> = {
  '利好': 'bg-red-50 text-red-600 border border-red-200',
  '利空': 'bg-green-50 text-green-600 border border-green-200',
  '中性': 'bg-gray-50 text-gray-500 border border-gray-200',
}

function formatTime(iso: string | null): string {
  if (!iso) return ''
  const d = new Date(iso)
  const now = new Date()
  const diffMin = Math.floor((now.getTime() - d.getTime()) / 60000)
  if (diffMin < 1) return '刚刚'
  if (diffMin < 60) return `${diffMin} 分钟前`
  if (diffMin < 1440) return `${Math.floor(diffMin / 60)} 小时前`
  return d.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
}

export default function News() {
  const [items, setItems] = useState<NewsItem[]>([])
  const [total, setTotal] = useState(0)
  const [hours, setHours] = useState(24)
  const [source, setSource] = useState('')
  const [sources, setSources] = useState<SourceOption[]>([])
  const [loading, setLoading] = useState(false)
  const [collecting, setCollecting] = useState(false)
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const resp = await client.get('/news', { params: { hours, source, limit: 100 } })
      setItems(resp.data.data.items)
      setTotal(resp.data.data.total)
    } finally {
      setLoading(false)
    }
  }, [hours, source])

  const loadSources = useCallback(async () => {
    const resp = await client.get('/news/sources')
    setSources(resp.data.data)
  }, [])

  useEffect(() => { load() }, [load])
  useEffect(() => { loadSources() }, [loadSources])
  useEffect(() => () => { if (pollRef.current) clearInterval(pollRef.current) }, [])

  const collect = async () => {
    setCollecting(true)
    try {
      const resp = await client.post('/news/collect')
      const taskId = resp.data.data.task_id
      if (pollRef.current) clearInterval(pollRef.current)
      pollRef.current = setInterval(async () => {
        const t = await client.get(`/tasks/${taskId}`)
        const status = t.data.data.status
        if (status === 'success' || status === 'failed') {
          if (pollRef.current) clearInterval(pollRef.current)
          setCollecting(false)
          load()
          loadSources()
        }
      }, 2000)
    } catch {
      setCollecting(false)
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <div className="flex rounded-lg border border-gray-200 overflow-hidden">
          {TIME_RANGES.map((r) => (
            <button key={r.hours} onClick={() => setHours(r.hours)}
              className={`px-3 py-1.5 text-sm transition-colors ${hours === r.hours ? 'bg-brand-600 text-white' : 'bg-white text-gray-600 hover:bg-gray-50'}`}>
              {r.label}
            </button>
          ))}
        </div>
        <select value={source} onChange={(e) => setSource(e.target.value)}
          className="px-3 py-1.5 text-sm border border-gray-200 rounded-lg bg-white text-gray-700">
          <option value="">全部来源</option>
          {sources.map((s) => (
            <option key={s.name} value={s.name}>{s.name}（{s.count}）</option>
          ))}
        </select>
        <button onClick={collect} disabled={collecting}
          className="px-4 py-1.5 text-sm bg-brand-600 text-white rounded-lg hover:bg-brand-700 disabled:opacity-50">
          {collecting ? '采集中…' : '立即采集'}
        </button>
        <span className="text-sm text-gray-400">共 {total} 条</span>
      </div>

      {loading ? (
        <div className="text-center py-12 text-gray-400">加载中…</div>
      ) : items.length === 0 ? (
        <div className="text-center py-12 text-gray-400">
          暂无新闻，点击「立即采集」拉取最新资讯
        </div>
      ) : (
        <div className="space-y-3">
          {items.map((n) => (
            <div key={n.id} className="bg-white rounded-lg border border-gray-100 p-4 hover:border-gray-200 transition-colors">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0 flex-1">
                  {n.url && !n.url.startsWith('sample://') ? (
                    <a href={n.url} target="_blank" rel="noreferrer"
                      className="text-sm font-medium text-gray-900 hover:text-brand-600 line-clamp-2">
                      {n.title}
                    </a>
                  ) : (
                    <span className="text-sm font-medium text-gray-900 line-clamp-2">{n.title}</span>
                  )}
                  {n.summary && (
                    <p className="mt-1 text-xs text-gray-500 line-clamp-2">{n.summary}</p>
                  )}
                  <div className="mt-2 flex flex-wrap items-center gap-2">
                    {n.sentiment && (
                      <span className={`px-2 py-0.5 text-xs rounded ${SENTIMENT_STYLE[n.sentiment] || SENTIMENT_STYLE['中性']}`}>
                        {n.sentiment}
                      </span>
                    )}
                    {n.category && n.category !== '综合' && (
                      <span className="px-2 py-0.5 text-xs rounded bg-blue-50 text-blue-600 border border-blue-200">{n.category}</span>
                    )}
                    {n.industries.map((ind) => (
                      <span key={ind} className="px-2 py-0.5 text-xs rounded bg-gray-50 text-gray-600 border border-gray-200">{ind}</span>
                    ))}
                  </div>
                </div>
                <div className="shrink-0 text-right">
                  <div className="text-xs text-gray-400">{n.source}</div>
                  <div className="text-xs text-gray-400 mt-1">{formatTime(n.published_at)}</div>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
