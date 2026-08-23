import { useCallback, useState, useEffect, useRef } from 'react'
import ReactECharts from 'echarts-for-react'
import client from '../api/client'
import { errMsg } from '../utils/errors'
import { isTaskFailure } from '../utils/taskStatus'

type Tab = 'single' | 'batch' | 'history'
type StepStatus = 'waiting' | 'analyzing' | 'completed' | 'failed' | 'unknown'
type AnalystResult = Record<string, unknown>

export interface AnalysisStep {
  key: string
  label: string
  status: StepStatus
  result: AnalystResult | null
  error: string | null
}

interface TaskInfo {
  task_id: number
  stock_code: string
}

interface TaskStatus {
  id: number
  status: string
  progress: number
  phase?: string
  error: string | null
  result: { report_id?: number; stock_code?: string } | null
  steps?: AnalysisStep[]
}

interface KlineRow {
  date: string
  open: number
  close: number
  high: number
  low: number
  volume: number
}

interface StockInfo {
  code?: string
  name?: string
  price?: number | null
  change_pct?: number | null
  pe_ttm?: number | null
  pb?: number | null
  market_cap?: number | null
  industry?: string | null
}

interface IndicatorSet {
  ma?: Record<string, number | null>
  macd?: Record<string, number | null>
  rsi?: Record<string, number | null>
  kdj?: Record<string, number | null>
  boll?: Record<string, number | null>
}

interface ReportData {
  _id?: number
  stock_code: string
  stock_name: string
  stock_info: StockInfo
  indicators: IndicatorSet
  kline?: KlineRow[]
  analysts?: Record<string, AnalystResult>
  decision?: {
    rating?: string
    target_price?: number | null
    stop_loss?: number | null
    confidence?: number | null
    entry_range?: string
    take_profit?: string
    holding_period?: string
    position_size?: string
    risk_warning?: string
    key_watchpoints?: string[]
    meeting_summary?: string
  }
  disclaimer?: string
  analyzed_at?: string
  data_warnings?: string[]
}

interface SnapshotData {
  stock_code?: string
  info?: StockInfo
  indicators?: IndicatorSet
  kline?: KlineRow[]
  financial?: Record<string, unknown> | null
  warnings?: string[]
}

const ANALYST_ORDER = ['technical', 'fundamental', 'capital', 'news', 'sentiment', 'risk']
const ANALYST_LABELS: Record<string, string> = {
  technical: '技术面分析师',
  fundamental: '基本面分析师',
  capital: '资金面分析师',
  news: '消息面分析师',
  sentiment: '情绪面分析师',
  risk: '风险分析师',
  chief: '首席分析师',
}
const STEP_STATUS_LABELS: Record<StepStatus, string> = {
  waiting: '等待中',
  analyzing: '分析中',
  completed: '已完成',
  failed: '失败',
  unknown: '状态未知',
}
const RATING_BADGE: Record<string, string> = { 买入: 'badge up', 持有: 'badge hold', 卖出: 'badge down' }

export default function Analysis() {
  const [tab, setTab] = useState<Tab>('single')
  return (
    <>
      <div className="tabs">
        {(['single', 'batch', 'history'] as Tab[]).map((t) => (
          <button key={t} onClick={() => setTab(t)} className={`tab${tab === t ? ' active' : ''}`}>
            {t === 'single' ? '单股分析' : t === 'batch' ? '批量分析' : '历史记录'}
          </button>
        ))}
      </div>
      {tab === 'single' && <SingleAnalysis />}
      {tab === 'batch' && <BatchAnalysis />}
      {tab === 'history' && <HistoryView />}
    </>
  )
}

