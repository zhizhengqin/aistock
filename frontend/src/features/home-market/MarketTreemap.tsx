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

export default function MarketTreemap({ market }: Props) {
  const [level, setLevel] = useState<Level>('board')
  const [selectedNode, setSelectedNode] = useState<SelectedNode>(null)

  const boardNodes = market.cloud.data
  const stockNodes = market.constituents.data
  const displayNodes = level === 'board' ? boardNodes : stockNodes
  const chartData = useMemo(() => displayNodes.map((node: MarketCloudNode | RepresentativeStock) => {
    const board = level === 'board' ? node as MarketCloudNode : null
    const stock = level === 'stock' ? node as RepresentativeStock : null
    const rawValue = board ? board.market_cap : stock?.market_cap
    const displayValue = rawValue != null && rawValue > 0 ? rawValue : 1
    return {
      name: node.name,
      code: board?.code || stock?.code,
      value: displayValue,
      change_pct: board?.change_pct ?? stock?.change_pct,
      market_cap: rawValue,
      itemStyle: { color: nodeColor(board?.change_pct ?? stock?.change_pct) },
    }
  }), [displayNodes, level])

  const option = {
    animation: false,
    tooltip: { show: false },
    series: [{
      type: 'treemap',
      roam: false,
      nodeClick: false,
      breadcrumb: { show: false },
      label: { show: true, formatter: (params: any) => (params?.data?.value > 2 ? params.data.name : '') },
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
  const selectedMarketCap = selectedMeta && 'market_cap' in selectedMeta ? selectedMeta.market_cap : null

  return (
    <section className="card market-treemap" aria-label="大盘云图">
      <div className="between wrap">
        <div>
          <h2 className="card-title" style={{ margin: 0 }}>大盘云图</h2>
          <p className="caption mt8">面积代表市值规模，颜色代表涨跌幅；点击板块可下钻到代表个股</p>
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
      {displayNodes.length > 0 && <div className="treemap-node-list" aria-label="云图节点列表">
        {displayNodes.map((node) => <button type="button" className="treemap-node-button" key={node.code} onClick={() => selectDisplayNode(node.code)}>{node.name}<span className="muted mono">{formatSignedPct(node.change_pct)}</span></button>)}
      </div>}
      {displayNodes.length === 0 && !market.cloud.loading && !market.cloud.error && <div className="empty treemap-empty">暂无可用云图数据</div>}
      <div className="treemap-detail" aria-live="polite">
        {selectedMeta ? (
          <>
            <strong>{selectedMeta.name}</strong><span className="muted mono">{'code' in selectedMeta ? selectedMeta.code : ''}</span>
            <span className={`mono ${signedClass(selectedChange)}`}>{formatSignedPct(selectedChange)}</span>
            <span>市值：<span>{formatAmount(selectedMarketCap)}</span></span>
          </>
        ) : <span className="muted">点击云图节点查看固定详情</span>}
      </div>
    </section>
  )
}
