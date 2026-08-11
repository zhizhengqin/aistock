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

const SENTIMENT_BADGE: Record<string, string> = {
  '利好': 'badge up',
  '利空': 'badge down',
  '中性': 'badge',
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
    <>
      <div className="between wrap">
        <div className="flex wrap">
          <div className="flex" style={{ gap: 8 }}>
            {TIME_RANGES.map((r) => (
              <button key={r.hours} className={`pill${hours === r.hours ? ' active' : ''}`} onClick={() => setHours(r.hours)}>
                {r.label}
              </button>
            ))}
          </div>
          <select className="select" aria-label="来源筛选" value={source} onChange={(e) => setSource(e.target.value)}>
            <option value="">全部来源</option>
            {sources.map((s) => (
              <option key={s.name} value={s.name}>{s.name}（{s.count}）</option>
            ))}
          </select>
          <button className="btn btn-primary" onClick={collect} disabled={collecting}>
            {collecting ? '采集中…' : '立即采集'}
          </button>
        </div>
        <span className="caption">共 {total} 条</span>
      </div>

      {loading ? (
        <div className="empty">加载中…</div>
      ) : items.length === 0 ? (
        <div className="empty">暂无新闻，点击「立即采集」拉取最新资讯</div>
      ) : (
        items.map((n) => (
          <section key={n.id} className="card news-card">
            <div className="between wrap">
              <h2 className="news-title">
                {n.url && !n.url.startsWith('sample://') ? (
                  <a href={n.url} target="_blank" rel="noreferrer">{n.title}</a>
                ) : (
                  n.title
                )}
              </h2>
              <span className="caption">{n.source} · {formatTime(n.published_at)}</span>
            </div>
            {n.summary && <p className="small fg2" style={{ margin: 0 }}>{n.summary}</p>}
            <div className="badge-row">
              {n.sentiment && <span className={SENTIMENT_BADGE[n.sentiment] || 'badge'}>{n.sentiment}</span>}
              {n.category && n.category !== '综合' && <span className="badge info">{n.category}</span>}
              {n.industries.map((ind) => (
                <span key={ind} className="badge">{ind}</span>
              ))}
            </div>
          </section>
        ))
      )}
    </>
  )
}