function SingleAnalysis() {
  const [code, setCode] = useState('')
  const [loading, setLoading] = useState(false)
  const [progress, setProgress] = useState(0)
  const [status, setStatus] = useState('')
  const [taskStatus, setTaskStatus] = useState<TaskStatus | null>(null)
  const [snapshot, setSnapshot] = useState<SnapshotData | null>(null)
  const [report, setReport] = useState<ReportData | null>(null)
  const [error, setError] = useState('')
  const snapshotRequestId = useRef(0)

  const pollTask = useCallback(async (id: number) => {
    for (let i = 0; i < 300; i++) {
      await new Promise((resolve) => setTimeout(resolve, 1000))
      try {
        const resp = await client.get(`/tasks/${id}`)
        const data: TaskStatus = resp.data.data
        setTaskStatus(data)
        setProgress(data.progress || 0)
        setStatus(data.phase || data.status)
        if (data.status === 'success' && data.result?.report_id) {
          const r2 = await client.get(`/stocks/user/results/${data.result.report_id}`)
          setReport({ ...r2.data.data.report, _id: r2.data.data.id })
          setLoading(false)
          return
        }
        if (isTaskFailure(data.status)) {
          setError(data.error || '分析失败')
          setLoading(false)
          return
        }
      } catch {
        // A transient polling error should not erase already visible steps.
      }
    }
    setError('轮询超时')
    setLoading(false)
  }, [])

  const loadSnapshot = async (stockCode: string, requestId: number) => {
    try {
      const response = await client.get(`/stocks/${encodeURIComponent(stockCode)}/snapshot`)
      const payload: unknown = response.data?.data ?? response.data
      if (!isRecord(payload)) throw new Error('股票行情概览数据格式不可用')
      const info = isRecord(payload.info) ? payload.info as StockInfo : undefined
      const indicators = isRecord(payload.indicators) ? payload.indicators as IndicatorSet : undefined
      const kline = Array.isArray(payload.kline) ? payload.kline as KlineRow[] : []
      if (!info && !indicators && kline.length === 0) throw new Error('股票行情概览数据为空')
      if (snapshotRequestId.current !== requestId) return
      setSnapshot({
        stock_code: typeof payload.stock_code === 'string' ? payload.stock_code : stockCode,
        info,
        indicators,
        kline,
        financial: isRecord(payload.financial) ? payload.financial : null,
        warnings: Array.isArray(payload.warnings) ? payload.warnings.filter((item): item is string => typeof item === 'string') : [],
      })
    } catch {
      // The quote view is optional.  It must never stop task polling or hide
      // an already validated analyst result.
      if (snapshotRequestId.current !== requestId) return
      setSnapshot({ stock_code: stockCode, warnings: ['股票行情概览暂不可用，分析仍在继续'] })
    }
  }

  const submit = async () => {
    const normalizedCode = code.trim()
    if (!normalizedCode) { setError('请输入股票代码'); return }
    const requestId = snapshotRequestId.current + 1
    snapshotRequestId.current = requestId
    setError('')
    setReport(null)
    setTaskStatus(null)
    setSnapshot(null)
    setLoading(true)
    setProgress(0)
    setStatus('preparing')
    try {
      // Start the optional market snapshot and durable task polling together;
      // neither request waits for the other.
      void loadSnapshot(normalizedCode, requestId)
      const resp = await client.post('/stocks/analyze', { stock_codes: [normalizedCode] })
      const task: TaskInfo = resp.data.data.tasks[0]
      void pollTask(task.task_id)
    } catch (err: unknown) {
      snapshotRequestId.current += 1
      setSnapshot(null)
      setError(errMsg(err, '提交失败'))
      setLoading(false)
    }
  }

  return (
    <div className="panel-stack">
      <section className="card">
        <div className="flex wrap">
          <input className="input" style={{ flex: 1, minWidth: 240 }} value={code} onChange={(event) => setCode(event.target.value)} placeholder="输入股票代码，如 600519" />
          <button className="btn btn-primary" onClick={submit} disabled={loading}>{loading ? '分析中...' : '开始分析'}</button>
        </div>
        <p className="caption mt8">支持沪深 A 股 6 位代码 · 6 位 AI 分析师协同生成完整投研报告</p>
      </section>
      {error && <p className="small" style={{ color: 'var(--up)' }}>{error}</p>}
      {!report && (loading || snapshot) && <SnapshotProgressView snapshot={snapshot} />}
      {!report && (loading || taskStatus) && <TaskProgressPanel task={taskStatus} progress={progress} status={status} />}
      {report && <ReportView report={report} />}
    </div>
  )
}

