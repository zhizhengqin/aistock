import { useMemo, useState } from 'react'
import ReactECharts from 'echarts-for-react'
import type { HomeMarketData } from './useHomeMarketData'
import type { MarketCloudNode, RepresentativeStock } from './types'
import { formatAmount, formatSignedPct, signedClass } from './format'

interface Props {
  market: HomeMarketData
}

type Level = 'board' | 'stock'
type SelectedNode = MarketCloudNode | RepresentativeStock | null

function nodeColor(change: number | null | undefined): string {
  if (change === null || change === undefined) return '#b7b1a3'
  const amount = Math.min(Math.abs(change) / 8, 1)
  return change >= 0 ? `rgba(211, 63, 35, ${0.3 + amount * 0.65})` : `rgba(38, 137, 92, ${0.3 + amount * 0.65})`
}

function positive(value: number | null | undefined): number | null {
  return value !== null && value !== undefined && Number.isFinite(value) && value > 0 ? value : null
}

function boardArea(node: MarketCloudNode): { value: number; basis: string } {
  const value = Number.isFinite(node.value) && node.value > 0 ? node.value : 1
  if (positive(node.market_cap) !== null) return { value, basis: '市值' }
  return { value, basis: value > 1 ? '成交额' : '等权' }
}

function stockArea(node: RepresentativeStock): { value: number; basis: string } {
  const marketCap = positive(node.market_cap)
  if (marketCap !== null) return { value: marketCap, basis: '市值' }
  const turnover = positive(node.turnover)
  if (turnover !== null) return { value: turnover, basis: '成交额' }
  return { value: 1, basis: '等权' }
}

function tooltipText(params: any): string {
  const data = Array.isArray(params) ? params[0]?.data : params?.data
  if (!data) return ''
  const name = String(data.name || '')
  const change = formatSignedPct(data.change_pct)
  const basis = String(data.area_basis || '面积')
  const value = Number(data.area_value ?? data.value)
  return `${name}\n涨跌幅：${change}\n${basis}：${formatAmount(value)}`
}