function SnapshotProgressView({ snapshot }: { snapshot: SnapshotData | null }) {
  if (!snapshot) {
    return <section className="card analysis-snapshot-placeholder"><div className="empty">股票行情概览加载中…</div></section>
  }

  const info = snapshot.info
  const financial = snapshot.financial || {}
  const overviewInfo: StockInfo | undefined = info ? {
    ...info,
    pe_ttm: info.pe_ttm ?? numberValue(financial.pe_ttm),
    pb: info.pb ?? numberValue(financial.pb),
    market_cap: info.market_cap ?? numberValue(financial.market_cap),
  } : undefined
  const warnings = snapshot.warnings || []

  if (!overviewInfo && !snapshot.kline?.length && !snapshot.indicators) {
    return <section className="card analysis-snapshot-placeholder"><p className="small" style={{ color: 'var(--up)' }}>{warnings[0] || '股票行情概览暂不可用，分析仍在继续'}</p></section>
  }

  return <div className="analysis-snapshot panel-stack">
    {overviewInfo && <StockOverview stockName={overviewInfo.name || snapshot.stock_code || '股票'} stockCode={overviewInfo.code || snapshot.stock_code || ''} info={overviewInfo} warnings={warnings} />}
    {snapshot.kline && snapshot.kline.length > 0 ? <KlineChart rows={snapshot.kline} /> : <section className="card"><h2 className="card-title">K线与成交量</h2><div className="empty">行情 K 线暂不可用，分析仍在继续</div></section>}
    {snapshot.indicators ? <TechnicalIndicatorSection indicators={snapshot.indicators} /> : <section className="card"><h2 className="card-title">技术指标</h2><div className="empty">技术指标暂不可用，分析仍在继续</div></section>}
  </div>
}

function TaskProgressPanel({ task, progress, status }: { task: TaskStatus | null; progress: number; status: string }) {
  const steps = task?.steps || []
  return (
    <section className="card analysis-progress-panel">
      <div className="between wrap"><span className="fg2 small">分析阶段：<span className="mono">{phaseLabel(task?.phase || status)}</span><span className="muted"> · {progress}%</span></span></div>
      <div className="progress mt8"><i style={{ width: `${Math.max(0, Math.min(100, progress))}%` }} /></div>
      {steps.length > 0 && <div className="analyst-step-grid mt16">{steps.map((step) => <AnalystCard key={step.key} step={step} />)}</div>}
    </section>
  )
}

function phaseLabel(phase: string) {
  return ({ preparing: '准备数据', analyzing: '分析师协同', meeting: '投研会议', completed: '已完成', failed: '已结束' } as Record<string, string>)[phase] || phase || '等待中'
}

function AnalystCard({ step }: { step: AnalysisStep }) {
  const title = step.label || ANALYST_LABELS[step.key] || step.key
  return (
    <article className={`mini-card analyst-card analyst-${step.status}`}>
      <div className="between wrap"><h3>{title}</h3><span className={`badge analyst-status analyst-status-${step.status}`}>{STEP_STATUS_LABELS[step.status]}</span></div>
      {step.status === 'failed' || step.status === 'unknown' ? <p className="small mt8" style={{ color: 'var(--up)' }}>{step.error || '本步骤暂不可用'}</p> : step.result ? <AnalystResultView analystKey={step.key} result={step.result} /> : <p className="caption mt8">{step.status === 'analyzing' ? '模型正在整理结构化结论…' : '等待前序数据准备完成'}</p>}
    </article>
  )
}