export default function MarketTreemap({ market }: Props) {
  const [level, setLevel] = useState<Level>('board')
  const [selectedNode, setSelectedNode] = useState<SelectedNode>(null)

  const boardNodes = market.cloud.data
  const stockNodes = market.constituents.data
  const displayNodes = level === 'board' ? boardNodes : stockNodes
  const chartData = useMemo(() => displayNodes.map((node: MarketCloudNode | RepresentativeStock) => {
    const board = level === 'board' ? node as MarketCloudNode : null
    const stock = level === 'stock' ? node as RepresentativeStock : null
    const area = board ? boardArea(board) : stockArea(stock as RepresentativeStock)
    return {
      name: node.name,
      code: board?.code || stock?.code,
      value: area.value,
      change_pct: board?.change_pct ?? stock?.change_pct,
      market_cap: board?.market_cap ?? stock?.market_cap,
      turnover: stock?.turnover,
      area_basis: area.basis,
      area_value: area.value,
      itemStyle: { color: nodeColor(board?.change_pct ?? stock?.change_pct) },
    }
  }), [displayNodes, level])

  const option = {
    animation: false,
    tooltip: { show: true, trigger: 'item', confine: true, renderMode: 'richText', formatter: tooltipText },
    series: [{
      type: 'treemap',
      roam: false,
      nodeClick: false,
      breadcrumb: { show: false },
      label: {
        show: true,
        overflow: 'truncate',
        ellipsis: '…',
        lineHeight: 16,
        color: '#201515',
        fontWeight: 600,
        textBorderColor: '#fffefb',
        textBorderWidth: 2,
        formatter: (params: any) => `${String(params?.data?.name || '')}\n${formatSignedPct(params?.data?.change_pct)}`,
      },
      upperLabel: { show: false },
      data: chartData,
    }],
  }

  const onChartClick = (params: any) => {
    const data = params?.data
    if (!data) return
    if (level === 'board') {
      const board = boardNodes.find((item) => item.code === data.code)
      if (!board) return
      setSelectedNode(board)
      setLevel('stock')
      market.selectBoard({ kind: board.kind, board_code: board.code, board_name: board.name, trade_date: board.trade_date })
    } else {
      const stock = stockNodes.find((item) => item.code === data.code)
      if (stock) setSelectedNode(stock)
    }
  }

  const selectDisplayNode = (code: string) => onChartClick({ data: { code } })

  const returnToBoards = () => {
    setLevel('board')
    setSelectedNode(null)
  }

  const selectedMeta = selectedNode || (level === 'board' ? boardNodes[0] : stockNodes[0]) || null
  const selectedChange = selectedMeta && 'change_pct' in selectedMeta ? selectedMeta.change_pct : null
  const selectedArea = selectedMeta
    ? 'kind' in selectedMeta
      ? boardArea(selectedMeta as MarketCloudNode)
      : stockArea(selectedMeta as RepresentativeStock)
    : null
  const quickLabel = level === 'stock' ? '快速定位个股' : market.cloudKind === 'industry' ? '快速定位行业' : '快速定位题材'

  return (
    <section className="card market-treemap" aria-label="大盘云图">
      <div className="between wrap">
        <div>
          <h2 className="card-title" style={{ margin: 0 }}>大盘云图</h2>
          <p className="caption mt8">面积优先按市值，缺失时按成交额；颜色代表涨跌幅；点击板块可下钻到代表个股</p>
        </div>
        <div className="flex" style={{ gap: 8 }}>
          <button type="button" className={`pill${market.cloudKind === 'industry' ? ' active' : ''}`} aria-pressed={market.cloudKind === 'industry'} onClick={() => { setLevel('board'); setSelectedNode(null); market.setCloudKind('industry') }}>行业</button>
          <button type="button" className={`pill${market.cloudKind === 'theme' ? ' active' : ''}`} aria-pressed={market.cloudKind === 'theme'} onClick={() => { setLevel('board'); setSelectedNode(null); market.setCloudKind('theme') }}>题材</button>
          {level === 'stock' && <button type="button" className="btn-text" onClick={returnToBoards}>返回板块云图</button>}
        </div>
      </div>
      {market.cloud.meta?.freshness === 'stale' && <p className="caption hotspot-meta">最近有效数据：{market.cloud.meta.provider || '历史快照'} · 交易日 {market.cloud.meta.trade_date || '暂无'}</p>}
      {market.cloud.error && <div className="status-banner datahub-error mt16" role="alert">{market.cloud.error}</div>}
      {level === 'stock' && market.constituents.error && <div className="status-banner datahub-error mt16" role="alert">{market.constituents.error}</div>}
      {((level === 'board' && market.cloud.loading && boardNodes.length === 0) || (level === 'stock' && market.constituents.loading && stockNodes.length === 0)) && <div className="empty treemap-empty">正在加载云图...</div>}
      {displayNodes.length > 0 && <ReactECharts option={option} onEvents={{ click: onChartClick }} style={{ height: '460px', width: '100%' }} opts={{ renderer: 'svg' }} />}
      {displayNodes.length > 0 && <div className="treemap-node-picker">
        <select
          className="treemap-node-select"
          aria-label={quickLabel}
          value=""
          onChange={(event) => {
            if (event.target.value) selectDisplayNode(event.target.value)
          }}
        >
          <option value="">{quickLabel}</option>
          {displayNodes.map((node) => <option key={node.code} value={node.code}>{node.name} {formatSignedPct(node.change_pct)}</option>)}
        </select>
      </div>}
      {displayNodes.length === 0 && !market.cloud.loading && !market.cloud.error && <div className="empty treemap-empty">暂无可用云图数据</div>}
      <div className="treemap-detail" aria-live="polite">
        {selectedMeta ? (
          <>
            <strong>{selectedMeta.name}</strong><span className="muted mono">{'code' in selectedMeta ? selectedMeta.code : ''}</span>
            <span className={`mono ${signedClass(selectedChange)}`}>{formatSignedPct(selectedChange)}</span>
            <span>{selectedArea?.basis || '面积'}：<span>{formatAmount(selectedArea?.value)}</span></span>
          </>
        ) : <span className="muted">点击云图节点查看固定详情</span>}
      </div>
    </section>
  )
}