function AnalystResultView({ analystKey, result }: { analystKey: string; result: AnalystResult }) {
  const score = numberValue(result.score) ?? numberValue(result.sentiment_score) ?? numberValue(result.risk_score)
  return (
    <div className="small fg2 mt8 analyst-result">
      {score != null && <p className="analyst-score">评分 <span className="mono">{score}</span></p>}
      {analystKey === 'technical' && <><TextLine label="趋势" value={result.trend} /><TextLine label="短期" value={result.short_trend} /><TextLine label="中期" value={result.mid_trend} /><TextLine label="长期" value={result.long_trend} /><TextLine label="形态" value={result.pattern} /><TextLine label="指标解读" value={result.indicator_readings} /><TextLine label="突破概率" value={result.breakout_prob != null ? `${result.breakout_prob}%` : undefined} /><SupportResistance value={result.support_resistance} /></>}
      {analystKey === 'fundamental' && <><TextLine label="财务健康度" value={result.financial_health} /><TextLine label="盈利能力" value={result.profitability} /><TextLine label="估值" value={result.valuation} /><TextLine label="分析" value={result.detail} /></>}
      {analystKey === 'capital' && <><TextLine label="主力资金" value={result.main_flow} /><TextLine label="资金趋势" value={result.flow_trend} /><TextLine label="分析" value={result.detail} /></>}
      {analystKey === 'news' && <><TextLine label="消息倾向" value={result.sentiment_rating} /><TextLine label="影响" value={result.impact} /><StringList label="关键信息" value={result.key_news} /></>}
      {analystKey === 'sentiment' && <><TextLine label="指标" value={result.indicators} /><TextLine label="情绪评估" value={result.assessment} /></>}
      {analystKey === 'risk' && <><TextLine label="风险等级" value={result.risk_level} /><TextLine label="风险分析" value={result.analysis} /><TextLine label="控制建议" value={result.advice} /></>}
    </div>
  )
}

function TextLine({ label, value }: { label: string; value: unknown }) {
  const text = textValue(value)
  return text ? <p style={{ margin: '5px 0' }}><span className="muted">{label}：</span>{text}</p> : null
}

function StringList({ label, value }: { label: string; value: unknown }) {
  if (!Array.isArray(value)) return null
  const items = value.filter((item): item is string => typeof item === 'string')
  if (!items.length) return null
  return <div><span className="muted">{label}：</span><ul className="compact-list">{items.map((item, index) => <li key={`${item}-${index}`}>{item}</li>)}</ul></div>
}

function SupportResistance({ value }: { value: unknown }) {
  if (!Array.isArray(value)) return null
  const rows = value.filter((item): item is { type?: string; price?: number; strength?: string } => typeof item === 'object' && item !== null)
  if (!rows.length) return null
  return <p style={{ margin: '5px 0' }}><span className="muted">支撑阻力：</span>{rows.map((row) => `${row.type || '位置'} ${row.price ?? 'N/A'}（${row.strength || '未说明'}）`).join('；')}</p>
}

function textValue(value: unknown): string {
  if (typeof value === 'string' || typeof value === 'number') return String(value)
  return ''
}

function formatValue(value: unknown): string {
  return value == null || value === '' ? 'N/A' : String(value)
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function numberValue(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function BatchAnalysis() {
  const [codesText, setCodesText] = useState('')
  const [loading, setLoading] = useState(false)
  const [results, setResults] = useState<{ code: string; status: string; report_id?: number }[]>([])
  const [error, setError] = useState('')
  const submit = async () => {
    const codes = codesText.split(/[,\n\s]+/).filter(Boolean)
    if (!codes.length) { setError('请输入至少一个股票代码'); return }
    if (codes.length > 50) { setError('最多50只股票'); return }
    setError(''); setLoading(true); setResults(codes.map((code) => ({ code, status: 'pending' })))
    try {
      const resp = await client.post('/stocks/analyze', { stock_codes: codes })
      const tasks: TaskInfo[] = resp.data.data.tasks
      const updated: { code: string; status: string; report_id?: number }[] = codes.map((code) => ({ code, status: 'pending' }))
      for (let i = 0; i < tasks.length; i++) {
        for (let attempt = 0; attempt < 300; attempt++) {
          await new Promise((resolve) => setTimeout(resolve, 1000))
          try {
            const data: TaskStatus = (await client.get(`/tasks/${tasks[i].task_id}`)).data.data
            if (data.status === 'success' && data.result?.report_id) { updated[i] = { code: tasks[i].stock_code, status: 'success', report_id: data.result.report_id }; setResults([...updated]); break }
            if (isTaskFailure(data.status)) { updated[i] = { code: tasks[i].stock_code, status: data.status }; setResults([...updated]); break }
          } catch { /* keep polling this batch item */ }
        }
      }
      setLoading(false)
    } catch (err: unknown) { setError(errMsg(err, '提交失败')); setLoading(false) }
  }
  return <div className="panel-stack"><section className="card"><div className="field"><label>股票代码列表</label><textarea className="textarea mono" value={codesText} onChange={(event) => setCodesText(event.target.value)} placeholder={'输入股票代码，逗号或换行分隔，最多50只\n例如: 600519,000858,002714'} rows={5} /></div><button className="btn btn-primary mt16" onClick={submit} disabled={loading}>{loading ? '批量分析中...' : '开始批量分析'}</button>{error && <p className="small mt8" style={{ color: 'var(--up)' }}>{error}</p>}</section>{results.length > 0 && <section className="card" style={{ padding: 0 }}><div style={{ overflowX: 'auto' }}><table className="table"><thead><tr><th>股票代码</th><th>状态</th></tr></thead><tbody>{results.map((result, index) => <tr key={index}><td className="mono">{result.code}</td><td>{result.status === 'success' && <span className="badge down">完成</span>}{isTaskFailure(result.status) && <span className="badge up">失败</span>}{result.status === 'pending' && <span className="badge">等待中...</span>}</td></tr>)}</tbody></table></div></section>}</div>
}

function HistoryView() {
  const [items, setItems] = useState<any[]>([])
  const [selected, setSelected] = useState<ReportData | null>(null)
  const [loading, setLoading] = useState(true)
  useEffect(() => { client.get('/stocks/user/results').then((response) => { setItems(response.data.data.items || []); setLoading(false) }).catch(() => setLoading(false)) }, [])
  const viewDetail = async (id: number) => { const response = await client.get(`/stocks/user/results/${id}`); setSelected({ ...response.data.data.report, _id: response.data.data.id }) }
  if (selected) return <ReportView report={selected} onBack={() => setSelected(null)} />
  return <section className="card" style={{ padding: 0 }}>{loading ? <div className="empty" style={{ margin: 24 }}>加载中...</div> : items.length === 0 ? <div className="empty" style={{ margin: 24 }}>暂无分析记录</div> : <div style={{ overflowX: 'auto' }}><table className="table"><thead><tr><th>股票</th><th>评级</th><th className="num">置信度</th><th>分析日期</th><th>操作</th></tr></thead><tbody>{items.map((item) => <tr key={item.id}><td>{item.stock_name} <span className="muted mono">{item.stock_code}</span></td><td><span className={RATING_BADGE[item.rating] || 'badge'}>{item.rating || '-'}</span></td><td className="num mono">{item.confidence}%</td><td className="muted">{item.created_at?.slice(0, 10)}</td><td><button className="btn-text" onClick={() => viewDetail(item.id)}>查看详情</button></td></tr>)}</tbody></table></div>}</section>
}

function ExportPdfButton({ reportId, stockCode }: { reportId: number; stockCode: string }) {
  const [downloading, setDownloading] = useState(false)
  const download = async () => {
    setDownloading(true)
    try { const response = await client.get(`/stocks/user/results/${reportId}/pdf`, { responseType: 'blob' }); const url = URL.createObjectURL(response.data); const anchor = document.createElement('a'); anchor.href = url; anchor.download = `${stockCode}_report_${reportId}.pdf`; anchor.click(); URL.revokeObjectURL(url) } finally { setDownloading(false) }
  }
  return <button className="btn btn-ghost" onClick={download} disabled={downloading}>{downloading ? '生成中...' : '导出PDF'}</button>
}

function StockOverview({ stockName, stockCode, info, warnings = [], analyzedAt }: { stockName: string; stockCode: string; info: StockInfo; warnings?: string[]; analyzedAt?: string }) {
  const pct = info.change_pct
  const fmt = (value: unknown) => value == null || value === '' ? 'N/A' : String(value)
  return <section className="card analysis-stock-overview">
    <div className="between wrap" style={{ marginBottom: 16 }}><h2 className="card-title" style={{ margin: 0 }}>{stockName} <span className="muted mono" style={{ fontSize: 16 }}>{stockCode}</span></h2>{pct != null && <span className={`badge ${pct >= 0 ? 'up' : 'down'}`}>{pct >= 0 ? '+' : ''}{pct}%</span>}</div>
    <div className="grid3"><Kpi label="最新价" value={fmt(info.price)} /><Kpi label="涨跌幅" value={`${fmt(pct)}%`} tone={pct != null && pct >= 0 ? 'up' : 'down'} /><Kpi label="PE(TTM)" value={fmt(info.pe_ttm)} /><Kpi label="PB" value={fmt(info.pb)} /><Kpi label="市值(亿)" value={fmt(info.market_cap)} /><Kpi label="行业" value={fmt(info.industry)} /></div>
    {warnings.map((warning) => <p className="caption mt8" key={warning}>{warning}</p>)}
    {analyzedAt && <p className="caption mt16">分析时间 {analyzedAt}</p>}
  </section>
}

function TechnicalIndicatorSection({ indicators }: { indicators?: IndicatorSet }) {
  return <section className="card"><h2 className="card-title">技术指标</h2><div className="mini-grid"><IndicatorCard title="MA" data={indicators?.ma} keys={['MA5', 'MA20', 'MA60']} /><IndicatorCard title="MACD" data={indicators?.macd} keys={['DIF', 'DEA', 'MACD']} /><IndicatorCard title="RSI(14)" data={indicators?.rsi} keys={['RSI']} /><IndicatorCard title="KDJ" data={indicators?.kdj} keys={['K', 'D', 'J']} /><IndicatorCard title="BOLL" data={indicators?.boll} keys={['UP', 'MID', 'LOW']} labels={['上轨', '中轨', '下轨']} /></div><p className="caption mt16">基于近 60 个交易日计算</p></section>
}

function ReportView({ report, onBack }: { report: ReportData; onBack?: () => void }) {
  const d = report.decision || {}
  const analysts = report.analysts || {}
  return <div className="panel-stack">
    {onBack && <button className="btn-text" style={{ alignSelf: 'start' }} onClick={onBack}>← 返回列表</button>}
    <StockOverview stockName={report.stock_name} stockCode={report.stock_code} info={report.stock_info} warnings={report.data_warnings} analyzedAt={report.analyzed_at} />
    {report.kline && report.kline.length > 0 ? <KlineChart rows={report.kline} /> : <section className="card"><h2 className="card-title">K线与成交量</h2><div className="empty">历史报告未保存 K 线数据</div></section>}
    <TechnicalIndicatorSection indicators={report.indicators} />
    <section className="card"><h2 className="card-title">六位 AI 分析师</h2><div className="mini-grid analyst-report-grid">{ANALYST_ORDER.filter((key) => analysts[key]).map((key) => <article className="mini-card analyst-card" key={key}><div className="between wrap"><h3>{ANALYST_LABELS[key]}</h3>{analysts[key].score != null && <span className="fg2 small">评分：<span className="mono">{textValue(analysts[key].score)}</span></span>}</div><AnalystResultView analystKey={key} result={analysts[key]} /></article>)}</div>{Object.keys(analysts).length === 0 && <div className="empty">暂无已验证的分析师结果</div>}</section>
    <section className="card card-accent"><h2 className="card-title">AI 投研会议 · 最终决策</h2>{d.meeting_summary && <p className="small fg2" style={{ background: 'var(--surface)', padding: 12, borderRadius: 'var(--r-card)' }}>{d.meeting_summary}</p>}<div className="kpi-grid mt16"><Kpi label="最终决策" value={d.rating || '-'} badge={RATING_BADGE[d.rating || ''] || 'badge'} /><Kpi label="目标价" value={`¥${formatValue(d.target_price)}`} /><Kpi label="止损价" value={`¥${formatValue(d.stop_loss)}`} /><Kpi label="置信度" value={`${formatValue(d.confidence)}%`} /></div><ul className="kv-list mt16"><li><span className="k">入场区间</span><span>{formatValue(d.entry_range)}</span></li><li><span className="k">止盈目标</span><span>{formatValue(d.take_profit)}</span></li><li><span className="k">持有期限</span><span>{formatValue(d.holding_period)}</span></li><li><span className="k">仓位建议</span><span>{formatValue(d.position_size)}</span></li><li><span className="k">风险提示</span><span style={{ textAlign: 'right' }}>{formatValue(d.risk_warning)}</span></li></ul>{d.key_watchpoints && d.key_watchpoints.length > 0 && <div className="mt16"><span className="section-label">关键观察指标</span><ul className="obs-list">{d.key_watchpoints.map((point, index) => <li key={index}>{point}</li>)}</ul></div>}<div className="between wrap mt24"><p className="caption" style={{ margin: 0 }}>{report.disclaimer || '本分析仅供参考，不构成任何投资建议。'}</p>{report._id && <ExportPdfButton reportId={report._id} stockCode={report.stock_code} />}</div></section>
  </div>
}

function Kpi({ label, value, tone, badge }: { label: string; value: string; tone?: string; badge?: string }) {
  return <div className="kpi"><div className="k-label">{label}</div><div className={`k-value mono ${tone || ''}`} style={{ fontSize: 24 }}>{badge ? <span className={badge}>{value}</span> : value}</div></div>
}

function KlineChart({ rows }: { rows: KlineRow[] }) {
  const categories = rows.map((row) => row.date)
  const candle = rows.map((row) => [row.open, row.close, row.low, row.high])
  const volumes = rows.map((row) => row.volume)
  const movingAverage = (period: number) => rows.map((_row, index) => index + 1 < period ? null : rows.slice(index + 1 - period, index + 1).reduce((sum, item) => sum + item.close, 0) / period)
  const option = {
    animation: false,
    tooltip: { trigger: 'axis' },
    axisPointer: { link: [{ xAxisIndex: 'all' }] },
    grid: [{ left: 48, right: 18, top: 22, height: '57%' }, { left: 48, right: 18, top: '73%', height: '17%' }],
    xAxis: [{ type: 'category', data: categories, boundaryGap: true, axisLabel: { hideOverlap: true } }, { type: 'category', gridIndex: 1, data: categories, axisLabel: { show: false } }],
    yAxis: [{ scale: true }, { gridIndex: 1, scale: true, splitNumber: 2 }],
    series: [
      { name: 'K线', type: 'candlestick', data: candle, itemStyle: { color: '#c9453d', color0: '#26745a', borderColor: '#c9453d', borderColor0: '#26745a' } },
      { name: 'MA5', type: 'line', data: movingAverage(5), smooth: true, showSymbol: false, lineStyle: { width: 1 } },
      { name: 'MA20', type: 'line', data: movingAverage(20), smooth: true, showSymbol: false, lineStyle: { width: 1 } },
      { name: '成交量', type: 'bar', xAxisIndex: 1, yAxisIndex: 1, data: volumes, itemStyle: { color: '#9aa3ad' } },
    ],
  }
  return <section className="card analysis-chart"><h2 className="card-title">K线与成交量</h2><ReactECharts option={option} notMerge style={{ width: '100%', height: 360 }} /><p className="caption mt8">近 {rows.length} 个交易日 · MA5 / MA20</p></section>
}

function IndicatorCard({ title, data, keys, labels }: { title: string; data?: Record<string, number | null>; keys: string[]; labels?: string[] }) {
  return <div className="mini-card"><h3>{title}</h3><ul className="kv-list">{keys.map((key, index) => <li key={key}><span className="k">{labels?.[index] || key}</span><span className="mono">{data?.[key] != null ? data[key] : 'N/A'}</span></li>)}</ul></div>
}
